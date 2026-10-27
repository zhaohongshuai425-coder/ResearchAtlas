from pathlib import Path
from unittest.mock import patch
import hashlib,importlib.util,json,os,shutil,sys,tempfile,types,unittest,zipfile

RELEASE=Path(__file__).resolve().parents[1];TOOLS=RELEASE/'research-evidence-workflow/scripts/tools'
sys.path.insert(0,str(TOOLS))
import core,local_ingest as intake,literature_tools as lt,portable_context as pc,file_commit as fc,harness_tools as ht
from intake_sources import candidates,CountBudgetError

def module(name,file):
    spec=importlib.util.spec_from_file_location(name,file);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
installer=module('patch_installer',RELEASE/'install.py');builder=module('patch_builder',RELEASE/'build_release.py')

class Fixture(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='运行修复 ');self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def library(self):
        library=self.root/'project/literature.json';lt.initialize(library,'Synthetic regression fixture','patch');return library
    def pdf(self,name='one.pdf',text=b'A'):
        path=self.root/name;path.write_bytes(b'%PDF-1.4\nUnparsed synthetic fixture '+text);return path
    def mapping(self,data):path=self.root/'metadata.json';core.save(path,data);return path
    def archive(self,name='papers.zip',count=6):
        path=self.root/name
        with zipfile.ZipFile(path,'w') as z:
            for i in range(count):z.writestr(str(i)+'.pdf',b'%PDF-1.4\nSynthetic '+str(i).encode())
        return path
    def release(self,name,version):
        root=self.root/name;(root/installer.NAME).mkdir(parents=True)
        file=root/installer.NAME/'SKILL.md';file.write_text('identical payload',encoding='utf-8')
        core.save(root/'manifest.json',{'schema':'research-skill-release/1.0','version':version,'files':{installer.NAME+'/SKILL.md':core.sha(file)}})
        return root

class UpgradeRegression(Fixture):
    def test_repeated_upgrade_is_noop_without_phantom_backup(self):
        source=self.release('one','1');parent=self.root/'skills'
        with patch.object(installer,'ROOT',source):
            installer.install(parent);before=installer.locations(parent)[3].read_bytes()
            result=installer.install(parent,upgrade=True)
        self.assertTrue(result['passed']);self.assertTrue(result['unchanged'])
        self.assertEqual(installer.locations(parent)[3].read_bytes(),before)
        self.assertEqual(installer.status(parent)['backups'],[])
        self.assertEqual(list(parent.glob('.research-skill-stage-*')),[])

    def test_metadata_only_upgrade_creates_real_backup_and_rolls_back(self):
        one=self.release('one','1');two=self.release('two','2');parent=self.root/'skills'
        with patch.object(installer,'ROOT',one):installer.install(parent)
        with patch.object(installer,'ROOT',two):installer.install(parent,upgrade=True)
        st=installer.status(parent);backup=installer.locations(parent)[2]/'backups'/st['backups'][-1]['id']
        self.assertTrue(backup.is_dir());self.assertEqual(st['version'],'2')
        installer.rollback(parent);self.assertEqual(installer.status(parent)['version'],'1')

    def test_identical_payload_published_before_ledger_failure_recovers(self):
        one=self.release('one','1');two=self.release('two','2');parent=self.root/'skills'
        with patch.object(installer,'ROOT',one):installer.install(parent)
        original=installer.atomic_json
        def fail_ledger(path,data):
            if Path(path).name=='current.json' and data.get('version')=='2':raise OSError('injected ledger failure')
            return original(path,data)
        with patch.object(installer,'ROOT',two),patch.object(installer,'atomic_json',fail_ledger),self.assertRaises(OSError):installer.install(parent,upgrade=True)
        self.assertTrue(installer.recover(parent)['passed'])
        st=installer.status(parent);self.assertEqual(st['version'],'2');self.assertFalse(st['pending_operation'])
        self.assertTrue((installer.locations(parent)[2]/'backups'/st['backups'][-1]['id']).is_dir())

    def test_matching_unmanaged_baseline_is_adopted_without_backup(self):
        source=self.release('one','1');parent=self.root/'skills';parent.mkdir();shutil.copytree(source/installer.NAME,parent/installer.NAME)
        with patch.object(installer,'ROOT',source):r=installer.install(parent,upgrade=True,baseline=source/'manifest.json')
        self.assertTrue(r['adopted_matching_unmanaged_install']);self.assertTrue(installer.status(parent)['managed'])
        self.assertEqual(installer.status(parent)['backups'],[])

