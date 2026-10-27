"""Regression cases for the seven independently reproduced 1.2.2 findings."""
from pathlib import Path
from unittest.mock import patch
import copy,gzip,hashlib,importlib.util,json,os,struct,sys,tempfile,unittest,zlib

RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
import core,literature_tools as lt,local_ingest as intake,research_flow as flow
import corpus_tools as ct,semantic_retrieval as sr,evidence_search as es,evidence_followup as ef,evidence_decisions as ed
from postfix_fixtures import corpus,binding,FakeEncoder,FakeNumpy

spec=importlib.util.spec_from_file_location('round3_installer',RELEASE/'install.py')
ins=importlib.util.module_from_spec(spec);spec.loader.exec_module(ins)
spec=importlib.util.spec_from_file_location('round3_builder',RELEASE/'build_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='Round3 synthetic ');self.root=Path(self.tmp.name).resolve()
    def tearDown(self):self.tmp.cleanup()
    def library(self):
        project=self.root/'project';flow.initialize(project,'Synthetic only','round3')
        library=project/'literature.json';lt.initialize(library,'Synthetic only','round3');return library
    def pdf(self,name='a.pdf'):
        path=self.root/name;path.write_bytes(b'%PDF-1.4\nSynthetic intake fixture '+name.encode());return path
    def meta(self,pdf,doi='10.9999/round3'):
        path=self.root/'metadata.json';core.save(path,{pdf.name:{'title':'Synthetic study','doi':doi}});return path
    def held(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a],metadata=self.meta(a))
        data=lt.load_library(library);data['documents'][0]['identity_status']='verified';flow.commit(library,data)
        b=self.pdf('b.pdf');result=intake.ingest(library,[b],metadata=self.meta(b));self.assertFalse(result['passed'])
        return library,lt.load_library(library)['intake_reviews'][0]
    def records_file(self,doi='10.9999/round3'):
        path=self.root/'records.json';core.save(path,[{'title':'Synthetic study','doi':doi}]);return path
    def package(self):
        project=self.root/'project';flow.initialize(project,'Synthetic only','round3')
        state=project/'research_state.json'
        for name in ('a.txt','b.txt'):
            (project/name).write_text('Original '+name);flow.register_artifact(state,name,'other',name,'Synthetic')
        package=self.root/'handoff.zip';flow.pack(state,package,include_artifacts=True);return package
    def two_pages(self):
        manifest=corpus(self.root/'corpus','Synthetic seedneedle evidence with explicit source conditions and limitations.')
        root=manifest.parent;m=core.load(manifest);catalog=core.load(root/m['catalog']['path'])
        source=catalog['documents'][0]['reports'][0];cache=root/source['cache']['path'];data=json.loads(gzip.decompress(cache.read_bytes()))
        data['pages'].append({'pdf_page':2,'text':'Neighbour page gives additional methodological details and caveats for this synthetic example.'})
        cache.write_bytes(gzip.compress(json.dumps(data).encode()));source['cache']=binding(root,cache)
        core.save(root/m['catalog']['path'],catalog);m['catalog']=binding(root,root/m['catalog']['path']);core.save(manifest,m)
        built=ct.build_search(manifest,root/'derived/two.sqlite');m['search_index']=binding(root,Path(built['path']));core.save(manifest,m)
        query=root/'query.json';core.save(query,es.query('corpus',manifest,'seedneedle',root,original_only=True));return root,manifest,query


