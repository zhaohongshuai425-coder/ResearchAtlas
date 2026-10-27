"""Regression cases for graph commit status and bibliographic title types."""
from pathlib import Path
from unittest.mock import patch
import json,subprocess,sys,unittest
from round5_fixtures import GraphFixture
from test_round3 import Base
import core,evidence_quality as eq,file_commit as fc,literature_tools as lt

RELEASE=Path(__file__).resolve().parents[1]

class S01GraphCommitStatus(unittest.TestCase):
    def setUp(self):self.f=GraphFixture();self.addCleanup(self.f.close)
    def test_valid_graph_commit_has_matching_completion_receipt(self):
        before=self.f.graph.read_bytes();result=self.f.publish();receipt=core.load(self.f.root/result['receipt']['path'])
        self.assertTrue(result['passed']);self.assertTrue(result['committed']);self.assertFalse(result['cleanup_pending'])
        self.assertEqual(receipt['graph']['sha256'],core.sha(self.f.output));self.assertEqual(self.f.graph.read_bytes(),before)
        self.assertFalse(result['claim_audits'][0]['scientific_truth_certified'])
    def test_post_commit_cleanup_warning_and_identical_retry_complete_cleanup(self):
        unlink=Path.unlink
        def fault(path,*args,**kwargs):
            if path.parent.name.startswith('.export-stage-') and path.name=='payload.tmp':raise OSError('Injected post-commit cleanup failure')
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',fault):first=self.f.publish()
        self.assertTrue(first['committed']);self.assertTrue(first['cleanup_pending']);self.assertTrue(first['cleanup_warnings'])
        before=core.sha(self.f.output);self.assertTrue(fc.paths(self.f.output)[2].exists())
        second=self.f.publish();self.assertTrue(second['recovered']);self.assertFalse(second['cleanup_pending'])
        self.assertEqual(core.sha(self.f.output),before);self.assertFalse(fc.paths(self.f.output)[2].exists())
    def test_identical_complete_request_reuses_graph_without_overwrite(self):
        self.f.publish();before=self.f.output.read_bytes()
        with patch.object(fc,'_publish',side_effect=AssertionError('Should not publish again')):result=self.f.publish()
        self.assertTrue(result['unchanged']);self.assertEqual(self.f.output.read_bytes(),before)
    def test_receipt_publication_failure_recovers_same_graph(self):
        publish=fc._publish
        def fault(source,target):
            if target.name.endswith('.receipt.json'):raise OSError('Injected graph receipt failure')
            return publish(source,target)
        with patch.object(fc,'_publish',fault),self.assertRaises(OSError):self.f.publish()
        self.assertTrue(self.f.output.exists());self.assertFalse(fc.paths(self.f.output)[1].exists())
        before=core.sha(self.f.output);result=self.f.publish()
        self.assertTrue(result['recovered']);self.assertEqual(core.sha(self.f.output),before);self.assertTrue(fc.paths(self.f.output)[1].exists())
    def test_user_edited_graph_is_preserved_during_recovery(self):
        publish=fc._publish
        def fault(source,target):
            if target.name.endswith('.receipt.json'):raise OSError('Injected receipt failure')
            return publish(source,target)
        with patch.object(fc,'_publish',fault),self.assertRaises(OSError):self.f.publish()
        self.f.output.write_text('User changes',encoding='utf-8')
        with self.assertRaises(FileExistsError):self.f.publish()
        self.assertEqual(self.f.output.read_text(encoding='utf-8'),'User changes')
    def test_other_request_cannot_overwrite_complete_pair(self):
        self.f.publish();before=self.f.output.read_bytes();receipt=fc.paths(self.f.output)[1].read_bytes()
        with self.assertRaises(FileExistsError):self.f.publish(require_independent=False)
        self.assertEqual(self.f.output.read_bytes(),before);self.assertEqual(fc.paths(self.f.output)[1].read_bytes(),receipt)
    def test_competing_unknown_output_is_preserved(self):
        self.f.output.write_text('User data',encoding='utf-8')
        with self.assertRaises(FileExistsError):self.f.publish()
        self.assertEqual(self.f.output.read_text(encoding='utf-8'),'User data')
    def test_unapproved_claim_produces_no_graph_or_receipt(self):
        data=core.load(self.f.independent);data['parts'][0]['decision']='disagree';core.save(self.f.independent,data)
        next(iter(self.f.decisions.values()))['independent_report']['sha256']=core.sha(self.f.independent)
        with self.assertRaisesRegex(ValueError,'Unresolved'):self.f.publish()
        self.assertFalse(self.f.output.exists());self.assertFalse(fc.paths(self.f.output)[1].exists())
    def test_staging_failure_does_not_publish_partial_graph(self):
        original=Path.open
        def fault(path,*args,**kwargs):
            if path.parent.name.startswith('.export-stage-') and path.name=='receipt.tmp':raise OSError('Injected stage failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fault),self.assertRaises(OSError):self.f.publish()
        self.assertFalse(self.f.output.exists());self.assertFalse(fc.paths(self.f.output)[1].exists());self.assertFalse(fc.paths(self.f.output)[2].exists())
    def test_inner_graph_builder_can_return_without_publishing(self):
        result=eq.add_to_graph(self.f.graph,self.f.scoped,self.f.root,self.f.output,return_graph=True)
        self.assertTrue(result['passed']);self.assertIn('graph',result);self.assertFalse(self.f.output.exists())


class S02BibliographicValidation(Base):
    def cli(self,library,value):
        file=self.root/'records.json';core.save(file,value)
        command=[sys.executable,'-B','-X','utf8',str(RELEASE/'research-evidence-workflow/scripts/workflow.py'),
            'literature-tools','import','--library',str(library),'--file',str(file)]
        return subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=20)
    def test_null_title_returns_structured_field_and_record_error(self):
        library=self.library();before=library.read_bytes();result=self.cli(library,[{'title':None,'doi':'10.9999/null'}])
        value=json.loads(result.stdout);self.assertNotEqual(result.returncode,0);self.assertFalse(value['passed'])
        self.assertIn('record[0]',value['error']);self.assertIn('title',value['error']);self.assertNotIn('Traceback',result.stderr)
        self.assertEqual(library.read_bytes(),before)
    def test_other_nontext_titles_are_rejected_without_registry_mutation(self):
        library=self.library();before=library.read_bytes();file=self.root/'records.json'
        for title in (None,0,False,{},[],['text']):
            with self.subTest(title=title):
                core.save(file,[{'title':title,'doi':'10.9999/types'}])
                with self.assertRaisesRegex(ValueError,r'record\[0\].*title'):lt.import_records(library,file)
                self.assertEqual(library.read_bytes(),before)
    def test_mixed_batch_is_validated_before_any_record_is_committed(self):
        library=self.library();before=library.read_bytes();file=self.root/'records.json'
        core.save(file,[{'title':'Valid synthetic','doi':'10.9999/good'},{'title':None,'doi':'10.9999/bad'}])
        with self.assertRaisesRegex(ValueError,r'record\[1\].*title'):lt.import_records(library,file)
        self.assertEqual(library.read_bytes(),before)
    def test_invalid_top_level_shapes_are_structured_failures(self):
        library=self.library();before=library.read_bytes()
        for value in (None,7,'records',{}):
            with self.subTest(value=value):
                result=self.cli(library,value);self.assertFalse(json.loads(result.stdout)['passed']);self.assertNotIn('Traceback',result.stderr)
                self.assertEqual(library.read_bytes(),before)
    def test_invalid_records_containers_are_rejected(self):
        library=self.library();before=library.read_bytes()
        for value in (None,{},'records'):
            with self.subTest(value=value):
                result=self.cli(library,{'records':value});self.assertFalse(json.loads(result.stdout)['passed'])
                self.assertEqual(library.read_bytes(),before)
    def test_valid_record_import_still_preserves_unreviewed_status(self):
        library=self.library();result=self.cli(library,{'records':[{'title':'Valid synthetic title','doi':'https://doi.org/10.9999/Case'}]})
        self.assertEqual(result.returncode,0,result.stderr);doc=lt.load_library(library)['documents'][0]
        self.assertEqual(doc['metadata']['doi'],'10.9999/case');self.assertEqual(doc['reading_status'],'not_reviewed')
        self.assertNotEqual(doc['identity_status'],'verified')


if __name__=='__main__':unittest.main()
