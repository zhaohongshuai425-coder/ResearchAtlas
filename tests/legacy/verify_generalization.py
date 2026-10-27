from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

import json

import sys

import unittest

from unittest.mock import patch

from verify_hardening import HardeningTests

import core

import evidence_search as es

import evidence_quality as eq

import research_flow as flow

class GeneralizationTests(HardeningTests):
    def unified(self,**kw):
        return es.query('corpus',self.manifest,'graph',self.root,**kw)

    def test_shared_fields_do_not_promote_metadata(self):
        result=self.unified()
        self.assertEqual(result['schema'],es.SCHEMA)
        self.assertFalse(result['answer_generated'])
        self.assertTrue(all(c['support_status']=='candidate_requires_source_review' for c in result['candidates']))
        self.assertTrue(any(c['kind']=='metadata' for c in result['candidates']))
        self.assertTrue(all(c['scientific_confidence'] is None for c in result['candidates']))

    def test_original_only_filters_missing_and_inherited(self):
        result=es.query('corpus',self.manifest,'NEW1',self.root,original_only=True)
        self.assertFalse(result['candidates'])
        self.assertGreater(result['excluded']['not_original_text'],0)

    def test_explicit_concept_alternatives_and_constraints(self):
        result=self.unified(concepts=[['Graph','图'],['anomaly','不存在词']],require_all_concepts=True)
        self.assertTrue(result['candidates'])
        self.assertTrue(all(c['kind']=='pdf_page' for c in result['candidates']))
        absent=self.unified(concepts=[['Graph'],['definitely_unavailable']],require_all_concepts=True)
        self.assertFalse(absent['candidates'])

    def test_no_silent_alias_ignoring(self):
        with self.assertRaises(ValueError):es.query('library',self.meta,'graph',self.root,alias='OLD1')
        result=self.unified(alias='NEW1')
        self.assertFalse(result['candidates'])

    def test_candidate_replay_then_changed_source_rejected(self):
        result=self.unified(original_only=True)
        file=self.root/'query.json';core.save(file,result)
        candidate=result['candidates'][0]
        self.assertTrue(es.resolve(file,candidate['candidate_id'],self.root)['replayed'])
        self.original.write_bytes(b'changed original')
        with self.assertRaises(ValueError):es.resolve(file,candidate['candidate_id'],self.root)

    def test_unified_root_escape_rejected(self):
        with self.assertRaises(ValueError):es.query('corpus',self.root/'../escape.json','test',self.root)

    def test_graph_entities_not_returned_as_original_pages(self):
        graph=core.load(self.graph_fixture()[0])
        graph['nodes'][1]['label']='Graph anomaly mechanism'
        file=self.root/'graph_search.json';core.save(file,graph)
        result=es.query('graph',file,'相关机制',self.root,concepts=[['Graph','图'],['anomaly']],require_all_concepts=True)
        self.assertTrue(result['candidates'])
        self.assertEqual(result['candidates'][0]['kind'],'graph_entity')
        self.assertFalse(es.query('graph',file,'graph',self.root,original_only=True)['candidates'])

    def state_file(self):
        folder=self.root/'new_project';flow.initialize(folder,'A new research direction','independent')
        return folder/'research_state.json'

    def test_route_has_no_implicit_training_or_completion(self):
        file=self.state_file()
        before=core.load(file)
        route=flow.configure_route(file,['theory','synthesis'],'Appropriate evidence form','Proof assumptions and scoped synthesis')
        after=core.load(file)
        self.assertEqual(before['stages'],after['stages'])
        self.assertEqual(route['modes'],['theory','synthesis'])
        self.assertEqual(flow.check_state(file)['validation_route'],route)
        self.assertNotIn('HI-Small',json.dumps(after))

    def test_route_change_flags_completed_downstream(self):
        file=self.state_file();folder=file.parent
        flow.configure_route(file,['theory'],'Initial','Proof')
        report=folder/'result.md';report.write_text('Fixture argument',encoding='utf-8')
        flow.register_artifact(file,report,'result','R1','Scoped proof record')
        flow.set_stage(file,'execution','complete','Recorded fixture only',['R1'])
        flow.configure_route(file,['measurement'],'Evidence requirement changed','Measured quantities with uncertainty')
        self.assertEqual(core.load(file)['stages']['execution']['status'],'needs_review')
        self.assertTrue(list((folder/'.research_history').glob('*.json')))

    def test_route_rejects_empty_and_preserves_old_states(self):
        file=self.state_file()
        self.assertTrue(flow.check_state(file)['passed'])
        with self.assertRaises(ValueError):flow.configure_route(file,[],'reason','evidence')
        with self.assertRaises(ValueError):flow.configure_route(file,['train_test_default'],'reason','evidence')
        flow.configure_route(file,['other'],'User-defined verification','Explicit independent check')
        revision=core.load(file)['revision']
        flow.configure_route(file,['other'],'User-defined verification','Explicit independent check')
        self.assertEqual(core.load(file)['revision'],revision)

    def test_incomplete_reading_card_is_not_reviewed(self):
        file=self.root/'reading.json'
        core.save(file,{'schema':'research-scoped-reading/1.0','reviewer':'fixture','reviews':[{'id':'R'}]})
        with self.assertRaises(ValueError):eq.checked_reviews(file,self.root)

    def test_metadata_cannot_be_scoped_original_evidence(self):
        result=es.query('corpus',self.manifest,'NEW1',self.root)
        query=self.root/'metadata_query.json';core.save(query,result)
        review={'id':'R','decision':'supports','conditions':['Fixture only'],'claim':'Fixture claim','reading_scope':'Metadata only',
                'rationale':'Fixture','reading_fields':{key:{'status':'unknown','text':'Not examined'} for key in eq.READING_FIELDS},
                'query':{'path':query.name,'sha256':core.sha(query)},'candidate_ids':[result['candidates'][0]['candidate_id']]}
        file=self.root/'reading.json';core.save(file,{'schema':'research-scoped-reading/1.0','reviewer':'fixture','reviews':[review]})
        with self.assertRaises(ValueError):eq.checked_reviews(file,self.root)

    def scoped_fixture(self,decision='supports'):
        result=self.unified(original_only=True)
        query=self.root/'source_query.json';core.save(query,result)
        inspection=self.root/'page.json';core.save(inspection,{'source_sha256':self.sha,'pages':[{'page':1,'quality':{'gate':'candidate_text_only'}}]})
        preview=self.root/'page.png';preview.write_bytes(b'synthetic preview fixture, not a real scientific page')
        review={'id':'R','decision':decision,'conditions':['Synthetic fixture only'],'claim':'Scoped claim','reading_scope':'Physical page 1',
                'rationale':'Synthetic source binding check only',
                'reading_fields':{key:{'status':'recorded','text':'Synthetic scoped interpretation'} for key in eq.READING_FIELDS},
                'query':{'path':query.name,'sha256':core.sha(query)},'candidate_ids':[result['candidates'][0]['candidate_id']],
                'inspection':{'path':inspection.name,'sha256':core.sha(inspection)},
                'visual_checks':[{'path':preview.name,'sha256':core.sha(preview),'page':1,'source_sha256':self.sha,'status':'visually_checked'}]}
        file=self.root/'scoped.json';core.save(file,{'schema':'research-scoped-reading/1.0','reviewer':'synthetic fixture','reviews':[review]})
        return file

    def test_scoped_graph_preserves_original_and_adds_conditional_chain(self):
        graph=self.graph_fixture()[0];before=graph.read_bytes();review=self.scoped_fixture()
        out=self.root/'new_graph.json'
        self.assertTrue(eq.add_to_graph(graph,review,self.root,out)['passed'])
        self.assertEqual(before,graph.read_bytes())
        result=core.load(out)
        self.assertTrue(any(e['relation']=='supports' for e in result['edges']))
        self.assertFalse(any(n['state'].get('adoption')=='adopted' for n in result['nodes']))

    def test_insufficient_evidence_does_not_create_support_relation(self):
        graph=self.graph_fixture()[0];review=self.scoped_fixture('insufficient');out=self.root/'insufficient.json'
        eq.add_to_graph(graph,review,self.root,out)
        self.assertFalse(any(e['relation'] in {'supports','challenges'} for e in core.load(out)['edges']))

    def test_unknown_mechanism_not_created_as_known_method(self):
        graph=self.graph_fixture()[0];review=self.scoped_fixture();data=core.load(review)
        data['reviews'][0]['reading_fields']['mechanism']={'status':'unknown','text':'Mechanism not examined'};core.save(review,data)
        out=self.root/'unknown.json';eq.add_to_graph(graph,review,self.root,out)
        self.assertFalse(any(n['id']=='test::R-method' for n in core.load(out)['nodes']))

    def test_wrong_preview_source_or_changed_inspection_rejected(self):
        review=self.scoped_fixture();data=core.load(review)
        data['reviews'][0]['visual_checks'][0]['source_sha256']='wrong';core.save(review,data)
        with self.assertRaises(ValueError):eq.checked_reviews(review,self.root)
        data['reviews'][0]['visual_checks'][0]['source_sha256']=self.sha;core.save(review,data)
        (self.root/'page.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):eq.checked_reviews(review,self.root)

    def test_scoped_graph_rejects_overwrite(self):
        graph=self.graph_fixture()[0];review=self.scoped_fixture()
        with self.assertRaises(FileExistsError):eq.add_to_graph(graph,review,self.root,graph)