class W01MigrationDependencies(Base):
    def test_held_original_and_metadata_survive_full_roundtrip(self):
        library,review=self.held();state=library.parent/'research_state.json';bound=lt.bind_to_flow(library,state)
        package=self.root/'held.zip';flow.pack(state,package,include_artifacts=True)
        restored=flow.restore(package,self.root/'restored');dest=Path(restored['state']).parent
        data=lt.load_library(dest/Path(bound['snapshot']).name)
        self.assertEqual(data['intake_reviews'][0]['status'],'needs_identity_review')
        for key in ('fulltext','metadata_source'):
            b=review[key];self.assertEqual(core.sha(dest/b['path']),b['sha256'])
        self.assertTrue(flow.check_state(restored['state'])['passed'])

    def test_missing_held_dependency_refuses_binding(self):
        library,review=self.held();(library.parent/review['metadata_source']['path']).unlink()
        with self.assertRaisesRegex(ValueError,'missing/changed'):lt.bind_to_flow(library,library.parent/'research_state.json')

    def test_conflicting_dependency_hashes_are_not_silently_overwritten(self):
        library,review=self.held();data=lt.load_library(library)
        data['intake_reviews'][0]['metadata_source']={**review['fulltext'],'sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'Conflicting'):lt.library_dependencies(data)


class W02StableIdentity(Base):
    def test_pdf_first_then_doi_import_retains_id_and_accepts_next_original(self):
        library=self.library();a=self.pdf();intake.ingest(library,[a]);identity=lt.load_library(library)['documents'][0]['id']
        intake.ingest(library,[a],metadata=self.meta(a));result=lt.import_records(library,self.records_file())
        data=lt.load_library(library);self.assertEqual(len(data['documents']),1)
        self.assertEqual(result['linked_metadata_reports'],[identity]);self.assertEqual(data['documents'][0]['id'],identity)
        b=self.pdf('b.pdf');self.assertTrue(intake.ingest(library,[b],metadata=self.meta(b))['passed'])
        self.assertEqual(len(lt.load_library(library)['documents'][0]['fulltexts']),2)

    def test_doi_import_first_then_pdf_retains_same_identity(self):
        library=self.library();lt.import_records(library,self.records_file());identity=lt.load_library(library)['documents'][0]['id']
        a=self.pdf();self.assertTrue(intake.ingest(library,[a],metadata=self.meta(a))['passed'])
        data=lt.load_library(library);self.assertEqual([d['id'] for d in data['documents']],[identity])

    def test_existing_duplicate_primary_dois_require_review_without_rewriting(self):
        library=self.library();lt.import_records(library,self.records_file());data=lt.load_library(library)
        other=copy.deepcopy(data['documents'][0]);other['id']='DOC-other';data['documents'].append(other);flow.commit(library,data)
        before=library.read_bytes()
        with self.assertRaisesRegex(ValueError,'Ambiguous'):lt.import_records(library,self.records_file())
        self.assertEqual(library.read_bytes(),before)

    def test_candidate_doi_variant_is_not_an_identity_alias(self):
        library=self.library();lt.import_records(library,self.records_file());data=lt.load_library(library)
        data['documents'][0]['metadata_variants'].append({'record':{'title':'Unreviewed','doi':'10.9999/lead'},'source':data['documents'][0]['metadata_variants'][0]['source']})
        flow.commit(library,data);lt.import_records(library,self.records_file('10.9999/lead'))
        self.assertEqual(len(lt.load_library(library)['documents']),2)


class W03SemanticSnapshots(Base):
    def build(self,backend,source,change=None):
        class Encoder(FakeEncoder):
            def encode(self,texts,role,batch=8):
                if role=='passage' and change:change()
                return super().encode(texts,role,batch)
        with patch.dict(sys.modules,{'numpy':FakeNumpy}),patch.object(sr,'Encoder',Encoder):
            return sr.build(backend,source,source.parent,None,source.parent/'semantic')

    def test_corpus_change_during_encoding_cannot_publish_success(self):
        source=corpus(self.root/'corpus','Synthetic source passage with sufficiently clear evidence and experimental conditions.')
        def change():
            m=core.load(source);m['scope']='Updated while encoding';core.save(source,m)
        with self.assertRaisesRegex(ValueError,'Source changed'):self.build('corpus',source,change)
        self.assertTrue((source.parent/'semantic/incomplete.json').exists())
        self.assertFalse((source.parent/'semantic/manifest.json').exists())

    def test_library_change_during_encoding_cannot_publish_success(self):
        source=self.library()
        def change():
            data=lt.load_library(source);data['topic']='Changed while encoding';flow.commit(source,data)
        with self.assertRaisesRegex(ValueError,'Source changed'):self.build('library',source,change)
        self.assertTrue((source.parent/'semantic/incomplete.json').exists())

    def test_stable_build_retains_starting_snapshot(self):
        source=corpus(self.root/'corpus','Synthetic stable source passage with explicit evidence and limitations for this controlled case.')
        snapshot=core.load(source);result=self.build('corpus',source)
        self.assertEqual(result['source_snapshot'],snapshot);self.assertEqual(result['source']['sha256'],core.sha(source))
        self.assertEqual(result['records'],1);self.assertFalse((source.parent/'semantic/incomplete.json').exists())

    def test_change_during_record_read_stops_before_output_creation(self):
        source=corpus(self.root/'corpus','Synthetic passage describing evidence and explicit experimental conditions.')
        original=sr.records
        def changed(*args,**kwargs):
            result=original(*args,**kwargs);m=core.load(source);m['scope']='Changed after record read';core.save(source,m);return result
        with patch.object(sr,'records',changed),self.assertRaisesRegex(ValueError,'Source changed'):self.build('corpus',source)
        self.assertFalse((source.parent/'semantic').exists())

    def test_incomplete_marker_rejects_even_present_manifest(self):
        source=corpus(self.root/'corpus','Synthetic passage describing evidence and explicit experimental conditions.')
        self.build('corpus',source);core.save(source.parent/'semantic/incomplete.json',{'status':'incomplete_do_not_use'})
        with patch.dict(sys.modules,{'numpy':FakeNumpy}),self.assertRaisesRegex(ValueError,'Incomplete'):
            sr.search(source.parent/'semantic/manifest.json','question',source.parent,encoder=FakeEncoder())


class W04PageAnchors(Base):
    def test_neighbour_anchor_uses_actual_page_and_replays(self):
        root,manifest,query=self.two_pages();follow=root/'follow.json';ef.save_and_replay(query,root,follow,radius=1,max_pages=3)
        saved=core.load(follow);second=next(c for c in saved['candidates'] if c['page']==2)
        self.assertIn('::p2::',second['anchor']);self.assertTrue(es.resolve(follow,second['candidate_id'],root)['replayed'])

    def test_forged_page_locator_is_rejected_even_with_valid_row(self):
        root,manifest,query=self.two_pages();follow=root/'follow.json';ef.save_and_replay(query,root,follow,radius=1,max_pages=3)
        saved=core.load(follow);second=next(c for c in saved['candidates'] if c['page']==2)
        second['anchor']=second['anchor'].replace('::p2::','::p1::');second['native']['anchor']=second['anchor']
        second['candidate_id']=hashlib.sha256(('corpus\0'+second['anchor']).encode()).hexdigest()[:24];core.save(follow,saved)
        with self.assertRaisesRegex(ValueError,'Corpus anchor'):es.resolve(follow,second['candidate_id'],root)


def png_chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)

