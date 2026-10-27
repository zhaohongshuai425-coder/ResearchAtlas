"""Observable handoff/receiver regressions on wholly synthetic evidence."""
from pathlib import Path
from unittest.mock import patch
import json,shutil,subprocess,sys,tempfile,unittest,zipfile
RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
import core,research_flow as flow,literature_tools as lt,local_ingest as intake,receiver_check as receiver
import handoff_dependencies as deps,evidence_decisions as ed
from round5_fixtures import GraphFixture


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='Round6 handoff ');self.root=Path(self.tmp.name).resolve()
    def tearDown(self):self.tmp.cleanup()
    def library(self,body='Battery capacity degradation is measured under declared cycling conditions, with source limitations preserved for review.',nested=False,empty=False):
        project=self.root/'project';flow.initialize(project,'Synthetic cross-domain project','round6')
        folder=project/'nested' if nested else project;folder.mkdir(exist_ok=True)
        library=folder/'custom_registry.json';lt.initialize(library,'Synthetic direction','round6')
        if not empty:
            pdf=self.root/'fixture.pdf';pdf.write_bytes(b'%PDF-1.4\nSynthetic binding, not an actual PDF')
            meta=self.root/'meta.json';core.save(meta,{pdf.name:{'title':'Synthetic subject','doi':'10.9999/round6'}})
            parsed=([{'page':1,'width':100,'height':100,'blocks':[{'text':body,'bbox':None}]}],{'backend':'synthetic','version':'fixture'})
            with patch.object(lt,'pdf_blocks',return_value=parsed):intake.ingest(library,[pdf],metadata=meta,index=True)
            lt.select_document(library,lt.load_library(library)['documents'][0]['id'],'included','Synthetic regression')
        state=project/'research_state.json'
        if nested:
            snapshot=folder/'unconventional_snapshot.json';data=lt.load_library(library);core.save(snapshot,data)
            bindings=[{'path':snapshot.name,'sha256':core.sha(snapshot)},*lt.library_dependencies(data)]
            for i,b in enumerate(bindings):flow.register_artifact(state,folder/b['path'],'other','F'+str(i),'Explicit nested dependency')
        else:
            snapshot=Path(lt.bind_to_flow(library,state)['snapshot'])
        package=self.root/'package.zip';flow.pack(state,package,True)
        dest=self.root/'different machine folder';flow.restore(package,dest)
        return dest,snapshot.relative_to(project).as_posix()
    def cli(self,folder,*args):
        return subprocess.run([sys.executable,'-B','-X','utf8',str(folder/'tools/receiver_check.py'),
            '--folder',str(folder),*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=30,cwd=folder)


class H01ReceiverCompatibility(Base):
    def test_standard_snapshot_roundtrip_has_replayed_evidence(self):
        dest,snapshot=self.library();report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertTrue(report['evidence_replay_passed'])
        cases=[c for c in report['smoke'] if c['case']=='original_lexical_query_and_replay']
        self.assertEqual(cases[0]['source'],snapshot);self.assertIn('battery',cases[0]['question'])
        self.assertFalse(report['scientific_truth_certified']);self.assertFalse(report['physical_other_computer_confirmed'])
    def test_unrelated_subject_uses_its_own_text(self):
        dest,_=self.library('Transformer insulation degradation is observed during electrical monitoring under recorded humidity and controlled loading conditions.')
        report=receiver.check(dest);self.assertTrue(report['core_passed'],report)
        self.assertIn('transformer',report['smoke'][0]['question']);self.assertTrue(report['evidence_replay_passed'])
    def test_explicit_query_and_snapshot_override(self):
        dest,snapshot=self.library();report=receiver.check(dest,'cycling',snapshot)
        self.assertTrue(report['core_passed'],report);self.assertEqual(report['smoke'][0]['question_origin'],'explicit_parameter')
    def test_no_match_reports_failure_via_cli_and_return_zip(self):
        dest,_=self.library();target=self.root/'no-match.json';p=self.cli(dest,'--query','unfindablexyz','--output',target)
        self.assertEqual(p.returncode,1,p.stderr);out=json.loads(p.stdout)
        self.assertFalse(out['core_passed']);self.assertTrue(target.is_file());self.assertNotIn('Traceback',p.stderr)
        with zipfile.ZipFile(target.with_suffix('.zip')) as z:self.assertEqual(json.loads(z.read(target.name)),core.load(target))
    def test_legacy_manifest_still_works_without_subject_assumption(self):
        dest,_=self.library();manifest=core.load(dest/'package_manifest.json');(dest/'package_manifest.json').unlink()
        core.save(dest/'receiver_manifest.json',{'files':manifest['files']})
        report=receiver.check(dest);self.assertTrue(report['core_passed'],report);self.assertEqual(report['package_kind'],'legacy_receiver')
    def test_missing_manifest_still_produces_structured_report(self):
        dest,_=self.library();(dest/'package_manifest.json').unlink();target=self.root/'missing.json'
        p=self.cli(dest,'--output',target);self.assertEqual(p.returncode,1,p.stderr)
        self.assertFalse(core.load(target)['core_passed']);self.assertTrue(core.load(target)['manifest_problems']);self.assertNotIn('Traceback',p.stderr)
    def test_changed_payload_fails_before_smoke(self):
        dest,snapshot=self.library();(dest/'project'/snapshot).write_text('{}',encoding='utf-8')
        report=receiver.check(dest);self.assertFalse(report['core_passed']);self.assertEqual(report['smoke'],[])
    def test_metadata_only_packet_cannot_certify_evidence(self):
        dest,_=self.library();project=self.root/'project';archive=self.root/'metadata.zip'
        flow.pack(project/'research_state.json',archive);folder=self.root/'metadata';flow.restore(archive,folder)
        report=receiver.check(folder);self.assertFalse(report['core_passed']);self.assertFalse(report['evidence_replay_passed'])
    def test_empty_library_original_check_is_explicitly_skipped(self):
        dest,_=self.library(empty=True);report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertFalse(report['evidence_replay_passed'])
        self.assertEqual(report['smoke'][0]['status'],'skipped_no_indexed_originals');self.assertIsNone(report['smoke'][0]['passed'])
    def test_default_receiver_does_not_load_semantic_models_or_mutate_payload(self):
        dest,_=self.library();before={p.relative_to(dest).as_posix():core.sha(p) for p in dest.rglob('*') if p.is_file()}
        with patch('semantic_retrieval.Encoder',side_effect=AssertionError('Unrequested model load')):
            report=receiver.check(dest)
        self.assertTrue(report['core_passed']);self.assertEqual(report['semantic_check']['status'],'not_requested')
        self.assertEqual(before,{p.relative_to(dest).as_posix():core.sha(p) for p in dest.rglob('*') if p.is_file()})
    def test_nested_registry_and_relative_original_paths_survive(self):
        dest,snapshot=self.library(nested=True);report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertEqual(report['smoke'][0]['source'],snapshot)
    def test_manifest_path_escape_is_reported_without_read(self):
        dest,_=self.library();manifest=core.load(dest/'package_manifest.json');manifest['files']['../secret.json']='0'*64;core.save(dest/'package_manifest.json',manifest)
        report=receiver.check(dest);self.assertFalse(report['core_passed']);self.assertIn('Unsafe package member',report['manifest_problems'][0]['error'])
    def semantic_fixture(self):
        dest,snapshot=self.library()
        for path in ('models/e5/model_lock.json','models/translation/model_lock.json'):
            file=dest/path;file.parent.mkdir(parents=True);core.save(file,{'synthetic':'no actual weights'})
        path=dest/'semantic/manifest.json';path.parent.mkdir();core.save(path,{'source':{'path':'project/'+snapshot}})
        return dest
    def test_explicit_optional_model_failure_does_not_mislabel_manifest_or_core(self):
        dest=self.semantic_fixture()
        with patch.object(receiver.util,'find_spec',return_value=object()),patch.object(receiver.metadata,'version',return_value='synthetic'),patch('semantic_retrieval.Encoder',side_effect=RuntimeError('Synthetic model failure')):
            report=receiver.check(dest,semantic=True)
        self.assertTrue(report['core_passed'],report);self.assertFalse(report['semantic_ready'])
        self.assertEqual(report['manifest_problems'],[]);self.assertEqual(report['semantic_check']['status'],'failed')
    def test_legacy_semantic_root_is_respected_with_synthetic_model_stub(self):
        import evidence_search as es
        dest=self.semantic_fixture();original=es.query;seen=[]
        def wrapped(backend,source,question,root,**kwargs):
            if 'semantic_index' in kwargs:
                seen.append(root)
                for k in ('semantic_index','encoder','translator'):kwargs.pop(k)
            return original(backend,source,question,root,**kwargs)
        with patch.object(receiver.util,'find_spec',return_value=object()),patch.object(receiver.metadata,'version',return_value='synthetic'),patch('semantic_retrieval.Encoder',return_value=object()),patch('query_translation.Translator',return_value=object()),patch.object(es,'query',side_effect=wrapped):
            report=receiver.check(dest,semantic=True)
        self.assertTrue(report['core_passed'],report);self.assertTrue(report['semantic_ready'],report)
        self.assertEqual(seen,[dest]);self.assertFalse(report['scientific_truth_certified'])


class H02DependencyClosure(Base):
    def fixture(self):
        f=GraphFixture();self.addCleanup(f.close);result=f.publish();root=f.root
        flow.initialize(root,'Synthetic graph transfer','graph-round6',existing=True);state=root/'research_state.json'
        flow.register_artifact(state,f.output,'graph','G','Synthetic committed graph')
        flow.register_artifact(state,result['receipt']['path'],'audit','R','Synthetic commit receipt')
        graph=core.load(f.output);receipt=core.load(root/result['receipt']['path'])
        paths={b['local_path'] for b in graph['sources']}|{b['path'] for b in receipt['review_input_versions'].values()}
        for i,path in enumerate(sorted(paths)):flow.register_artifact(state,path,'other','D'+str(i),'Explicit direct graph dependency')
        return f,state,receipt
    def test_direct_bindings_reveal_indirect_index_cache_and_metadata(self):
        f,state,_=self.fixture();report=flow.check_state(state)
        self.assertTrue(report['passed']);self.assertFalse(report['handoff_ready'])
        self.assertIn('derived/index.sqlite',report['dependencies']['unregistered_dependencies'])
        self.assertIn('cache.gz',report['dependencies']['unregistered_dependencies'])
    def test_incomplete_full_pack_is_rejected_before_output_or_state_change(self):
        f,state,_=self.fixture();before=state.read_bytes();package=self.root/'incomplete.zip'
        with self.assertRaises(deps.IncompleteHandoff) as caught:flow.pack(state,package,True)
        self.assertFalse(caught.exception.report['passed']);self.assertFalse(package.exists());self.assertEqual(before,state.read_bytes())
    def test_metadata_export_preserves_dependency_diagnostics(self):
        f,state,_=self.fixture();package=self.root/'meta.zip';result=flow.pack(state,package)
        self.assertTrue(result['passed']);self.assertFalse(result['evidence_dependencies_complete'])
        with zipfile.ZipFile(package) as z:manifest=json.loads(z.read('package_manifest.json'))
        self.assertTrue(manifest['metadata_only']);self.assertFalse(manifest['dependency_check']['passed'])
        self.assertIn('derived/index.sqlite',manifest['dependency_check']['unregistered_dependencies'])
    def test_explicit_dependency_registration_restores_full_claim_replay(self):
        f,state,receipt=self.fixture();missing=flow.check_state(state)['dependencies']['unregistered_dependencies']
        for i,path in enumerate(missing):flow.register_artifact(state,path,'other','I'+str(i),'Explicit transitive dependency')
        self.assertTrue(flow.check_state(state)['handoff_ready']);package=self.root/'complete.zip';flow.pack(state,package,True)
        dest=self.root/'received';flow.restore(package,dest);bindings=receipt['review_input_versions'];root=dest/'project'
        primary=next(b['path'] for k,b in bindings.items() if k.startswith('primary_'))
        independent=next(b['path'] for k,b in bindings.items() if k.startswith('independent_'))
        audit=ed.audit(root/primary,root,True,root/independent)
        self.assertTrue(audit['can_export_assertion']);self.assertFalse(audit['scientific_truth_certified'])
        report=receiver.check(dest);self.assertTrue(report['core_passed'],report);self.assertTrue(report['evidence_replay_passed'])
    def test_missing_indirect_index_invalidates_state(self):
        f,state,_=self.fixture();(f.root/'derived/index.sqlite').unlink();report=flow.check_state(state)
        self.assertFalse(report['passed']);self.assertTrue(any(i.get('path')=='derived/index.sqlite' and i['status']=='missing' for i in report['dependencies']['issues']))
    def test_changed_indirect_cache_is_detected(self):
        f,state,_=self.fixture();(f.root/'cache.gz').write_bytes(b'changed');report=flow.check_state(state)
        self.assertFalse(report['passed']);self.assertTrue(any(i.get('path')=='cache.gz' and i['status']=='changed' for i in report['dependencies']['issues']))
    def test_external_dependency_is_not_followed(self):
        project=self.root/'safe';flow.initialize(project,'Synthetic','safe');path=project/'graph.json'
        core.save(path,{'schema':'research-kb/1.0','sources':[{'local_path':'../outside.json','sha256':'0'*64}]})
        state=project/'research_state.json';flow.register_artifact(state,path,'graph','G','Synthetic unsafe binding')
        report=flow.check_state(state);self.assertFalse(report['passed'])
        self.assertTrue(any(i['status']=='outside_or_invalid_root' for i in report['dependencies']['issues']))
    def test_opaque_user_metadata_does_not_trigger_arbitrary_path_crawls(self):
        project=self.root/'safe';flow.initialize(project,'Synthetic','safe');path=project/'other.json'
        core.save(path,{'path':'../private.txt','sha256':'0'*64});state=project/'research_state.json'
        flow.register_artifact(state,path,'other','O','Opaque metadata, not an evidence protocol')
        report=flow.check_state(state);self.assertTrue(report['passed']);self.assertTrue(report['handoff_ready'])
        self.assertEqual(report['dependencies']['opaque_json_documents'],['other.json'])


if __name__=='__main__':unittest.main()