class MetadataRegression(Fixture):
    def test_late_metadata_and_new_version_keep_one_stable_identity(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);identity=lt.load_library(library)['documents'][0]['id']
        mapping=self.mapping({a.name:{'title':'Reported title','doi':'10.5555/patch'}})
        intake.ingest(library,[a],metadata=mapping)
        before=library.read_bytes();intake.ingest(library,[a],metadata=mapping);self.assertEqual(library.read_bytes(),before)
        b=self.pdf('second.pdf',b'B');mapping=self.mapping({b.name:{'title':'Reported title','doi':'10.5555/patch'}})
        intake.ingest(library,[b],metadata=mapping)
        docs=lt.load_library(library)['documents'];self.assertEqual(len(docs),1);doc=docs[0]
        self.assertEqual(doc['id'],identity);self.assertEqual(doc['metadata']['title'],'Reported title')
        self.assertEqual(doc['metadata']['doi'],'10.5555/patch');self.assertEqual(len(doc['fulltexts']),2)
        self.assertEqual(doc['identity_status'],'pending_identity_verification');self.assertEqual(doc['reading_status'],'not_reviewed')
        self.assertGreaterEqual(len(doc['metadata_variants']),2)

    def test_verified_fields_remain_and_candidate_doi_does_not_merge_identity(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);data=lt.load_library(library);doc=data['documents'][0]
        doc.update(identity_status='verified');doc['metadata'].update(title='Verified title',title_status='verified');core.save(library,data)
        mapping=self.mapping({a.name:{'title':'Another reported title','doi':'10.5555/candidate'}})
        intake.ingest(library,[a],metadata=mapping)
        b=self.pdf('b.pdf',b'B');mapping=self.mapping({b.name:{'doi':'10.5555/candidate'}});intake.ingest(library,[b],metadata=mapping)
        docs=lt.load_library(library)['documents'];self.assertEqual(len(docs),2)
        self.assertEqual(docs[0]['metadata']['title'],'Verified title');self.assertEqual(docs[0]['metadata']['doi'],'')
        self.assertTrue(docs[0]['metadata_conflict']);self.assertEqual(docs[0]['identity_status'],'verified')
        self.assertEqual(docs[0]['fulltexts'][-1]['reading_status'],'not_reviewed')
        self.assertEqual(len(docs[0]['fulltexts']),1)
        self.assertIn(docs[0]['id'],docs[1]['possible_identity_matches'])

    def test_doi_owned_by_another_document_is_not_silently_merged(self):
        library=self.library();a=self.pdf();b=self.pdf('b.pdf',b'B');intake.ingest(library,[a])
        mapping=self.mapping({b.name:{'doi':'10.5555/other'}});intake.ingest(library,[b],metadata=mapping);before=library.read_bytes()
        mapping=self.mapping({a.name:{'doi':'10.5555/other'}});result=intake.ingest(library,[a],metadata=mapping)
        self.assertFalse(result['passed']);self.assertEqual(lt.load_library(library)['documents'],json.loads(before)['documents'])
        self.assertTrue(any(a.get('success') is False for a in lt.load_library(library)['attempts']))

