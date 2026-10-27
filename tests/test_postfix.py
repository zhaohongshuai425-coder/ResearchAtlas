from pathlib import Path
from unittest.mock import patch
import ast,hashlib,importlib.util,json,os,shutil,sys,tempfile,types,unittest,zipfile

RELEASE=Path(__file__).resolve().parents[1];TOOLS=RELEASE/'research-evidence-workflow/scripts/tools';sys.path.insert(0,str(TOOLS))
import core,literature_tools as lt,local_ingest as intake,corpus_tools as ct,evidence_search as es,semantic_retrieval as sr,runtime_check,prototype_acceptance
from postfix_fixtures import corpus,FakeEncoder,FakeNumpy

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
ins=module('postfix_installer',RELEASE/'install.py');builder=module('postfix_builder',RELEASE/'build_release.py')

class Fixture(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='证据修复 ');self.root=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()
    def library(self):
        path=self.root/'project/literature.json';lt.initialize(path,'Synthetic','postfix');return path
    def pdf(self,name='a.pdf',body=b'A'):
        path=self.root/name;path.write_bytes(b'%PDF-1.4\nSynthetic original '+body);return path
    def metadata(self,value):p=self.root/'metadata.json';core.save(p,value);return p
    def pages(self,path,backend='auto'):
        text='Synthetic extracted evidence for '+path.read_bytes().decode('ascii')
        return [{'page':1,'width':100,'height':100,'blocks':[{'text':text,'bbox':None}]}],{'backend':'synthetic','version':'1'}
    def release(self,name,version):
        root=self.root/name;(root/ins.NAME).mkdir(parents=True);file=root/ins.NAME/'SKILL.md';file.write_text('payload '+version)
        core.save(root/'manifest.json',{'schema':'research-skill-release/1.0','version':version,'files':{ins.NAME+'/SKILL.md':core.sha(file)}})
        return root
    def pending_upgrade(self):
        one=self.release('one','1');two=self.release('two','2');parent=self.root/'skills'
        with patch.object(ins,'ROOT',one):ins.install(parent)
        atomic=ins.atomic_json
        def fail(path,value):
            if Path(path).name=='current.json' and value.get('version')=='2':raise OSError('ledger interrupted')
            return atomic(path,value)
        with patch.object(ins,'ROOT',two),patch.object(ins,'atomic_json',fail),self.assertRaises(OSError):ins.install(parent,upgrade=True)
        return parent,one,two