class W05PngData(Base):
    def image(self,raw=None,idat=None,width=100,height=100,interlace=0):
        if idat is None:
            if raw is None:raw=(b'\0'+b'\0'*(width*4))*height
            idat=zlib.compress(raw)
        path=self.root/'preview.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,interlace))+png_chunk(b'IDAT',idat)+png_chunk(b'IEND',b''));return path
    def test_real_rgba_scanlines_pass(self):
        result=ed.checked_png(self.image());self.assertTrue(result['scanlines_verified']);self.assertFalse(result['rendered_original_content_certified'])
    def test_non_zlib_idat_with_correct_crc_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'cannot be decoded'):ed.checked_png(self.image(idat=b'not a zlib stream'))
    def test_wrong_scanline_size_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'decoded size'):ed.checked_png(self.image(raw=b'\0'*10))
    def test_invalid_filter_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'scanline filter'):ed.checked_png(self.image(raw=(b'\5'+b'\0'*400)*100))
    def test_concatenated_zlib_stream_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'stream termination'):ed.checked_png(self.image(idat=zlib.compress((b'\0'+b'\0'*400)*100)+zlib.compress(b'extra')))
    def test_large_decoded_preview_is_rejected_before_allocation(self):
        with self.assertRaisesRegex(ValueError,'64 MiB'):ed.checked_png(self.image(idat=zlib.compress(b'\0'),width=10000,height=10000))
    def claim(self,png):
        root,manifest,query=self.two_pages();candidate=core.load(query)['candidates'][0];source=candidate['native']['source_binding']
        copied=root/'preview.png';copied.write_bytes(png.read_bytes());claim=root/'claim.json'
        core.save(claim,{'schema':'research-claim-review/1.0','claim':'Synthetic fixture claim only',
            'reviewer':{'id':'fixture-author','method':'Synthetic format gate test; no actual scientific reading'},
            'parts':[{'id':'p1','text':'Synthetic fixture claim only','rationale':'Format regression only','decision':'supported',
                'conditions':['Synthetic only'],'material_types':['text'],'units_and_population':'Synthetic only',
                'negation_and_alternatives':'No scientific interpretation','external_validity':'No generalization claimed',
                'citations':[{'query':binding(root,query),'candidate_id':candidate['candidate_id'],'excerpt':'Synthetic seedneedle evidence',
                    'visual_check':{'status':'visually_checked','page':1,'source_sha256':source['sha256'],**binding(root,copied)}}]}],
            'qualified_answer':'Synthetic fixture claim only'})
        return ed.audit(claim,root)
    def test_bad_png_cannot_enable_assertion_export(self):
        with self.assertRaisesRegex(ValueError,'cannot be decoded'):
            self.claim(self.image(idat=b'not zlib'))
    def test_valid_png_keeps_existing_conditional_audit_behavior(self):
        result=self.claim(self.image());self.assertTrue(result['can_export_assertion']);self.assertFalse(result['scientific_truth_certified'])
    def test_adam7_scanlines_pass(self):
        raw=b''
        for x,y,dx,dy in [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]:
            w=(100-x+dx-1)//dx;h=(100-y+dy-1)//dy;raw+=(b'\0'+b'\0'*(w*4))*h
        self.assertTrue(ed.checked_png(self.image(raw=raw,interlace=1))['scanlines_verified'])


