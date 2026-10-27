"""Meaningful regressions for the four 1.2.3 review findings; synthetic only."""
from pathlib import Path
from unittest.mock import patch
import copy,hashlib,importlib.util,json,subprocess,sys,types,unittest,zipfile
from test_round3 import Base
from postfix_fixtures import corpus,binding

RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
import core,literature_tools as lt,local_ingest as intake,research_flow as flow,corpus_tools as ct,evidence_search as es,semantic_retrieval as sr
import source_snapshot as snapshots
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
ins=module('round4_installer',RELEASE/'install.py');builder=module('round4_builder',RELEASE/'build_release.py')

def parser(path,backend='auto'):
    return [{'page':1,'width':100,'height':100,'blocks':[{'text':'Synthetic needle evidence with specific methodological conditions and limitations.','bbox':None}]}],{'backend':'synthetic','version':'fixture'}


class R01PlaceholderTitles(Base):
    def setup_document(self):
        library=self.library();pdf=self.pdf('random_scan.pdf');meta=self.root/'doi-only.json'
        core.save(meta,{pdf.name:{'doi':'10.9999/round3'}});intake.ingest(library,[pdf],metadata=meta)
        return library,lt.load_library(library)['documents'][0]
    def test_doi_citation_fills_unverified_placeholder_and_index_can_run(self):
        library,before=self.setup_document();lt.import_records(library,self.records_file())
        doc=lt.load_library(library)['documents'][0]
        self.assertEqual(doc['id'],before['id']);self.assertFalse(doc['metadata_conflict'])
        self.assertEqual(doc['metadata']['title'],'Synthetic study');self.assertNotEqual(doc['metadata']['title_status'],'verified')
        self.assertEqual(doc['reading_status'],'not_reviewed')
        self.assertTrue(any(v['record']['title']=='random_scan' for v in doc['metadata_variants']))
        with patch.object(lt,'pdf_blocks',side_effect=parser):result=lt.index_pdf(library,doc['id'])
        self.assertTrue(lt.load_library(library)['documents'][0]['active_index'])
    def test_verified_identity_is_not_rewritten_even_with_placeholder_flag(self):
        library,doc=self.setup_document();data=lt.load_library(library);data['documents'][0]['identity_status']='verified';flow.commit(library,data)
        lt.import_records(library,self.records_file());after=lt.load_library(library)['documents'][0]
        self.assertEqual(after['metadata']['title'],doc['metadata']['title']);self.assertTrue(after['metadata_conflict'])
    def test_real_reported_title_conflict_still_blocks_index(self):
        library,doc=self.setup_document();data=lt.load_library(library)
        data['documents'][0]['metadata'].update(title='Actual reported different title',title_status='user_reported');flow.commit(library,data)
        lt.import_records(library,self.records_file());self.assertTrue(lt.load_library(library)['documents'][0]['metadata_conflict'])
        with self.assertRaisesRegex(ValueError,'Resolve document identity'):lt.index_pdf(library,doc['id'])
    def test_placeholder_fill_does_not_clear_an_existing_conflict(self):
        library,doc=self.setup_document();data=lt.load_library(library);data['documents'][0]['metadata_conflict']=True;flow.commit(library,data)
        lt.import_records(library,self.records_file());after=lt.load_library(library)['documents'][0]
        self.assertEqual(after['metadata']['title'],'Synthetic study');self.assertTrue(after['metadata_conflict'])