class IdentitySafety(Fixture):
    def test_conflicting_candidate_dois_cannot_switch_verified_original(self):
        library=self.library();a=self.pdf();meta=self.metadata({a.name:{'title':'Verified A'}})
        with patch.object(lt,'pdf_blocks',side_effect=self.pages):intake.ingest(library,[a],metadata=meta,index=True)
        data=lt.load_library(library);doc=data['documents'][0];doc.update(identity_status='verified');doc['metadata']['title_status']='verified';old_index=doc['active_index'];core.save(library,data)
        for doi in ('10.5555/candidate-a','10.5555/candidate-b'):
            meta=self.metadata({a.name:{'doi':doi}});intake.ingest(library,[a],metadata=meta)
        b=self.pdf('b.pdf',b'UNRELATED B');meta=self.metadata({b.name:{'title':'Unrelated B','doi':'10.5555/candidate-b'}})
        with patch.object(lt,'pdf_blocks',side_effect=self.pages):intake.ingest(library,[b],metadata=meta,index=True)
        docs=lt.load_library(library)['documents'];self.assertEqual(len(docs),2)
        self.assertEqual(docs[0]['active_index'],old_index);self.assertEqual(len(docs[0]['fulltexts']),1)
        self.assertTrue(docs[0]['metadata_conflict']);self.assertEqual(docs[0]['identity_status'],'verified')
        self.assertIn(docs[0]['id'],docs[1]['possible_identity_matches'])

    def test_title_conflict_is_held_without_append_or_active_index_switch(self):
        library=self.library();a=self.pdf();meta=self.metadata({a.name:{'title':'Study A','doi':'10.5555/same'}})
        with patch.object(lt,'pdf_blocks',side_effect=self.pages):intake.ingest(library,[a],metadata=meta,index=True)
        old=lt.load_library(library)['documents'][0]['active_index'];b=self.pdf('b.pdf',b'B');meta=self.metadata({b.name:{'title':'Study B','doi':'10.5555/same'}})
        with patch.object(lt,'pdf_blocks',side_effect=self.pages) as parser:result=intake.ingest(library,[b],metadata=meta,index=True)
        self.assertFalse(result['passed']);self.assertEqual(result['items'][0]['status'],'needs_identity_review');parser.assert_not_called()
        data=lt.load_library(library);self.assertEqual(len(data['documents'][0]['fulltexts']),1);self.assertEqual(data['documents'][0]['active_index'],old)
        self.assertTrue(data['intake_reviews']);self.assertTrue((library.parent/data['intake_reviews'][0]['fulltext']['path']).is_file())

    def test_explicit_nonconflicting_document_id_can_link_a_pending_version(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);identity=lt.load_library(library)['documents'][0]['id']
        b=self.pdf('b.pdf',b'B');meta=self.metadata({b.name:{'document_id':identity,'version_note':'Explicit user mapping; still pending review'}})
        result=intake.ingest(library,[b],metadata=meta);self.assertTrue(result['passed'])
        doc=lt.load_library(library)['documents'][0];self.assertEqual(len(doc['fulltexts']),2);self.assertEqual(doc['fulltexts'][-1]['reading_status'],'not_reviewed')

    def test_identity_change_during_parsing_preserves_active_index(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);identity=lt.load_library(library)['documents'][0]['id']
        def change(path,backend):
            data=lt.load_library(library);data['documents'][0]['metadata_conflict']=True;core.save(library,data);return self.pages(path,backend)
        with patch.object(lt,'pdf_blocks',side_effect=change),self.assertRaisesRegex(ValueError,'changed during indexing'):lt.index_pdf(library,identity)
        self.assertIsNone(lt.load_library(library)['documents'][0]['active_index'])

    def test_direct_indexing_of_conflicted_document_is_blocked(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);data=lt.load_library(library);doc=data['documents'][0];doc['metadata_conflict']=True;core.save(library,data)
        with self.assertRaisesRegex(ValueError,'conflict'):lt.index_pdf(library,doc['id'])

    def test_verified_primary_doi_still_requires_explicit_new_original_mapping(self):
        library=self.library();a=self.pdf();meta=self.metadata({a.name:{'title':'Study A','doi':'10.5555/primary'}});intake.ingest(library,[a],metadata=meta)
        data=lt.load_library(library);data['documents'][0]['identity_status']='verified';identity=data['documents'][0]['id'];core.save(library,data)
        b=self.pdf('version.pdf',b'new version');fields={'title':'Study A','doi':'10.5555/primary'}
        result=intake.ingest(library,[b],metadata=self.metadata({b.name:fields}));self.assertFalse(result['passed'])
        self.assertEqual(len(lt.load_library(library)['documents'][0]['fulltexts']),1)
        result=intake.ingest(library,[b],metadata=self.metadata({b.name:{**fields,'document_id':identity}}));self.assertTrue(result['passed'])
        self.assertEqual(len(lt.load_library(library)['documents'][0]['fulltexts']),2)

class CurrentRequestRecovery(Fixture):
    def test_pending_upgrade_then_rollback_performs_the_rollback(self):
        parent,one,two=self.pending_upgrade();result=ins.rollback(parent)
        self.assertTrue(result['requested_action_completed']);self.assertEqual(ins.status(parent)['version'],'1')
        self.assertEqual(result['recovered_previous_transaction']['version'],'2')

    def test_pending_upgrade_then_new_target_installs_requested_version(self):
        parent,one,two=self.pending_upgrade();three=self.release('three','3')
        with patch.object(ins,'ROOT',three):result=ins.install(parent,upgrade=True)
        self.assertTrue(result['requested_action_completed']);self.assertEqual(ins.status(parent)['version'],'3')

    def test_retry_of_interrupted_rollback_does_not_undo_it(self):
        parent,one,two=self.pending_upgrade();ins.recover(parent);atomic=ins.atomic_json
        def fail(path,value):
            if Path(path).name=='current.json' and value.get('version')=='1':raise OSError('rollback ledger interrupted')
            return atomic(path,value)
        with patch.object(ins,'atomic_json',fail),self.assertRaises(OSError):ins.rollback(parent)
        result=ins.rollback(parent);self.assertTrue(result['resumed_same_rollback']);self.assertEqual(ins.status(parent)['version'],'1')

    def test_interrupted_fresh_install_can_resume_without_upgrade_flag(self):
        one=self.release('one','1');parent=self.root/'skills';atomic=ins.atomic_json
        def fail(path,value):
            if Path(path).name=='current.json':raise OSError('first ledger interrupted')
            return atomic(path,value)
        with patch.object(ins,'ROOT',one),patch.object(ins,'atomic_json',fail),self.assertRaises(OSError):ins.install(parent)
        with patch.object(ins,'ROOT',one):result=ins.install(parent)
        self.assertTrue(result['passed']);self.assertEqual(ins.status(parent)['backups'],[])

    def test_legacy_unknown_transaction_does_not_claim_a_rollback_was_done(self):
        parent,one,two=self.pending_upgrade();journal=ins.locations(parent)[4];plan=core.load(journal);plan.pop('action');core.save(journal,plan)
        result=ins.rollback(parent);self.assertFalse(result['passed']);self.assertFalse(result['requested_action_completed'])
        self.assertTrue(result['recovery_only']);self.assertEqual(ins.status(parent)['version'],'2')