class W06LinkedInstalls(Base):
    def setup_link(self,junction=False):
        external=self.root/'external';external.mkdir();(external/'SKILL.md').write_text('v1')
        parent=self.root/'skills';parent.mkdir();link=parent/ins.NAME
        if junction:
            import _winapi
            if not hasattr(_winapi,'CreateJunction'):self.skipTest('Junction creation unavailable')
            _winapi.CreateJunction(str(external),str(link))
        else:
            try:link.symlink_to(external,target_is_directory=True)
            except OSError as exc:self.skipTest('Directory symlink unavailable: '+str(exc))
        self.addCleanup(lambda:os.rmdir(link) if link.exists() else None)
        return parent,link,external
    def rejected(self,junction=False):
        parent,link,external=self.setup_link(junction)
        with self.assertRaisesRegex(ValueError,'Linked/reparse'):ins.tree(link)
        baseline=self.root/'baseline.json';core.save(baseline,{'version':'1','files':{'SKILL.md':core.sha(external/'SKILL.md')}})
        # Installer verify is independent of these synthetic link fixtures.
        with patch.object(ins,'verify',return_value={'version':'2','files':{ins.NAME+'/SKILL.md':'0'*64}}):
            with self.assertRaisesRegex(ValueError,'Linked/reparse'):ins.install(parent,upgrade=True,baseline=baseline)
        self.assertEqual((external/'SKILL.md').read_text(),'v1')
        self.assertFalse((parent/'.research-skill-releases'/ins.NAME/'pending.json').exists())
        self.assertFalse((parent/'.research-skill-releases'/ins.NAME/'current.json').exists())
        self.assertEqual(list(parent.glob('.research-skill-stage-*')),[])
    def test_root_symlink_is_rejected_before_upgrade(self):self.rejected()
    @unittest.skipUnless(os.name=='nt','Windows junction')
    def test_root_junction_is_rejected_before_upgrade(self):self.rejected(junction=True)


