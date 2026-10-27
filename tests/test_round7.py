"""Positive and negative cross-step regressions for R01--R04."""
from pathlib import Path
from unittest.mock import patch
import json,sqlite3,subprocess,sys,tempfile,unittest,zipfile
RELEASE=Path(__file__).resolve().parents[1];TOOLS=RELEASE/'research-evidence-workflow/scripts/tools'
sys.path.insert(0,str(TOOLS))
import core,research_flow as flow,evidence_search as es,literature_tools as lt,local_ingest as intake
import receiver_check as receiver,research_brief as brief,handoff_dependencies as deps
from test_round6 import Base as LibraryBase
from round7_fixtures import graph,corpus,register_dependencies,update_corpus,GOOD,HELD


class Base(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='R7 ');self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def save_query(self,backend,source,state,question='battery'):
        path=state.parent/'query.json';core.save(path,es.query(backend,source,question,state.parent,original_only=backend!='graph'))
        self.assertTrue(core.load(path)['candidates']);flow.register_artifact(state,path,'other','Q','Synthetic preserved query');return path
    def transfer(self,state):
        archive=self.root/'handoff.zip';packed=flow.pack(state,archive,True);dest=self.root/'received';flow.restore(archive,dest)
        return dest,packed
    def receiver_cli(self,dest,*args):
        out=self.root/'receiver.json'
        p=subprocess.run([sys.executable,'-B','-X','utf8',str(dest/'tools/receiver_check.py'),'--folder',str(dest),
            '--output',str(out),*args],capture_output=True,text=True,encoding='utf-8',timeout=30)
        return p,out
    def corpus_query(self,legacy=False):
        manifest,state=corpus(self.root/'corpus');path=self.save_query('corpus',manifest,state,'capacity')
        if legacy:
            data=core.load(path);data['source_snapshot']=None;core.save(path,data)
            # Rebuild the explicit query registration before any immutable bind.
            current=flow.read_state(state);current['artifacts']=[];flow.commit(state,current)
            flow.register_artifact(state,path,'other','Q','Synthetic legacy query')
        return manifest,state,path