class DurableIntakeHistory(Fixture):
    def test_retry_keeps_original_failure_and_immutable_run(self):
        library=self.library();a=self.root/'a.pdf';a.write_bytes(b'bad PDF');first=intake.ingest(library,[a]);before=Path(first['run_report']).read_bytes()
        a.write_bytes(b'%PDF-1.4\nFixed synthetic fixture');second=intake.ingest(library,[a])
        self.assertTrue(second['passed']);self.assertNotEqual(first['run_id'],second['run_id']);self.assertEqual(Path(first['run_report']).read_bytes(),before)
        self.assertEqual(core.load(first['run_report'])['failed'],1);self.assertEqual(core.load(second['report'])['run_id'],second['run_id'])
        self.assertTrue(any(r.get('success') is False for r in lt.load_library(library)['attempts']))

    def test_successful_retries_keep_separate_runs_without_registry_duplication(self):
        library=self.library();a=self.pdf();first=intake.ingest(library,[a]);before=library.read_bytes();second=intake.ingest(library,[a])
        self.assertEqual(library.read_bytes(),before);self.assertNotEqual(first['run_report'],second['run_report'])
        self.assertTrue(Path(first['run_report']).is_file());self.assertTrue(Path(second['run_report']).is_file())

    def test_legacy_latest_report_is_preserved_before_replacing_pointer(self):
        library=self.library();a=self.pdf();first=intake.ingest(library,[a]);legacy={'schema':'research-local-intake/1.0','failed':1,'error':'old failure'}
        core.save(first['report'],legacy);intake.ingest(library,[a]);folder=Path(first['run_report']).parent
        kept=list(folder.glob('legacy-*.json'));self.assertEqual(len(kept),1);self.assertEqual(core.load(kept[0]),legacy)

    def test_failed_attempt_is_kept_in_run_even_when_registry_lock_is_busy(self):
        import research_flow
        library=self.library();a=self.pdf()
        with patch.object(research_flow,'locked',side_effect=FileExistsError('busy registry')):result=intake.ingest(library,[a])
        self.assertFalse(result['passed']);saved=core.load(result['run_report']);self.assertFalse(saved['items'][0]['failure_attempt_recorded'])
        self.assertIn('busy registry',saved['items'][0]['attempt_registration_error'])

class MetadataValidation(Fixture):
    def test_invalid_new_title_does_not_break_subsequent_valid_import(self):
        library=self.library();a=self.pdf();meta=self.metadata({a.name:{'title':123}});result=intake.ingest(library,[a],metadata=meta)
        self.assertFalse(result['passed']);self.assertEqual(lt.load_library(library)['documents'],[])
        records=self.root/'records.json';core.save(records,[{'title':'Valid later record','doi':'10.5555/valid'}]);lt.import_records(library,records)
        self.assertEqual(lt.load_library(library)['documents'][0]['metadata']['title'],'Valid later record')

    def test_local_field_types_are_checked_on_new_and_duplicate_inputs(self):
        library=self.library();a=self.pdf()
        for fields in ({'title':None},{'venue':123},{'authors':[123]},{'document_id':12}):
            with self.subTest(fields=fields):
                result=intake.ingest(library,[a],metadata=self.metadata({a.name:fields}));self.assertFalse(result['passed'])
        self.assertEqual(lt.load_library(library)['documents'],[])

    def test_existing_bad_title_reports_identity_without_rewriting(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);data=lt.load_library(library);identity=data['documents'][0]['id'];data['documents'][0]['metadata']['title']=123;core.save(library,data);before=library.read_bytes()
        with self.assertRaisesRegex(ValueError,identity):lt.load_library(library)
        self.assertEqual(library.read_bytes(),before)