class CleanupRegression(Fixture):
    def test_committed_output_cleanup_warning_can_be_recovered(self):
        output=self.root/'context.txt';original=Path.rmdir
        def fault(path,*args,**kwargs):
            if path.name.startswith('.export-stage-'):raise PermissionError('cleanup blocked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'rmdir',fault):result=pc.export(output)
        self.assertTrue(result['committed']);self.assertTrue(result['cleanup_pending']);self.assertTrue(fc.paths(output)[2].exists())
        receipt=core.load(fc.paths(output)[1]);self.assertEqual(receipt['output_sha256'],core.sha(output))
        recovered=fc.recover_pair(output);self.assertTrue(recovered['passed']);self.assertFalse(recovered['cleanup_pending'])
        self.assertFalse(fc.paths(output)[2].exists())

    def test_journal_cleanup_failure_is_retried_without_republishing(self):
        output=self.root/'context.txt';original=Path.unlink
        def fault(path,*args,**kwargs):
            if path.name.endswith('.pending.json'):raise PermissionError('journal cleanup blocked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fault):result=pc.export(output)
        self.assertTrue(result['committed']);before=output.read_bytes()
        second=pc.export(output);self.assertTrue(second['recovered']);self.assertEqual(output.read_bytes(),before)
        self.assertFalse(second['cleanup_pending'])

    def test_pending_cleanup_still_preserves_user_changed_output(self):
        output=self.root/'context.txt';original=Path.rmdir
        def fault(path,*args,**kwargs):
            if path.name.startswith('.export-stage-'):raise PermissionError('cleanup blocked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'rmdir',fault):pc.export(output)
        output.write_text('user edits',encoding='utf-8')
        with self.assertRaises(FileExistsError):fc.recover_pair(output)
        self.assertEqual(output.read_text(encoding='utf-8'),'user edits')

class BudgetRegression(Fixture):
    def test_invalid_archives_share_the_count_limit(self):
        paths=[]
        for i in range(3):p=self.root/f'{i}.zip';p.write_bytes(b'bad ZIP');paths.append(p)
        with self.assertRaises(CountBudgetError):candidates(paths,1,1024,1024)

    def test_unsupported_files_share_the_count_limit(self):
        paths=[]
        for i in range(3):p=self.root/f'{i}.txt';p.write_text('not PDF');paths.append(p)
        with self.assertRaises(CountBudgetError):candidates(paths,1,1024,1024)

    def test_invalid_archive_members_share_the_count_limit(self):
        archive=self.root/'bad.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('../a.pdf',b'bad');z.writestr('../b.pdf',b'bad')
        with self.assertRaises(CountBudgetError):candidates([archive],1,1024,1024)

    def test_oversized_pdf_is_recorded_without_stopping_good_pdf(self):
        library=self.library();bad=self.pdf('big.pdf',b'x'*(1024*1024));good=self.pdf('good.pdf')
        result=intake.ingest(library,[bad,good],max_file_mb=1)
        self.assertEqual(result['processed'],2);self.assertEqual(result['failed'],1)
        self.assertEqual(len(lt.load_library(library)['documents']),1)

class ErrorBudgetRegression(Fixture):
    def test_escaped_id_rejection_is_explicit_and_bounded(self):
        identity='\x00'*256;request={'id':identity,'tool':'research_status','arguments':{'state':'missing.json'}}
        result=ht.handle(request,self.root,1000)
        self.assertLessEqual(len(ht.serialize(result)),1000);self.assertIsNone(result['id']);self.assertTrue(result['id_rejected'])
        self.assertEqual(result['error']['code'],'ID_EXCEEDS_RESPONSE_BUDGET');self.assertEqual(len(result['request_id_sha256']),64)

    def test_message_shrinks_before_losing_a_fitting_id(self):
        identity='x'*256;r=ht.error_response({'id':identity},OSError('\x00'*500),1000)
        self.assertLessEqual(len(ht.serialize(r)),1000);self.assertEqual(r['id'],identity)
        self.assertTrue(r['error']['message_truncated'])

    def test_invalid_unicode_id_does_not_break_utf8_output(self):
        r=ht.handle({'id':'\ud800','tool':'research_status','arguments':{'state':'missing'}},self.root,1000)
        ht.serialize(r).encode('utf-8');self.assertTrue(r['id_rejected'])

class SnapshotRegression(Fixture):
    def test_creation_time_api_difference_does_not_reject_stable_zip(self):
        library=self.library();archive=self.archive(count=1);original=os.fstat
        def different_creation_time(fd):
            value=original(fd);fields={k:getattr(value,k) for k in dir(value) if k.startswith('st_')}
            fields['st_ctime_ns']+=1000000
            return types.SimpleNamespace(**fields)
        with patch.object(os,'fstat',different_creation_time):result=intake.ingest(library,[archive])
        self.assertTrue(result['passed']);self.assertEqual(len(lt.load_library(library)['documents']),1)

    def test_zip_is_read_once_and_members_keep_original_hash(self):
        library=self.library();archive=self.archive();expected=core.sha(archive);original=Path.open;reads=[]
        def counted(path,*args,**kwargs):
            if path.resolve()==archive.resolve() and args and args[0]=='rb':reads.append(1)
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',counted):result=intake.ingest(library,[archive])
        self.assertTrue(result['passed']);self.assertEqual(len(reads),1);self.assertEqual(result['archive_snapshots'],1)
        self.assertEqual(result['archive_snapshot_bytes'],archive.stat().st_size)
        self.assertTrue(all(d['fulltexts'][0]['container']['sha256']==expected for d in lt.load_library(library)['documents']))

    def test_archive_change_is_detected_and_snapshot_is_cleaned(self):
        archive=self.archive(count=1)
        with candidates([archive],10,1024,1024*1024) as rows:
            folder=rows.folder
            with archive.open('ab') as f:f.write(b'changed source')
            with self.assertRaisesRegex(ValueError,'Archive changed'):rows[0][1]()
        self.assertFalse(folder.exists())

    def test_snapshot_budget_includes_non_pdf_archive_payload(self):
        library=self.library();archive=self.root/'large.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('large.bin',b'x'*(2*1024*1024));z.writestr('a.pdf',b'%PDF-1.4')
        result=intake.ingest(library,[archive],max_total_mb=1)
        self.assertEqual(result['failed'],1);self.assertEqual(result['archive_snapshot_bytes'],0)
        self.assertEqual(lt.load_library(library)['documents'],[])

class BuilderRegression(Fixture):
    def build_with_change(self,relative):
        source=self.root/'source';shutil.copytree(RELEASE,source)
        changed=source/relative;changed.parent.mkdir(parents=True,exist_ok=True)
        if not changed.exists():changed.write_text('fixture',encoding='utf-8')
        original=builder.call;calls=[]
        def mutate(command,cwd):
            result=original(command,cwd);calls.append(1)
            if len(calls)==1:
                with changed.open('a',encoding='utf-8') as f:f.write('\nchanged during build')
            return result
        with patch.object(builder,'SOURCE',source),patch.object(builder,'call',mutate):return builder.build(self.root/'output',test=False)

    def test_ignored_git_change_does_not_fail_build(self):
        self.assertTrue(self.build_with_change('.git/HEAD')['passed']);self.assertFalse((self.root/'output/.git').exists())

    def test_real_source_change_still_blocks_delivery(self):
        with self.assertRaisesRegex(RuntimeError,'Source changed'):self.build_with_change('README.md')
        self.assertFalse(core.load(self.root/'output/build_report.json')['passed'])

    def test_dotted_version_is_preserved_in_archive_name(self):
        with patch.object(builder,'SOURCE',RELEASE):result=builder.build(self.root/'release.v1.2.1',test=False,archive=True)
        self.assertEqual(Path(result['archive']).name,'release.v1.2.1.zip')

    def test_archive_collision_is_detected_before_copying(self):
        output=self.root/'release.v1.2.1';archive=Path(str(output)+'.zip');archive.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):builder.build(output,test=False,archive=True)
        self.assertFalse(output.exists());self.assertEqual(archive.read_bytes(),b'preserve')

if __name__=='__main__':unittest.main(verbosity=2)
