from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

from unittest import mock

import json

import subprocess

import sys

import tempfile

import types

import unittest

import core

import research_flow as flow

import literature_tools as lt

class LiteratureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / '试验夹具 项目'
        flow.initialize(self.project, '仅用于测试的主题', 'fixture')
        self.state = self.project / 'research_state.json'
        self.library = self.project / 'literature.json'
        lt.initialize(self.library, '仅用于测试的主题', 'fixture')

    def tearDown(self):
        if not self.root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError('Unexpected test cleanup path')
        self.temp.cleanup()

    def add(self, doi='10.5555/fixture.1', title='Fixture attribution study', included=True):
        file = self.project / 'incoming.json'
        core.save(file, [{'title': title, 'doi': doi, 'url': 'https://example.invalid/paper',
                          'authors': ['Simulated Author'], 'fulltext_candidates': ['https://example.invalid/paper.pdf']}])
        result = lt.import_records(self.library, file)
        identity = (result['added'] + result['linked_metadata_reports'])[0]
        if included:
            lt.select_document(self.library, identity, 'included', 'Isolated fixture scope only')
        return identity

    def indexed(self):
        identity = self.add()
        with mock.patch.object(lt, 'http_get', return_value=(b'%PDF-1.7 simulated fixture bytes', {'final_url': 'https://example.invalid/p.pdf'})):
            self.assertTrue(lt.fetch_pdf(self.library, identity)['passed'])
        pages = [{'page': 1, 'width': 600, 'height': 800, 'blocks': []},
                 {'page': 2, 'width': 600, 'height': 800, 'blocks': [
                     {'text': 'Citation completeness and entailment are different assessment concepts. Figure 1: fixture only.',
                      'bbox': [20, 30, 250, 110]},
                     {'text': 'A separate passage concerns runtime and resources.', 'bbox': [20, 150, 250, 210]}]}]
        with mock.patch.object(lt, 'pdf_blocks', return_value=(pages, {'backend': 'fixture', 'version': '1'})):
            report = lt.index_pdf(self.library, identity)
        index = lt.read_index(self.project, report['index'])
        return identity, report, index

    def test_zero_material_init_and_preserve_existing(self):
        self.assertEqual(lt.load_library(self.library)['documents'], [])
        before = self.library.read_bytes()
        with self.assertRaises(FileExistsError):
            lt.initialize(self.library, 'other', 'other')
        self.assertEqual(before, self.library.read_bytes())

    def test_doi_duplicates_link_reports_without_reading_promotion(self):
        first = self.add(included=False)
        second = self.add(doi='https://doi.org/10.5555/FIXTURE.1', included=False)
        data = lt.load_library(self.library)
        self.assertEqual(first, second)
        self.assertEqual(len(data['documents']), 1)
        self.assertEqual(len(data['documents'][0]['metadata_variants']), 2)
        self.assertEqual(data['documents'][0]['selection_status'], 'candidate')
        self.assertEqual(data['documents'][0]['reading_status'], 'not_reviewed')

    def test_same_title_different_doi_not_merged(self):
        self.add(doi='10.5555/fixture.1')
        self.add(doi='10.5555/fixture.2')
        data = lt.load_library(self.library)
        self.assertEqual(len(data['documents']), 2)
        self.assertEqual(len(data['title_collision_review']), 1)

    def test_later_publication_report_can_supply_missing_pdf_link(self):
        identity = self.add()
        with flow.locked(self.library):
            data = lt.load_library(self.library)
            doc = lt.document(data, identity)
            doc['metadata']['fulltext_candidates'] = []
            doc['metadata_variants'][0]['record']['fulltext_candidates'] = []
            flow.commit(self.library, data)
        file = self.project / 'publication-supplement.json'
        core.save(file, [{'title': 'Fixture attribution study', 'doi': '10.5555/fixture.1',
                          'url': 'https://example.invalid/official', 'fulltext_candidates': ['https://example.invalid/official.pdf']}])
        lt.import_records(self.library, file)
        with mock.patch.object(lt, 'http_get', return_value=(b'%PDF-1.7 simulated source', {'final_url': 'https://example.invalid/official.pdf'})) as request:
            self.assertTrue(lt.fetch_pdf(self.library, identity)['passed'])
            self.assertEqual(request.call_args.args[0], 'https://example.invalid/official.pdf')

    def test_publication_metadata_preserves_original_html(self):
        html = b'<meta name="citation_title" content="Simulated title"><meta name="citation_doi" content="10.5555/html.fixture"><meta name="citation_pdf_url" content="/p.pdf">'
        with mock.patch.object(lt, 'http_get', return_value=(html, {'final_url': 'https://example.invalid/p'})):
            report = lt.capture_publication(self.library, 'https://example.invalid/p')
        doc = lt.document(lt.load_library(self.library), report['added'][0])
        binding = doc['metadata_variants'][0]['source']
        self.assertEqual((self.project / binding['path']).read_bytes(), html)
        self.assertEqual(doc['metadata']['fulltext_candidates'], ['https://example.invalid/p.pdf'])

    def test_network_failure_and_html_pdf_rejection_logged(self):
        with mock.patch.object(lt, 'http_get', side_effect=OSError('simulated unavailable service')):
            self.assertFalse(lt.discover(self.library, 'fixture topic', 3)['passed'])
        identity = self.add()
        with mock.patch.object(lt, 'http_get', return_value=(b'<html>not a PDF</html>', {'final_url': 'https://example.invalid/login'})):
            self.assertFalse(lt.fetch_pdf(self.library, identity)['passed'])
        data = lt.load_library(self.library)
        self.assertEqual(len(data['attempts']), 2)
        self.assertEqual(lt.document(data, identity)['availability'], 'not_retrieved')
        self.assertEqual(lt.document(data, identity)['reading_status'], 'not_reviewed')

    def test_unsupported_url_and_missing_original_do_not_fabricate_text(self):
        with self.assertRaises(ValueError):
            lt.http_get('file:///private.pdf')
        identity = self.add()
        with self.assertRaises(ValueError):
            lt.index_pdf(self.library, identity)
        result = lt.retrieve(self.library, 'citation entailment')
        self.assertEqual(result['hits'], [])
        self.assertEqual(result['metadata_only_documents'], [identity])

    def test_page_bbox_anchor_and_scanned_page_status(self):
        identity, report, index = self.indexed()
        self.assertEqual(report['sparse_pages_needing_ocr_review'], [1])
        anchor = index['chunks'][0]['anchor']
        located = lt.locate(self.library, anchor)
        self.assertEqual(located['page'], 2)
        self.assertEqual(located['bbox'], [20, 30, 250, 110])
        self.assertEqual(lt.document(lt.load_library(self.library), identity)['reading_status'], 'not_reviewed')
        self.assertEqual(located['label_status'], 'text_mentions_not_verified_figure_bounds_or_formula_semantics')

    def test_footer_only_boxes_fall_back_to_available_body_text(self):
        body = 'Available main-body evidence about citation assessment. ' * 80
        page = types.SimpleNamespace(width=600,height=800,
                objects={'textboxhorizontal':[{'text':'7','x0':20,'top':780,'x1':30,'bottom':790}]},
                chars=[{'text':'x'}]*1000, extract_text=lambda:body, close=lambda:None)
        pdf = mock.MagicMock()
        pdf.__enter__.return_value.pages = [page]
        module = types.SimpleNamespace(open=mock.Mock(return_value=pdf))
        with mock.patch.dict(sys.modules, {'pdfplumber':module}), mock.patch.object(lt.metadata,'version',return_value='fixture'):
            pages, engine = lt.pdf_blocks('simulated.pdf','pdfplumber')
        self.assertEqual(pages[0]['blocks'][0]['text'],body)
        self.assertTrue(pages[0]['coverage_fallback_used'])
        self.assertIsNone(pages[0]['blocks'][0]['bbox'])

    def test_question_retrieval_expansions_and_no_answer_fabrication(self):
        identity, report, index = self.indexed()
        result = lt.retrieve(self.library, '如何核查引用完整性', terms=['citation completeness entailment'], concepts=[['citation'], ['completeness', 'entailment']])
        self.assertTrue(result['hits'])
        self.assertEqual(result['hits'][0]['page'], 2)
        self.assertFalse(result['answer_generated'])
        self.assertIn('requires_source_review', result['hits'][0]['role'])
        self.assertEqual(lt.retrieve(self.library, 'quasar orbital parallax')['hits'], [])
        lt.select_document(self.library, identity, 'excluded', 'Fixture exclusion')
        self.assertEqual(lt.retrieve(self.library, 'citation')['hits'], [])

    def test_stale_source_or_index_does_not_return_evidence(self):
        identity, report, index = self.indexed()
        source = self.project / index['source']['path']
        source.write_bytes(b'changed original')
        result = lt.retrieve(self.library, 'citation')
        self.assertEqual(result['hits'], [])
        self.assertTrue(result['warnings'])
        with self.assertRaises(ValueError):
            lt.locate(self.library, index['chunks'][0]['anchor'])

    def test_export_graph_scoped_to_included_without_support_claims(self):
        identity, report, index = self.indexed()
        self.add(doi='10.5555/not.included', title='Not selected fixture', included=False)
        output = self.project / 'graph.v1.json'
        self.assertTrue(lt.graph_view(self.library, output)['passed'])
        graph = core.load(output)
        self.assertEqual(sum(n['type'] == 'document' for n in graph['nodes']), 1)
        self.assertFalse(any(e['relation'] in {'supports', 'challenges'} for e in graph['edges']))
        self.assertTrue(all(n['state']['reading'] == 'not_reviewed' for n in graph['nodes']))
        with self.assertRaises(FileExistsError):
            lt.graph_view(self.library, output)

    def test_immutable_snapshot_binding_and_relocated_retrieval(self):
        identity, report, index = self.indexed()
        first = lt.bind_to_flow(self.library, self.state)
        self.assertTrue(first['registered'])
        self.assertEqual(lt.bind_to_flow(self.library, self.state)['registered'], [])
        lt.select_document(self.library, identity, 'included', 'A new decision does not mutate old snapshots')
        self.assertTrue(flow.check_state(self.state)['passed'])
        package = self.root / 'library.zip'
        flow.pack(self.state, package, True)
        dest = self.root / '恢复路径'
        flow.restore(package, dest)
        snapshot = dest / 'project' / Path(first['snapshot']).name
        result = subprocess.run([sys.executable, str(dest / 'tools/literature_tools.py'), 'ask', '--library', str(snapshot),
                                 '--question', 'citation completeness'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(json.loads(result.stdout)['hits'])