class R02BuildCompletion(Base):
    def release(self,metadata=True,report=None):
        root=self.root/'release';(root/ins.NAME).mkdir(parents=True);(root/ins.NAME/'SKILL.md').write_text('Synthetic skill')
        if metadata:core.save(root/'release_metadata.json',{'name':ins.NAME,'version':'1'})
        if report is not None:core.save(root/'build_report.json',{'version':'1',**report})
        files={p.relative_to(root).as_posix():core.sha(p) for p in root.rglob('*') if p.is_file()}
        core.save(root/'manifest.json',{'schema':'research-skill-release/1.0','version':'1','files':files});return root
    def test_missing_build_report_is_not_an_installable_release(self):
        release=self.release();parent=self.root/'skills'
        with patch.object(ins,'ROOT',release),self.assertRaisesRegex(ValueError,'Incomplete build'):ins.install(parent)
        self.assertFalse(parent.exists())
    def test_verifying_report_is_only_eligible_for_content_checks(self):
        release=self.release(report={'passed':True,'completion_state':'verifying','installable':False})
        self.assertTrue(ins.verify(release,require_complete=False)['files'])
        with self.assertRaisesRegex(ValueError,'Incomplete build'):ins.verify(release)
    def test_complete_report_allows_installation(self):
        release=self.release(report={'passed':True,'completion_state':'complete','installable':True})
        with patch.object(ins,'ROOT',release):result=ins.install(self.root/'skills')
        self.assertTrue(result['passed'])
    def test_older_complete_report_without_new_fields_remains_compatible(self):
        release=self.release(report={'passed':True})
        self.assertTrue(ins.verify(release)['files'])
    def test_legacy_minimal_manifest_is_not_reclassified_as_an_unfinished_build(self):
        release=self.release(metadata=False)
        self.assertTrue(ins.verify(release)['files'])
    def test_builder_keeps_installation_blocked_until_verification_completes(self):
        output=self.root/'build';original=builder.call;phases=[]
        def check_phase(command,cwd):
            if any(Path(str(v)).name=='run_tests.py' for v in command):
                with self.assertRaisesRegex(ValueError,'Incomplete build'):ins.verify(output)
                phases.append('pre_tests_blocked')
                return types.SimpleNamespace(stdout=json.dumps({'passed':True,'tests_run':1,'failures':0,'errors':0}))
            if '--verify-content-only' in command:
                with self.assertRaisesRegex(ValueError,'Incomplete build'):ins.verify(output)
                phases.append('verification_blocked')
            if '--verify-only' in command:
                self.assertTrue(ins.verify(output)['files']);phases.append('completed')
            return original(command,cwd)
        with patch.object(builder,'call',side_effect=check_phase):result=builder.build(output,test=True)
        self.assertTrue(result['installable']);self.assertEqual(phases,['pre_tests_blocked','verification_blocked','completed'])


class R03QuerySnapshot(Base):
    def source(self):return corpus(self.root/'corpus','Synthetic needle evidence with source conditions and limitations for this controlled test.')
    def update(self,manifest):
        root=manifest.parent;m=core.load(manifest);catalog_path=root/m['catalog']['path'];catalog=core.load(catalog_path)
        catalog['documents'][0]['title']='Updated source title';core.save(catalog_path,catalog);m['catalog']=binding(root,catalog_path);core.save(manifest,m)
        built=ct.build_search(manifest,root/'derived/updated.sqlite');m['search_index']=binding(root,Path(built['path']));core.save(manifest,m)
    def test_update_after_search_keeps_old_candidate_snapshot_replayable(self):
        manifest=self.source();before=core.load(manifest);before_sha=core.sha(manifest);original=ct.search
        def changed(*args,**kwargs):
            result=original(*args,**kwargs);self.update(manifest);return result
        with patch.object(ct,'search',side_effect=changed):q=es.query('corpus',manifest,'needle',manifest.parent,original_only=True)
        self.assertEqual(q['source_snapshot'],before);self.assertEqual(q['source']['sha256'],before_sha)
        path=manifest.parent/'query.json';core.save(path,q);replay=es.resolve(path,q['candidates'][0]['candidate_id'],manifest.parent)
        self.assertTrue(replay['replayed']);self.assertTrue(replay['source_registry_changed'])
    def test_update_before_reading_frozen_catalog_fails_instead_of_mixing_versions(self):
        manifest=self.source();original=ct.search
        def changed(*args,**kwargs):self.update(manifest);return original(*args,**kwargs)
        with patch.object(ct,'search',side_effect=changed),self.assertRaisesRegex(ValueError,'catalog missing/changed'):
            es.query('corpus',manifest,'needle',manifest.parent,original_only=True)
    def test_library_retrieval_consumes_the_same_captured_registry(self):
        library=self.library();pdf=self.pdf();intake.ingest(library,[pdf],metadata=self.meta(pdf));identity=lt.load_library(library)['documents'][0]['id']
        with patch.object(lt,'pdf_blocks',side_effect=parser):lt.index_pdf(library,identity)
        lt.select_document(library,identity,'included','Synthetic only');before_sha=core.sha(library);original=lt.retrieve
        def changed(*args,**kwargs):
            data=lt.load_library(library);data['documents'][0]['metadata']['title']='Updated after snapshot';flow.commit(library,data)
            return original(*args,**kwargs)
        with patch.object(lt,'retrieve',side_effect=changed):q=es.query('library',library,'needle',library.parent,original_only=True)
        self.assertEqual(q['source']['sha256'],before_sha);self.assertEqual(q['candidates'][0]['title'],'Synthetic study')
        path=library.parent/'query.json';core.save(path,q);self.assertTrue(es.resolve(path,q['candidates'][0]['candidate_id'],library.parent)['replayed'])
    def test_different_semantic_and_lexical_snapshots_are_not_fused(self):
        manifest=self.source();original=ct.search;semantic=manifest.parent/'semantic.json'
        def changed(*args,**kwargs):
            result=original(*args,**kwargs);self.update(manifest)
            core.save(semantic,{'backend':'corpus','source':{'path':manifest.name,'sha256':core.sha(manifest)}});return result
        fake={'hits':[],'source_snapshot':{},'pool_may_be_truncated':False}
        with patch.object(ct,'search',side_effect=changed),patch.object(sr,'search',return_value=fake),self.assertRaisesRegex(ValueError,'Lexical and semantic'):
            es.query('corpus',manifest,'needle',manifest.parent,original_only=True,semantic_index=semantic)
    def test_handoff_includes_new_helper_and_runs_without_the_original_directory(self):
        library=self.library();pdf=self.pdf();intake.ingest(library,[pdf],metadata=self.meta(pdf));identity=lt.load_library(library)['documents'][0]['id']
        with patch.object(lt,'pdf_blocks',side_effect=parser):lt.index_pdf(library,identity)
        lt.select_document(library,identity,'included','Synthetic only');bound=lt.bind_to_flow(library,library.parent/'research_state.json')
        archive=self.root/'handoff.zip';flow.pack(library.parent/'research_state.json',archive,True)
        restored=self.root/'receiver';flow.restore(archive,restored)
        self.assertTrue((restored/'tools/source_snapshot.py').is_file())
        command=[sys.executable,'-B','-X','utf8',str(restored/'tools/evidence_search.py'),'query','--backend','library',
            '--source',str(restored/'project'/Path(bound['snapshot']).name),'--question','needle','--root',str(restored/'project'),
            '--original-only','--output',str(restored/'project/query.json')]
        result=subprocess.run(command,cwd=restored,capture_output=True,text=True,encoding='utf-8',timeout=25)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertGreater(json.loads(result.stdout)['candidates'],0)