class OriginalPassages(Fixture):
    def test_unicode_expansion_keeps_nonempty_literal_excerpt(self):
        body=('oﬃce research evidence '*300)+'needleterm';manifest=corpus(self.root/'unicode',body)
        result=ct.search(manifest,'needleterm');text=result['hits'][0]['excerpt'];self.assertIn('needleterm',text);self.assertIn(text,body);self.assertLessEqual(len(text),1300)
        q=es.query('corpus','manifest.json','needleterm',manifest.parent,original_only=True);p=manifest.parent/'query.json';core.save(p,q)
        replay=es.resolve(p,q['candidates'][0]['candidate_id'],manifest.parent);self.assertTrue(replay['replayed']);self.assertFalse(replay['support_verified'])

    def test_empty_candidate_is_not_replayable(self):
        manifest=corpus(self.root/'empty','Long synthetic evidence on research methods with needleterm and sufficient original text.')
        q=es.query('corpus',manifest,'needleterm',manifest.parent,original_only=True);c=q['candidates'][0];c['text']='';c['native']['excerpt']=''
        p=manifest.parent/'empty.json';core.save(p,q)
        with self.assertRaisesRegex(ValueError,'Empty evidence'):es.resolve(p,c['candidate_id'],manifest.parent)

    def test_casefold_and_combining_sequences_remain_findable(self):
        for i,(original,query) in enumerate([('StraßeNeedle','strasseneedle'),('cafe\u0301needle','caféneedle'),('oﬃceneedle','officeneedle')]):
            body=('oﬃce research evidence '*100)+original
            manifest=corpus(self.root/str(i),body);hit=ct.search(manifest,query)['hits'][0]
            self.assertIn(query,ct.normal(hit['excerpt']));self.assertIn(hit['excerpt'],body)

class ProjectPaths(Fixture):
    def test_hash_percent_space_unicode_path_opens_exact_readonly_database(self):
        folder=self.root/'project#1 %中文';manifest=corpus(folder,'Synthetic research evidence with needleterm and enough original text for a normal lookup.')
        before={p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}
        self.assertTrue(ct.search(manifest,'needleterm')['hits']);self.assertEqual(before,{p.relative_to(self.root).as_posix() for p in self.root.rglob('*')})
        self.assertFalse((self.root/'project').exists())

    def test_missing_database_does_not_create_another_file(self):
        folder=self.root/'project#2';manifest=corpus(folder,'Synthetic research evidence with needleterm and enough original text for a normal lookup.')
        (folder/'derived/index.sqlite').unlink();before={p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}
        with self.assertRaises(FileNotFoundError):ct.search(manifest,'needleterm')
        self.assertEqual(before,{p.relative_to(self.root).as_posix() for p in self.root.rglob('*')})

    def test_semantic_relative_source_matches_absolute_source_from_unrelated_cwd(self):
        folder=self.root/'semantic';manifest=corpus(folder,'Synthetic research evidence with needleterm and enough original text for indexing.')
        work=self.root/'unrelated';work.mkdir();old=Path.cwd();os.chdir(work)
        try:
            with patch.dict(sys.modules,{'numpy':FakeNumpy}),patch.object(sr,'Encoder',FakeEncoder):
                relative=sr.build('corpus','manifest.json',folder,None,folder/'derived/relative')
                absolute=sr.build('corpus',manifest,folder,None,folder/'derived/absolute')
        finally:os.chdir(old)
        self.assertEqual(relative['source'],absolute['source']);self.assertEqual(relative['source_snapshot'],absolute['source_snapshot'])
        self.assertFalse((folder/'derived/relative/incomplete.json').exists())

