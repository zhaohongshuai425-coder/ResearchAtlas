"""Synthetic cross-step regression checks; no real reading, training or model calls."""
from pathlib import Path
import hashlib,json,sqlite3,subprocess,sys,tempfile,tracemalloc,unittest,zlib
RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
import core,research_flow as flow,handoff_dependencies as deps,evidence_decisions as ed,evidence_search as es
import evidence_followup as ef,receiver_check as receiver
from round5_fixtures import GraphFixture
from round7_fixtures import corpus,GOOD,HELD
import test_round3 as round3


class R01IndependentReport(unittest.TestCase):
    def setUp(self):
        self.f=GraphFixture();self.addCleanup(self.f.close);self.root=self.f.root
        primary=core.load(self.f.claim);report=core.load(self.f.independent)
        report['reviewed_claim_sha256']=hashlib.sha256(json.dumps(primary,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        core.save(self.f.independent,report)
        primary['independent_review']={'report':{'path':self.f.independent.name,'sha256':core.sha(self.f.independent)}}
        core.save(self.f.claim,primary)
        self.assertEqual(ed.audit(self.f.claim,self.root,True)['independent_status'],'agreed')
        flow.initialize(self.root,'Synthetic reviewed-claim handoff','round8-independent',existing=True)
        self.state=self.root/'research_state.json'
        flow.register_artifact(self.state,self.f.claim,'other','CLAIM','Synthetic standalone reviewed claim')
    def refresh_claim(self):
        state=flow.read_state(self.state);next(a for a in state['artifacts'] if a['id']=='CLAIM')['sha256']=core.sha(self.f.claim)
        flow.commit(self.state,state)
    def register_dependencies(self,skip=()):
        report=deps.inspect(self.root,flow.read_state(self.state)['artifacts'])
        for number,path in enumerate(report['unregistered_dependencies']):
            if path not in skip:flow.register_artifact(self.state,path,'other','DEP'+str(number),'Explicit fixture dependency')
    def blocked(self):
        self.assertFalse(flow.check_state(self.state)['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(self.state,self.root/'bad.zip',True)
        self.assertFalse((self.root/'bad.zip').exists())
    def test_unregistered_independent_report_is_reported_and_blocks_full_pack(self):
        report=deps.inspect(self.root,flow.read_state(self.state)['artifacts'])
        self.assertIn(self.f.independent.name,report['unregistered_dependencies'])
        self.register_dependencies(skip=(self.f.independent.name,));self.blocked()
    def test_complete_report_transfers_replays_and_receiver_cli_passes(self):
        self.register_dependencies();original=self.f.claim.read_bytes();review=self.f.independent.read_bytes()
        self.assertTrue(flow.check_state(self.state)['handoff_ready'])
        package=self.root/'complete.zip';flow.pack(self.state,package,True);dest=self.root/'received';flow.restore(package,dest)
        self.assertEqual((dest/'project/claim.json').read_bytes(),original)
        self.assertEqual((dest/'project/independent.json').read_bytes(),review)
        self.assertEqual(ed.audit(dest/'project/claim.json',dest/'project',True)['independent_status'],'agreed')
        result=subprocess.run([sys.executable,'-B','-X','utf8',str(dest/'tools/receiver_check.py'),'--folder',str(dest),
            '--output',str(self.root/'receiver.json')],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(core.load(self.root/'receiver.json')['core_passed'])
    def test_missing_independent_report_blocks_even_before_registration(self):
        self.f.independent.unlink();self.register_dependencies();self.blocked()
    def test_changed_registered_independent_report_blocks(self):
        self.register_dependencies();self.f.independent.write_text('{}',encoding='utf-8');self.blocked()
    def test_parent_traversal_report_binding_is_rejected(self):
        primary=core.load(self.f.claim);primary['independent_review']['report']['path']='../outside.json'
        core.save(self.f.claim,primary);self.refresh_claim();self.register_dependencies();self.blocked()
        self.assertTrue(any(i['status']=='outside_or_invalid_root' for i in flow.check_state(self.state)['dependencies']['issues']))
    def test_absent_independent_review_does_not_create_a_report_or_approval(self):
        primary=core.load(self.f.claim);primary.pop('independent_review');core.save(self.f.claim,primary);self.refresh_claim()
        self.register_dependencies();self.assertTrue(flow.check_state(self.state)['handoff_ready'])
        self.assertEqual(ed.audit(self.f.claim,self.root)['independent_status'],'not_performed')
        self.assertFalse(ed.audit(self.f.claim,self.root,True)['can_export_assertion'])
    def test_incomplete_declared_report_binding_blocks(self):
        primary=core.load(self.f.claim);primary['independent_review']['report'].pop('sha256')
        core.save(self.f.claim,primary);self.refresh_claim();self.blocked()


class R02FollowupQuality(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='R8 quality ');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'project'
    def fixture(self,pages,expected='cjk',query='capacity'):
        self.manifest,_=corpus(self.root,pages,expected_script=expected)
        self.query=self.root/'query.json';core.save(self.query,es.query('corpus',self.manifest,query,self.root,original_only=True,limit=1))
        self.assertEqual(len(core.load(self.query)['candidates']),1)
    def follow(self,**options):
        output=self.root/'follow.json';report=ef.save_and_replay(self.query,self.root,output,**options)
        value=core.load(output)
        for c in value['candidates']:self.assertTrue(es.resolve(output,c['candidate_id'],self.root)['replayed'])
        self.assertFalse(value['answer_generated']);return report,value
    def test_held_neighbour_is_excluded_with_original_quality_reason(self):
        self.fixture([GOOD,HELD]);before=self.query.read_bytes();report,value=self.follow(radius=1)
        self.assertEqual([c['page'] for c in value['candidates']],[1]);self.assertEqual(self.query.read_bytes(),before)
        held=value['trace']['held_passages'];self.assertEqual([c['page'] for c in held],[2])
        self.assertEqual(held[0]['quality']['expected_script'],'cjk')
        self.assertIn('unexpected_script_mapping',held[0]['quality']['flags'])
        self.assertEqual(report['passages'],1)
    def test_valid_neighbour_keeps_cjk_condition_and_remains_unreviewed(self):
        self.fixture([GOOD,'这是正常的中文原文材料，用于检查相邻页补读时是否能够保留原索引的文字质量和明确的语言条件。'*3])
        _,value=self.follow(radius=1);self.assertEqual({c['page'] for c in value['candidates']},{1,2})
        self.assertEqual(value['trace']['held_passages'],[])
        for c in value['candidates']:
            self.assertEqual(c['followup_text_quality']['expected_script'],'cjk')
            self.assertEqual(c['native']['quality']['expected_script'],'cjk')
            self.assertEqual(c['support_status'],'candidate_requires_source_review')
    def test_unknown_script_is_not_automatically_rejected(self):
        self.fixture([GOOD,HELD],expected='unknown');_,value=self.follow(radius=1)
        self.assertEqual({c['page'] for c in value['candidates']},{1,2})
        self.assertEqual(value['trace']['held_passages'],[])
        self.assertTrue(all(c['followup_text_quality']['expected_script']=='unknown' for c in value['candidates']))
    def test_persisted_manual_hold_is_not_overridden_by_clean_text(self):
        self.fixture([GOOD,GOOD+' A neighbouring passage retained for manual original-page review.'])
        index=self.root/'derived/index.sqlite';con=sqlite3.connect(index)
        quality={'gate':'hold_text_for_review','expected_script':'unknown','flags':['manual_original_page_review']}
        con.execute("UPDATE item SET quality=? WHERE kind='pdf_page' AND page=2",(json.dumps(quality),));con.commit();con.close()
        manifest=core.load(self.manifest);manifest['search_index']['sha256']=core.sha(index);core.save(self.manifest,manifest)
        core.save(self.query,es.query('corpus',self.manifest,'capacity',self.root,original_only=True,limit=1))
        _,value=self.follow(radius=1);self.assertEqual([c['page'] for c in value['candidates']],[1])
        self.assertEqual(value['trace']['held_passages'][0]['quality'],quality)
    def test_seed_after_held_first_page_does_not_reintroduce_it(self):
        self.fixture([HELD,GOOD]);self.assertEqual(core.load(self.query)['candidates'][0]['page'],2)
        _,value=self.follow(radius=1);self.assertEqual([c['page'] for c in value['candidates']],[2])
        self.assertEqual([c['page'] for c in value['trace']['held_passages']],[1])
    def test_changed_pinned_index_still_rejects_followup(self):
        self.fixture([GOOD,HELD]);(self.root/'derived/index.sqlite').write_bytes(b'Changed pinned index')
        with self.assertRaisesRegex(ValueError,'Preserved index changed'):self.follow(radius=1)
        self.assertFalse((self.root/'follow.json').exists())


class R03PngFixtureMemory(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='R8 PNG ');self.addCleanup(self.tmp.cleanup)
        self.case=round3.W05PngData();self.case.root=Path(self.tmp.name)
    def test_oversized_header_test_rejects_without_large_fixture_allocation(self):
        tracing=tracemalloc.is_tracing()
        if not tracing:tracemalloc.start()
        start,_=tracemalloc.get_traced_memory();tracemalloc.reset_peak()
        try:
            path=self.case.image(idat=zlib.compress(b'\0'),width=10000,height=10000)
            with self.assertRaisesRegex(ValueError,'64 MiB'):ed.checked_png(path)
            _,peak=tracemalloc.get_traced_memory()
        finally:
            if not tracing:tracemalloc.stop()
        self.assertLess(peak-start,4*1024*1024)
        self.assertEqual(path.stat().st_size,66)
    def test_default_valid_image_and_explicit_small_raw_keep_behavior(self):
        self.assertTrue(ed.checked_png(self.case.image())['scanlines_verified'])
        with self.assertRaisesRegex(ValueError,'decoded size'):ed.checked_png(self.case.image(raw=b'\0'*10))


if __name__=='__main__':unittest.main(verbosity=2)
