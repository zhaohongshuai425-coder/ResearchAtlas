from pathlib import Path
import importlib.util,json,shutil,sys,tempfile,unittest,zipfile,subprocess
from unittest.mock import patch

RELEASE=Path(__file__).resolve().parents[1];TOOLS=RELEASE/'research-evidence-workflow/scripts/tools'
sys.path.insert(0,str(TOOLS))
import core,file_commit as fc,portable_context as pc,harness_tools as ht,local_ingest as intake
import research_flow as flow,literature_tools as lt
import runtime_check
spec=importlib.util.spec_from_file_location('repair_installer',RELEASE/'install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class PairCommitTests(unittest.TestCase):
    def test_writer_lock_is_released_after_process_death(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json'
            script='import sys;sys.path.insert(0,sys.argv[1]);import research_flow as f;from pathlib import Path\nwith f.locked(Path(sys.argv[2])):\n print("LOCKED",flush=True)\n sys.stdin.read()\n'
            child=subprocess.Popen([sys.executable,'-B','-c',script,str(TOOLS),str(state)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'LOCKED')
                with self.assertRaises(FileExistsError):
                    with flow.locked(state):pass
            finally:child.kill();child.wait(timeout=5);child.stdin.close();child.stdout.close();child.stderr.close()
            with flow.locked(state):pass

    def test_doctor_can_probe_core_without_model(self):
        result=runtime_check.check(probe=True)
        self.assertTrue(result['passed']);self.assertTrue(result['import_probes']['sqlite3']['passed'])
        self.assertFalse(result['model_started'])

    def test_receipt_publish_failure_recovers_same_request(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'context.txt';publish=fc._publish
            def fault(src,dst):
                if str(dst).endswith('.receipt.json'):raise OSError('injected receipt failure')
                return publish(src,dst)
            with patch.object(fc,'_publish',fault),self.assertRaises(OSError):pc.export(out)
            self.assertTrue(out.exists());self.assertFalse(out.with_name(out.name+'.receipt.json').exists())
            result=pc.export(out);self.assertTrue(result['recovered']);self.assertEqual(result['output_sha256'],core.sha(out))

    def test_recovery_preserves_user_changed_payload(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'c.txt';publish=fc._publish
            def fault(src,dst):
                if str(dst).endswith('.receipt.json'):raise OSError('fault')
                return publish(src,dst)
            with patch.object(fc,'_publish',fault),self.assertRaises(OSError):pc.export(out)
            out.write_text('my changes',encoding='utf-8')
            with self.assertRaises(FileExistsError):pc.export(out)
            self.assertEqual(out.read_text(encoding='utf-8'),'my changes')

    def test_new_request_cannot_silently_finish_previous_export(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'c.txt'
            with patch.object(fc,'_publish',side_effect=OSError('fault')),self.assertRaises(OSError):pc.export(out)
            with self.assertRaises(FileExistsError):pc.export(out,task='different request')
            self.assertTrue(fc.recover_pair(out)['passed'])

    def test_staging_failure_leaves_no_half_output(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'c.txt';original=Path.open
            def fault(path,*args,**kwargs):
                if path.name=='receipt.tmp':raise OSError('staging failure')
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',fault),self.assertRaises(OSError):pc.export(out)
            self.assertFalse(out.exists());self.assertFalse(fc.paths(out)[2].exists())
            self.assertFalse(pc.export(out)['recovered'])

class BridgeRepairTests(unittest.TestCase):
    def test_error_keeps_request_id_and_error_code(self):
        with tempfile.TemporaryDirectory() as d:
            result=ht.handle({'id':'lookup-7','tool':'research_status','arguments':{'state':'missing.json'}},d)
            self.assertEqual(result['id'],'lookup-7');self.assertEqual(result['error']['code'],'NOT_FOUND')

    def test_long_id_is_rejected_with_bounded_error(self):
        with tempfile.TemporaryDirectory() as d:
            r=ht.handle({'id':'X'*30000,'tool':'research_status','arguments':{'state':'missing'}},d)
            self.assertFalse(r['ok']);self.assertIsNone(r['id']);self.assertTrue(r['id_rejected']);self.assertLess(len(ht.serialize(r)),1000)

    def test_whole_envelope_respects_budget(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(ht,'execute',return_value={'text':'a'*950}):
                r=ht.handle({'id':'x'*256,'tool':'unused','arguments':{}},d,max_chars=1000)
            self.assertFalse(r['ok']);self.assertEqual(r['error']['code'],'BUDGET_EXCEEDED');self.assertLessEqual(len(ht.serialize(r)),1000)

    def test_malformed_tool_name_is_structured(self):
        with tempfile.TemporaryDirectory() as d:
            r=ht.handle({'id':17,'tool':[],'arguments':{}},d)
            self.assertEqual(r['id'],17);self.assertFalse(r['ok'])

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='收文献 空格 ');self.root=Path(self.tmp.name)
        self.library=self.root/'project/literature.json';lt.initialize(self.library,'Fixture research','fixture')
        self.a=self.root/'one.pdf';self.a.write_bytes(b'%PDF-1.4\nUnparsed fixture A')
    def tearDown(self):self.tmp.cleanup()
    def test_local_pdf_received_without_claiming_read(self):
        r=intake.ingest(self.library,[self.a]);self.assertTrue(r['passed'])
        doc=lt.load_library(self.library)['documents'][0]
        self.assertEqual(doc['identity_status'],'pending_identity_verification');self.assertEqual(doc['reading_status'],'not_reviewed')
        self.assertEqual(doc['metadata']['title_status'],'filename_placeholder_unverified')
        self.assertEqual(core.sha(self.library.parent/doc['fulltexts'][0]['path']),core.sha(self.a))

    def test_identical_retry_does_not_duplicate_registry_or_attempt(self):
        intake.ingest(self.library,[self.a]);first=lt.load_library(self.library)
        r=intake.ingest(self.library,[self.a]);second=lt.load_library(self.library)
        self.assertEqual(r['items'][0]['status'],'duplicate_bytes');self.assertEqual(first,second)

    def test_alias_bytes_deduplicate_but_keep_new_source(self):
        b=self.root/'copy.pdf';shutil.copy2(self.a,b)
        r=intake.ingest(self.library,[self.a,b]);data=lt.load_library(self.library)
        self.assertEqual(len(data['documents']),1);self.assertEqual(len(data['attempts']),2)
        self.assertEqual(r['items'][1]['status'],'duplicate_bytes')

    def test_zip_reads_pdf_without_extracting_member_paths(self):
        z=self.root/'papers.zip'
        with zipfile.ZipFile(z,'w') as f:f.writestr('folder/paper.pdf',self.a.read_bytes());f.writestr('notes.txt','not a PDF')
        r=intake.ingest(self.library,[z]);self.assertTrue(r['passed']);self.assertEqual(r['processed'],1)
        self.assertFalse((self.root/'folder').exists());self.assertEqual(r['items'][0]['status'],'added_document')

    def test_invalid_zip_path_is_failed_and_not_extracted(self):
        z=self.root/'bad.zip'
        with zipfile.ZipFile(z,'w') as f:f.writestr('../outside.pdf',self.a.read_bytes())
        r=intake.ingest(self.library,[z]);self.assertFalse(r['passed']);self.assertEqual(r['failed'],1)
        self.assertFalse((self.root.parent/'outside.pdf').exists())

    def test_bad_pdf_preserved_as_failure(self):
        bad=self.root/'bad.pdf';bad.write_text('<html>not pdf</html>')
        r=intake.ingest(self.library,[bad]);self.assertFalse(r['passed']);self.assertEqual(len(lt.load_library(self.library)['documents']),0)
        self.assertTrue(bad.exists())

    def test_doi_adds_version_without_marking_it_read(self):
        meta=self.root/'meta.json';core.save(meta,{self.a.name:{'title':'User reported title','doi':'10.5555/local.1'}})
        intake.ingest(self.library,[self.a],meta)
        b=self.root/'two.pdf';b.write_bytes(b'%PDF-1.4\nDifferent version B')
        core.save(meta,{b.name:{'title':'User reported title','doi':'10.5555/local.1','version_note':'needs review'}})
        r=intake.ingest(self.library,[b],meta);doc=lt.load_library(self.library)['documents'][0]
        self.assertEqual(r['items'][0]['status'],'added_version');self.assertEqual(len(doc['fulltexts']),2)
        self.assertTrue(all(v['reading_status']=='not_reviewed' for v in doc['fulltexts']))

    def test_interrupted_summary_can_be_rebuilt_without_duplicate_commit(self):
        with patch.object(fc,'atomic_json',side_effect=OSError('summary failed')),self.assertRaises(OSError):intake.ingest(self.library,[self.a])
        before=lt.load_library(self.library);r=intake.ingest(self.library,[self.a])
        self.assertTrue(r['passed']);self.assertEqual(before,lt.load_library(self.library))

    def test_index_failure_does_not_discard_received_pdf(self):
        with patch.object(lt,'index_pdf',side_effect=ValueError('unreadable test PDF')):r=intake.ingest(self.library,[self.a],index=True)
        self.assertFalse(r['passed']);self.assertEqual(r['items'][0]['index_status'],'failed')
        self.assertEqual(len(lt.load_library(self.library)['documents'][0]['fulltexts']),1)

    def test_indexed_pdf_retry_does_not_reparse_or_change_revision(self):
        pages=[{'page':1,'width':100,'height':100,'blocks':[{'text':'Fixture-only extracted text for an indexing test.','bbox':None}]}]
        with patch.object(lt,'pdf_blocks',return_value=(pages,{'backend':'fixture','version':'1'})) as parse:
            intake.ingest(self.library,[self.a],index=True);before=self.library.read_bytes()
            result=intake.ingest(self.library,[self.a],index=True)
            self.assertEqual(parse.call_count,1);self.assertEqual(result['items'][0]['index_status'],'already_indexed')
            self.assertEqual(before,self.library.read_bytes())

    def test_old_registry_schema_is_not_silently_replaced(self):
        core.save(self.library,{'schema':'some-other-library','documents':[]});before=self.library.read_bytes()
        with self.assertRaises(ValueError):intake.ingest(self.library,[self.a])
        self.assertEqual(before,self.library.read_bytes())

class InstallerRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.parent=self.root/'skills'
        self.one=self.make_release('one','1.0');self.two=self.make_release('two','2.0')
    def tearDown(self):self.tmp.cleanup()
    def make_release(self,name,version):
        root=self.root/name;(root/installer.NAME).mkdir(parents=True)
        (root/installer.NAME/'SKILL.md').write_text('version '+version,encoding='utf-8')
        core.save(root/'manifest.json',{'schema':'research-skill-release/1.0','version':version,
                   'files':{installer.NAME+'/SKILL.md':core.sha(root/installer.NAME/'SKILL.md')}})
        return root
    def install_one(self):
        with patch.object(installer,'ROOT',self.one):return installer.install(self.parent)
    def test_upgrade_and_rollback_preserve_both_versions(self):
        self.install_one()
        with patch.object(installer,'ROOT',self.two):installer.install(self.parent,upgrade=True)
        self.assertEqual(installer.status(self.parent)['version'],'2.0')
        installer.rollback(self.parent)
        self.assertEqual(installer.status(self.parent)['version'],'1.0')
        self.assertEqual(len(installer.status(self.parent)['backups']),2)
    def test_upgrade_refuses_user_edits(self):
        self.install_one();p=self.parent/installer.NAME/'SKILL.md';p.write_text('my edits')
        with patch.object(installer,'ROOT',self.two),self.assertRaises(ValueError):installer.install(self.parent,upgrade=True)
        self.assertEqual(p.read_text(),'my edits')
    def test_unmanaged_install_requires_matching_baseline(self):
        self.parent.mkdir();shutil.copytree(self.one/installer.NAME,self.parent/installer.NAME)
        with patch.object(installer,'ROOT',self.two),self.assertRaises(ValueError):installer.install(self.parent,upgrade=True)
        with patch.object(installer,'ROOT',self.two):installer.install(self.parent,upgrade=True,baseline=self.one/'manifest.json')
        self.assertEqual(installer.status(self.parent)['version'],'2.0')
    def test_interrupted_publish_resumes_from_journal(self):
        self.install_one();rename=Path.rename
        def fault(path,target):
            if path.parent.name.startswith('.research-skill-stage-'):raise OSError('publish failed')
            return rename(path,target)
        with patch.object(installer,'ROOT',self.two),patch.object(Path,'rename',fault),self.assertRaises(OSError):installer.install(self.parent,upgrade=True)
        self.assertTrue(installer.status(self.parent)['pending_operation'])
        self.assertTrue(installer.recover(self.parent)['passed']);self.assertEqual(installer.status(self.parent)['version'],'2.0')
    def test_rollback_refuses_changed_backup(self):
        self.install_one()
        with patch.object(installer,'ROOT',self.two):installer.install(self.parent,upgrade=True)
        st=installer.status(self.parent);backup=installer.locations(self.parent)[2]/'backups'/st['backups'][-1]['id']
        (backup/'SKILL.md').write_text('changed')
        with self.assertRaises(ValueError):installer.rollback(self.parent)
        self.assertEqual(installer.status(self.parent)['version'],'2.0')
    def test_staging_must_match_verified_manifest(self):
        self.one.joinpath(installer.NAME,'SKILL.md').write_text('changed after manifest')
        with patch.object(installer,'ROOT',self.one),self.assertRaises(ValueError):installer.install(self.parent)
        self.assertFalse((self.parent/installer.NAME).exists())

if __name__=='__main__':unittest.main(verbosity=2)