class R01GraphQueryHandoff(Base):
    def fixture(self):
        source,state=graph(self.root/'project');query=self.save_query('graph',source,state)
        return source,state,query
    def test_normal_graph_query_roundtrip_keeps_original_query_bytes(self):
        source,state,query=self.fixture();before=query.read_bytes();self.assertTrue(flow.check_state(state)['handoff_ready'])
        dest,_=self.transfer(state);saved=dest/'project/query.json'
        self.assertEqual(saved.read_bytes(),before)
        self.assertTrue(es.resolve(saved,core.load(saved)['candidates'][0]['candidate_id'],dest/'project')['replayed'])
        self.assertTrue(receiver.check(dest)['core_passed'])
    def test_missing_graph_source_remains_a_blocker(self):
        _,state,_=self.fixture();(state.parent/'source.md').unlink()
        self.assertFalse(flow.check_state(state)['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_changed_graph_version_remains_a_blocker(self):
        source,state,_=self.fixture();data=core.load(source);data['title']='Changed graph';core.save(source,data)
        self.assertFalse(flow.check_state(state)['passed'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_escaping_graph_source_binding_is_rejected(self):
        source,state,query=self.fixture();data=core.load(query);data['candidates'][0]['source_bindings'][0]['local_path']='../private.md';core.save(query,data)
        state_data=flow.read_state(state);next(a for a in state_data['artifacts'] if a['id']=='Q')['sha256']=core.sha(query);flow.commit(state,state_data)
        report=flow.check_state(state)
        self.assertFalse(report['handoff_ready']);self.assertTrue(any(i['status']=='outside_or_invalid_root' for i in report['dependencies']['issues']))


class R02HistoricalLibrary(LibraryBase):
    def fixture(self,explicit_registry=False):
        self.library();root=self.root/'project';source=root/'custom_registry.json';state=root/'research_state.json'
        query=root/'preserved.json';core.save(query,es.query('library',source,'battery',root,original_only=True))
        if explicit_registry:flow.register_artifact(state,source,'literature_registry','WORK','Explicit immutable version request')
        pdf=self.root/'additional.pdf';pdf.write_bytes(b'%PDF-1.4\nSynthetic unrelated new PDF')
        metadata=self.root/'more.json';core.save(metadata,{pdf.name:{'title':'Synthetic additional record','doi':'10.9999/round7-additional'}})
        intake.ingest(source,[pdf],metadata=metadata);lt.bind_to_flow(source,state)
        flow.register_artifact(state,query,'other','OLD-QUERY','Synthetic query before an unrelated addition')
        return source,state,query
    def test_registry_addition_keeps_replay_and_full_migration(self):
        source,state,query=self.fixture();original=query.read_bytes();record=core.load(query)
        self.assertTrue(es.resolve(query,record['candidates'][0]['candidate_id'],state.parent)['replayed'])
        report=flow.check_state(state);self.assertTrue(report['passed'],report);self.assertTrue(report['handoff_ready'])
        self.assertTrue(any(w['status']=='changed' for w in report['dependencies']['context_warnings']))
        archive=self.root/'new.zip';flow.pack(state,archive,True);dest=self.root/'new';flow.restore(archive,dest)
        saved=dest/'project'/query.name;self.assertEqual(saved.read_bytes(),original)
        self.assertTrue(es.resolve(saved,record['candidates'][0]['candidate_id'],dest/'project')['replayed'])
        self.assertTrue(receiver.check(dest)['core_passed'])
    def test_context_warning_is_visible_in_bounded_status(self):
        _,state,_=self.fixture();report=brief.status(state)
        self.assertTrue(report['handoff_ready']);self.assertGreater(report['dependency_warning_count'],0)
        self.assertFalse(report['dependency_warnings'][0]['support_verified'])
    def test_missing_unregistered_context_does_not_remove_preserved_evidence(self):
        source,state,query=self.fixture();source.unlink();report=flow.check_state(state)
        self.assertTrue(report['handoff_ready'],report)
        self.assertTrue(any(w['status']=='missing' for w in report['dependencies']['context_warnings']))
    def test_explicitly_registered_mutable_file_cannot_silently_change(self):
        _,state,_=self.fixture(explicit_registry=True);self.assertFalse(flow.check_state(state)['passed'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_changed_preserved_original_still_blocks_full_pack(self):
        _,state,query=self.fixture();native=core.load(query)['candidates'][0]['native']
        (state.parent/native['source']['path']).write_bytes(b'Changed original')
        self.assertFalse(flow.check_state(state)['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_changed_preserved_index_still_blocks_full_pack(self):
        _,state,query=self.fixture();native=core.load(query)['candidates'][0]['native']
        (state.parent/native['index_binding']['path']).write_text('{}',encoding='utf-8')
        self.assertFalse(flow.check_state(state)['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_modified_candidate_cannot_use_registry_context_exception(self):
        _,state,query=self.fixture();data=core.load(query);data['candidates'][0]['text']='Invented passage absent from the preserved original'
        core.save(query,data);record=flow.read_state(state)
        next(a for a in record['artifacts'] if a['id']=='OLD-QUERY')['sha256']=core.sha(query);flow.commit(state,record)
        report=flow.check_state(state);self.assertTrue(all(a['status']=='intact' for a in report['artifacts'].values()))
        self.assertFalse(report['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)


class R02HistoricalCorpus(Base):
    def test_new_catalog_and_index_preserve_old_candidate_roundtrip(self):
        manifest,state,query=self.corpus_query();original=query.read_bytes();update_corpus(manifest)
        replay=es.resolve(query,core.load(query)['candidates'][0]['candidate_id'],state.parent)
        self.assertTrue(replay['replayed']);self.assertTrue(replay['source_registry_changed'])
        self.assertTrue(register_dependencies(state)['handoff_ready'])
        dest,_=self.transfer(state);saved=dest/'project/query.json';self.assertEqual(saved.read_bytes(),original)
        self.assertTrue(es.resolve(saved,core.load(saved)['candidates'][0]['candidate_id'],dest/'project')['replayed'])
        self.assertTrue(receiver.check(dest)['core_passed'])
    def test_legacy_corpus_query_without_snapshot_stays_strict(self):
        manifest,state,_=self.corpus_query(legacy=True);update_corpus(manifest);report=register_dependencies(state)
        self.assertFalse(report['handoff_ready'])
        with self.assertRaises(deps.IncompleteHandoff):flow.pack(state,self.root/'bad.zip',True)
    def test_changed_old_index_blocks_even_with_new_current_index(self):
        manifest,state,query=self.corpus_query();old=core.load(query)['source_snapshot']['search_index']['path'];update_corpus(manifest)
        register_dependencies(state);(state.parent/old).write_bytes(b'Wrong old index')
        self.assertFalse(flow.check_state(state)['handoff_ready'])
    def test_changed_old_extraction_cache_stays_a_blocker(self):
        manifest,state,_=self.corpus_query();update_corpus(manifest);register_dependencies(state)
        (state.parent/'cache.json.gz').write_bytes(b'Wrong extraction cache');self.assertFalse(flow.check_state(state)['handoff_ready'])


class R03AutomaticQuestionQuality(Base):
    def fixture(self,pages,expected_script='cjk'):
        manifest,state=corpus(self.root/'corpus',pages,expected_script);self.assertTrue(register_dependencies(state)['handoff_ready'])
        return self.transfer(state)[0]
    def test_held_first_page_is_not_used_for_automatic_question(self):
        dest=self.fixture([HELD,GOOD]);report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertTrue(report['evidence_replay_passed'])
        self.assertNotIn('unreadablemappingtoken',report['smoke'][0]['question']);self.assertIn('capacity',report['smoke'][0]['question'])
    def test_all_held_pages_are_explicitly_skipped_without_replay_claim(self):
        dest=self.fixture([HELD]);report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertFalse(report['evidence_replay_passed'])
        self.assertEqual(report['smoke'][0]['status'],'skipped_no_indexed_originals')
    def test_single_valid_page_and_explicit_query_both_still_pass(self):
        dest=self.fixture([GOOD]);self.assertTrue(receiver.check(dest)['core_passed'])
        self.assertTrue(receiver.check(dest,query='capacity')['core_passed'])
    def test_chinese_eligible_page_can_supply_automatic_control(self):
        text='本研究使用明确记录的电池循环条件分析容量衰减，并保留实验对象、数据来源及方法适用范围等信息，以供核查。'*3
        dest=self.fixture([HELD,text]);report=receiver.check(dest)
        self.assertTrue(report['core_passed'],report);self.assertTrue(report['evidence_replay_passed'])


class R04FailureReports(Base):
    def fixture(self,empty_sqlite=False):
        manifest,state=corpus(self.root/'corpus',bad_index=b'Synthetic bytes, not an SQLite database')
        if empty_sqlite:
            path=state.parent/'derived/index.sqlite';path.unlink();con=sqlite3.connect(path);con.close()
            data=core.load(manifest);data['search_index']['sha256']=core.sha(path);core.save(manifest,data)
        self.assertTrue(register_dependencies(state)['handoff_ready']);return self.transfer(state)[0]
    def assert_reported(self,dest,*args):
        p,target=self.receiver_cli(dest,*args);self.assertEqual(p.returncode,1);self.assertNotIn('Traceback',p.stderr)
        summary=json.loads(p.stdout);self.assertFalse(summary['core_passed']);report=core.load(target)
        self.assertFalse(report['core_passed']);self.assertTrue(report['manifest_problems'])
        with zipfile.ZipFile(target.with_suffix('.zip')) as z:self.assertEqual(json.loads(z.read(target.name)),report)
        return report
    def test_wrong_sqlite_bytes_generate_failed_json_and_return_zip(self):
        report=self.assert_reported(self.fixture());self.assertEqual(report['manifest_problems'][0]['error_type'],'DatabaseError')
        self.assertEqual(report['manifest_problems'][0]['stage'],'original_retrieval')
    def test_explicit_query_database_failure_also_generates_report(self):
        report=self.assert_reported(self.fixture(),'--query','capacity');self.assertEqual(report['manifest_problems'][0]['error_type'],'DatabaseError')
    def test_sqlite_missing_schema_generates_failed_report(self):
        report=self.assert_reported(self.fixture(empty_sqlite=True));self.assertEqual(report['manifest_problems'][0]['error_type'],'OperationalError')


if __name__=='__main__':unittest.main()