class W07StagedRestore(Base):
    def test_interrupted_restore_leaves_no_partial_target_and_same_target_retries(self):
        package=self.package();dest=self.root/'restored';original=flow.shutil.copyfileobj;calls=[]
        def interrupted(source,target,*args,**kwargs):
            calls.append(True)
            if len(calls)==2:raise OSError('Injected output interruption')
            return original(source,target,*args,**kwargs)
        with patch.object(flow.shutil,'copyfileobj',side_effect=interrupted),self.assertRaisesRegex(OSError,'Injected'):
            flow.restore(package,dest)
        self.assertFalse(dest.exists());self.assertEqual(list(self.root.glob('.research-restore-*')),[])
        self.assertTrue(flow.verify_package(package)['passed'])
        self.assertTrue(flow.restore(package,dest)['passed']);self.assertEqual((dest/'project/a.txt').read_text(),'Original a.txt')
    def test_existing_destination_is_preserved(self):
        package=self.package();dest=self.root/'restored';dest.mkdir();(dest/'user.txt').write_text('User data')
        with self.assertRaises(FileExistsError):flow.restore(package,dest)
        self.assertEqual((dest/'user.txt').read_text(),'User data')
    def test_destination_created_during_extraction_is_preserved(self):
        package=self.package();dest=self.root/'restored';original=flow.shutil.copyfileobj;created=[]
        def intervening(source,target,*args,**kwargs):
            if not created:dest.mkdir();(dest/'user.txt').write_text('User data');created.append(True)
            return original(source,target,*args,**kwargs)
        with patch.object(flow.shutil,'copyfileobj',side_effect=intervening),self.assertRaises(FileExistsError):flow.restore(package,dest)
        self.assertEqual((dest/'user.txt').read_text(),'User data')
    def test_bad_staged_bytes_are_not_published(self):
        package=self.package();dest=self.root/'restored';original=flow.shutil.copyfileobj
        def corrupted(source,target,*args,**kwargs):
            result=original(source,target,*args,**kwargs)
            if Path(target.name).name=='a.txt':target.write(b'corrupt')
            return result
        with patch.object(flow.shutil,'copyfileobj',side_effect=corrupted),self.assertRaisesRegex(ValueError,'Restored file differs'):flow.restore(package,dest)
        self.assertFalse(dest.exists())


class BuildFailureGuard(Base):
    def test_test_failure_marks_output_noninstallable_and_does_not_publish_zip(self):
        original=builder.call;output=self.root/'failed-release'
        def fail_tests(command,cwd):
            if any(Path(str(arg)).name=='run_tests.py' for arg in command):raise RuntimeError('Injected failed tests')
            return original(command,cwd)
        with patch.object(builder,'call',side_effect=fail_tests),self.assertRaisesRegex(RuntimeError,'Injected failed tests'):
            builder.build(output,test=True,archive=True)
        self.assertFalse(core.load(output/'build_report.json')['passed'])
        self.assertFalse(Path(str(output)+'.zip').exists())
        with self.assertRaisesRegex(ValueError,'not accepted'):ins.verify(output)


if __name__=='__main__':unittest.main()
