from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

import copy

import gzip

import json

import os

import sys

import tempfile

import unittest

from unittest.mock import patch

import core

import corpus_tools as ct

import pdf_quality as pq

import pdf_ocr

import review_workbench as rw

class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.derived=self.root/'derived';self.derived.mkdir()
        self.meta=self.root/'registered.json';core.save(self.meta,{'registered':True})
        self.original=self.root/'source.bin';self.original.write_bytes(b'original fixture source bytes')
        self.cache=self.root/'cache.gz';self.sha=core.sha(self.original)
        self.cache.write_bytes(gzip.compress(json.dumps({'sha256':self.sha,'pages':[{'pdf_page':1,'text':'Graph anomaly detection evidence source with sufficient readable context for review.'},
                         {'pdf_page':2,'text':'�'*60+' graph evidence'}]}).encode()))
        self.metab={'path':'registered.json','sha256':core.sha(self.meta),'locator':'fixture metadata'}
        doc={'id':'P1','alias':'OLD1','batch':'historical','title':'Graph method','reading':{'status':'inherited_only'},
             'metadata_binding':self.metab,'search_metadata':{'alias':'OLD1','title':'Graph method'},'expected_script':'unknown',
             'reports':[{'id':'REP1','availability':'verified_present','path':'source.bin','sha256':self.sha,
                         'cache':{'path':'cache.gz','sha256':core.sha(self.cache)}}]}
        absent={'id':'P2','alias':'NEW1','batch':'added','title':'Missing original','reading':{'status':'missing_original'},
                'metadata_binding':self.metab,'search_metadata':{'alias':'NEW1','title':'Missing original'},'reports':[]}
        self.catalog=self.derived/'catalog.json';core.save(self.catalog,{'documents':[doc,absent]})
        self.manifest=self.derived/'manifest.json'
        m={'schema':ct.SCHEMA,'instance_id':'test','workspace_root':'..','derived_root':'derived',
           'catalog':{'path':'derived/catalog.json','sha256':core.sha(self.catalog)},'metadata_bindings':[self.metab],'scope':'fixture'}
        core.save(self.manifest,m)
        index=ct.build_search(self.manifest,self.derived/'index.sqlite')
        m['search_index']={'path':'derived/index.sqlite','sha256':index['sha256']};core.save(self.manifest,m)

    def tearDown(self):self.temp.cleanup()

    def test_all_batches_and_missing_original_stay_searchable_as_metadata(self):
        hit=ct.search(self.manifest,'NEW1')['hits'][0]
        self.assertEqual(hit['kind'],'metadata');self.assertEqual(hit['reading']['status'],'missing_original')
        self.assertEqual(ct.search(self.manifest,'graph')['registered_documents'],2)

    def test_flagged_text_held_unless_explicit(self):
        normal=ct.search(self.manifest,'evidence',limit=100)
        self.assertEqual(normal['held_flagged_matches'],1)
        self.assertNotIn(2,[h['page'] for h in normal['hits']])
        raw=ct.search(self.manifest,'evidence',limit=100,include_flagged=True)
        self.assertIn(2,[h['page'] for h in raw['hits']])
        self.assertFalse(raw['answer_generated'])

    def test_stale_original_text_withheld(self):
        self.original.write_bytes(b'changed original')
        result=ct.search(self.manifest,'evidence')
        self.assertFalse(result['hits']);self.assertTrue(result['stale_matches_withheld'])

    def test_changed_cache_withheld(self):
        self.cache.write_bytes(b'changed cache')
        result=ct.search(self.manifest,'evidence')
        self.assertFalse(result['hits']);self.assertTrue(result['stale_matches_withheld'])

    def test_changed_formal_metadata_requires_new_projection(self):
        self.meta.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):ct.search(self.manifest,'graph')

    def test_changed_index_rejected(self):
        with (self.derived/'index.sqlite').open('ab') as stream:stream.write(b'tampered')
        with self.assertRaises(ValueError):ct.search(self.manifest,'graph')

    def test_catalog_root_escape_rejected(self):
        m=core.load(self.manifest);m['catalog']['path']='../outside.json';core.save(self.manifest,m)
        with self.assertRaises(ValueError):ct.read_manifest(self.manifest)

    def test_index_output_cannot_overwrite_or_escape(self):
        with self.assertRaises(FileExistsError):ct.build_search(self.manifest,self.derived/'index.sqlite')
        with self.assertRaises(ValueError):ct.build_search(self.manifest,self.root/'outside.sqlite')

    def test_cache_binding_is_content_based_not_alias_based(self):
        data={'sha256':'incorrect','pages':[]};self.cache.write_bytes(gzip.compress(json.dumps(data).encode()))
        with self.assertRaises(ValueError):ct.read_cache(self.root,{'path':'cache.gz','sha256':core.sha(self.cache)},self.sha)

    def test_expected_script_gate_does_not_ban_legitimate_unknown_language(self):
        text='\u0f60'*80
        self.assertEqual(pq.text_quality(text,'cjk')['gate'],'hold_text_for_review')
        self.assertEqual(pq.text_quality(text,'unknown')['gate'],'candidate_text_only')
        self.assertEqual(pq.text_quality('这是正常的中文材料。'*20,'cjk')['gate'],'candidate_text_only')

    def test_empty_private_and_replacement_text_flags(self):
        self.assertEqual(pq.text_quality('')['gate'],'hold_text_for_review')
        self.assertIn('replacement_characters',pq.text_quality('�'*40)['flags'])
        self.assertIn('private_use_mapping',pq.text_quality('\ue000'*40)['flags'])

    def graph_fixture(self):
        state={a:'unknown' for a in core.AXES};refs=[{'source_id':'test::s','locator':'fixture row'}]
        graph={'schema':core.SCHEMA_VERSION,'instance_id':'test','sources':[{'id':'test::s','local_path':'registered.json','sha256':core.sha(self.meta)}],
               'nodes':[{'id':'test::P1','local_id':'P1','type':'document','label':'Paper','state':state.copy(),'source_refs':refs},
                        {'id':'test::M1','local_id':'M1','type':'method','label':'Method','state':state.copy(),'source_refs':refs}],
               'edges':[{'id':'test::d','from':'test::P1','to':'test::M1','relation':'describes','source_refs':refs}]}
        file=self.root/'graph.json';core.save(file,graph)
        inspection=self.root/'inspect.json';core.save(inspection,{'source_sha256':self.sha,'document_id':'P1',
                   'pages':[{'page':1,'quality':{'gate':'candidate_text_only'}}]})
        review={'schema':'research-method-review/1.0','base_graph_sha256':core.sha(file),'reviews':[{'id':'REV',
            'method_id':'test::M1','document_id':'test::P1','conditions':['Only under declared context'],
            'question':'What does it require?','claim':'A bounded source claim','decision':'Retain for evidence review only',
            'decision_kind':'evidence_scope_only','reading_scope':'Physical page 1 only','evidence':[{'path':'source.bin','sha256':self.sha,'page':1,'section':'fixture section',
            'inspection':{'path':'inspect.json','sha256':core.sha(inspection)}}]}]}
        reviewfile=self.root/'review.json';core.save(reviewfile,review)
        return file,reviewfile,review,graph

    def test_queue_preserves_unknowns_without_inventing_decision(self):
        file,_,_,_=self.graph_fixture();record=rw.queue(file,self.root)['methods'][0]
        self.assertIsNone(record['inherited_limits']);self.assertEqual(record['decision'],'not_reassessed_this_queue')

    def test_review_adds_chains_and_preserves_original_states(self):
        graphfile,reviewfile,review,before=self.graph_fixture();out=self.root/'new.json'
        result=rw.apply_reviews(graphfile,reviewfile,self.root,out);self.assertTrue(result['passed'])
        value=core.load(out);self.assertEqual(value['nodes'][:2],before['nodes']);self.assertEqual(result['added_nodes'],5)
        self.assertEqual(core.load(graphfile),before)
        with self.assertRaises(FileExistsError):rw.apply_reviews(graphfile,reviewfile,self.root,out)

    def test_wrong_method_document_binding_rejected(self):
        graphfile,reviewfile,review,_=self.graph_fixture();review['reviews'][0]['document_id']='test::other';core.save(reviewfile,review)
        with self.assertRaises(ValueError):rw.apply_reviews(graphfile,reviewfile,self.root,self.root/'new.json')

    def test_claim_cannot_turn_into_model_adoption(self):
        graphfile,reviewfile,review,_=self.graph_fixture();review['reviews'][0]['decision_kind']='replace_model';core.save(reviewfile,review)
        with self.assertRaises(ValueError):rw.apply_reviews(graphfile,reviewfile,self.root,self.root/'new.json')

    def test_wrong_page_or_source_hash_rejected(self):
        for key,value in [('page',2),('sha256','incorrect')]:
            graphfile,reviewfile,review,_=self.graph_fixture();review['reviews'][0]['evidence'][0][key]=value;core.save(reviewfile,review)
            with self.assertRaises(ValueError):rw.apply_reviews(graphfile,reviewfile,self.root,self.root/'new.json')

    def test_challenged_inference_is_not_adopted(self):
        graphfile,reviewfile,review,_=self.graph_fixture();review['reviews'][0]['relation']='challenges';core.save(reviewfile,review)
        rw.apply_reviews(graphfile,reviewfile,self.root,self.root/'new.json')
        value=core.load(self.root/'new.json');claim=next(n for n in value['nodes'] if n['type']=='claim')
        self.assertEqual(claim['state']['adoption'],'not_adopted')
        self.assertTrue(any(e['relation']=='challenges' for e in value['edges']))

    def test_ocr_explicit_pages_existing_output_and_absent_backend(self):
        out=self.root/'ocr.json';out.write_text('preserved')
        with self.assertRaises(FileExistsError):pdf_ocr.recognize(self.original,[1],out)
        with self.assertRaises(ValueError):pdf_ocr.recognize(self.original,[],self.root/'new.json')
        with patch.object(pdf_ocr.shutil,'which',return_value=None):
            with self.assertRaises(ValueError):pdf_ocr.recognize(self.original,[1],self.root/'new.json')