class ReleaseConsistency(Fixture):
    def test_archive_output_change_blocks_publication(self):
        target=self.root/'output';original=zipfile.ZipFile.write
        def mutate(z,path,arcname=None,*args,**kwargs):
            path=Path(path)
            if path==target/'README.md':path.write_text('changed output after verification')
            return original(z,path,arcname,*args,**kwargs)
        with patch.object(builder,'SOURCE',RELEASE),patch.object(zipfile.ZipFile,'write',mutate),self.assertRaisesRegex(RuntimeError,'Archive not published'):builder.build(target,test=False,archive=True)
        self.assertFalse(Path(str(target)+'.zip').exists());self.assertFalse(core.load(target/'build_report.json')['passed'])

    def test_verified_archive_extracts_and_verifies(self):
        target=self.root/'good'
        with patch.object(builder,'SOURCE',RELEASE):result=builder.build(target,test=False,archive=True)
        self.assertTrue(result['archive_verified']);out=self.root/'extract'
        with zipfile.ZipFile(result['archive']) as z:z.extractall(out)
        self.assertEqual(ins.verify(out/target.name)['version'],core.load(RELEASE/'release_metadata.json')['version'])

    def test_manifest_and_release_versions_must_agree(self):
        root=self.release('one','1');meta=root/'release_metadata.json';core.save(meta,{'name':ins.NAME,'version':'1'})
        manifest=core.load(root/'manifest.json');manifest['files']['release_metadata.json']=core.sha(meta);manifest['version']='99';core.save(root/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'versions differ'):ins.verify(root)

    def test_failed_build_report_cannot_be_installed(self):
        root=self.release('one','1');report=root/'build_report.json';core.save(report,{'passed':False,'version':'1'})
        manifest=core.load(root/'manifest.json');manifest['files']['build_report.json']=core.sha(report);core.save(root/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'Build was not accepted'):ins.verify(root)

    def test_build_and_manifest_versions_must_agree(self):
        root=self.release('one','1');report=root/'build_report.json';core.save(report,{'passed':True,'version':'2'})
        manifest=core.load(root/'manifest.json');manifest['files']['build_report.json']=core.sha(report);core.save(root/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'Build and manifest'):ins.verify(root)

class MinimumEnvironment(Fixture):
    def test_hash_verification_does_not_need_python311_file_digest(self):
        p=self.root/'fixture.txt';p.write_text('Synthetic checksum fixture');core.save(self.root/'prototype_manifest.json',{'schema':'research-prototype-package/1.0','files':{'fixture.txt':core.sha(p)},'projects':[]})
        available={k:v for k,v in hashlib.__dict__.items() if k!='file_digest'}
        with patch.dict(hashlib.__dict__,available,clear=True):result=prototype_acceptance.run(self.root)
        self.assertEqual(result['integrity_problems'],[]);self.assertFalse(result['passed'])

    def test_translation_dependencies_are_a_separate_optional_group(self):
        def package(name,distribution=None):return {'available':name!='sentencepiece','version':'fixture','import_tested':False}
        with patch.object(runtime_check,'package',package):
            core_result=runtime_check.check();translation=runtime_check.check(required=['translation'])
        self.assertTrue(core_result['passed']);self.assertFalse(translation['passed']);self.assertIn('translation',translation['unavailable_required_groups'])

    def test_translation_probe_imports_its_declared_dependencies_without_models(self):
        def package(*args):return {'available':True,'version':'fixture','import_tested':False}
        with patch.object(runtime_check,'package',package),patch.object(runtime_check.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stderr='')) as process:
            result=runtime_check.check(required=['translation'],probe=True)
        self.assertTrue(result['passed']);self.assertEqual(set(result['import_probes']),{'sqlite3','torch','transformers','sentencepiece','sacremoses'})
        self.assertFalse(result['model_started']);self.assertFalse(result['network_used'])

    def test_translation_imports_have_dependency_declarations(self):
        tree=ast.parse((TOOLS/'query_translation.py').read_text(encoding='utf-8'))
        imports={a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names}
        imports|={n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module}
        third=imports-set(sys.stdlib_module_names)-{p.stem for p in TOOLS.glob('*.py')}
        import re
        declared={re.split(r'[<>=!~]',line.strip())[0] for name in ('requirements-semantic.txt','requirements-translation.txt') for line in (RELEASE/name).read_text().splitlines() if line.strip() and not line.startswith(('#','-'))}
        self.assertTrue(third<=declared)

if __name__=='__main__':unittest.main(verbosity=2)