class R04RestoreArchiveSnapshot(Base):
    def packages(self):
        a=self.package();a_bytes=a.read_bytes();project=self.root/'second'
        flow.initialize(project,'Synthetic B','B');(project/'a.txt').write_text('Different B')
        flow.register_artifact(project/'research_state.json','a.txt','other','A','Synthetic only')
        b=self.root/'b.zip';flow.pack(project/'research_state.json',b,True);return a,a_bytes,b.read_bytes()
    def test_archive_a_b_a_source_updates_cannot_replace_the_verified_snapshot(self):
        source,a,b=self.packages();verify=flow.verify_package;copier=flow.shutil.copyfileobj;updates=[]
        with zipfile.ZipFile(source) as z:last=z.infolist()[-1].filename
        def changed(path):
            result=verify(path);self.assertNotEqual(Path(path),source);source.write_bytes(b);updates.append('B');return result
        def revert(incoming,target,*args,**kwargs):
            result=copier(incoming,target,*args,**kwargs)
            if Path(target.name).as_posix().endswith('/'+last):source.write_bytes(a);updates.append('A')
            return result
        with patch.object(flow,'verify_package',side_effect=changed),patch.object(flow.shutil,'copyfileobj',side_effect=revert):
            result=flow.restore(source,self.root/'restored')
        self.assertEqual(updates,['B','A']);self.assertEqual((self.root/'restored/project/a.txt').read_text(),'Original a.txt')
        self.assertEqual(result['input_snapshot_sha256'],hashlib.sha256(a).hexdigest());self.assertTrue(result['passed'])
    def test_later_source_change_does_not_change_the_captured_version(self):
        source,a,b=self.packages();verify=flow.verify_package
        def changed(path):result=verify(path);source.write_bytes(b);return result
        with patch.object(flow,'verify_package',side_effect=changed):result=flow.restore(source,self.root/'restored')
        self.assertEqual(source.read_bytes(),b);self.assertTrue(result['passed'])
        self.assertEqual((self.root/'restored/project/a.txt').read_text(),'Original a.txt')
    def test_private_snapshot_corruption_is_detected_before_publication(self):
        source,a,b=self.packages();verify=flow.verify_package
        def corrupted(path):result=verify(path);Path(path).write_bytes(b);return result
        with patch.object(flow,'verify_package',side_effect=corrupted),self.assertRaisesRegex(ValueError,'Private archive snapshot changed'):
            flow.restore(source,self.root/'restored')
        self.assertFalse((self.root/'restored').exists())
    def test_snapshot_cleanup_failure_does_not_reverse_a_committed_restore(self):
        source=self.package();original=Path.unlink
        def cleanup(path,*args,**kwargs):
            if path.name=='archive.snapshot':raise OSError('Injected snapshot cleanup failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',cleanup):result=flow.restore(source,self.root/'restored')
        self.assertTrue(result['passed']);self.assertTrue(result['cleanup_pending']);self.assertTrue(result['cleanup_warnings'])
        self.assertTrue((self.root/'restored/project/a.txt').is_file())
    def test_normal_restore_removes_its_temporary_snapshot(self):
        source=self.package();result=flow.restore(source,self.root/'restored')
        self.assertFalse(result['cleanup_pending']);self.assertEqual(list(self.root.glob('.research-source-*')),[])


if __name__=='__main__':unittest.main()
