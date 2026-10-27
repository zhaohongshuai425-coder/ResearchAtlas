from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

import importlib.util

import json

import os

import subprocess

import sys

import tempfile

import unittest

from unittest import mock

import zipfile

import research_flow as flow

import core

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / '独立项目 含空格'
        flow.initialize(self.project, '仅用于流程测试的方向', 'test-project')
        self.state = self.project / 'research_state.json'

    def tearDown(self):
        self.temp.cleanup()

    def deliver(self):
        source = self.project / '可行性.md'
        source.write_text('测试夹具：可核来源与未知项，不是实际研究结论。', encoding='utf-8')
        flow.register_artifact(self.state, source, 'feasibility', 'A1', '隔离测试文件')
        flow.set_stage(self.state, 'scope', 'complete', '测试交付条件', ['A1'])
        return source

    def test_direction_only_initialization(self):
        state = flow.read_state(self.state)
        self.assertEqual(state['artifacts'], [])
        self.assertTrue(all(s['status'] == 'pending' for s in state['stages'].values()))
        self.assertFalse((self.project / 'registry.json').exists())
        with self.assertRaises(FileExistsError):
            flow.initialize(self.project, '禁止覆盖', 'other', existing=True)

    def test_existing_project_preserved(self):
        folder = self.root / '原有项目'
        folder.mkdir()
        sentinel = folder / '研究原件.json'
        sentinel.write_bytes(b'protected')
        with self.assertRaises(FileExistsError):
            flow.initialize(folder, '已有方向', 'existing')
        flow.initialize(folder, '已有方向', 'existing', existing=True)
        self.assertEqual(sentinel.read_bytes(), b'protected')

    def test_completion_requires_kind_reason_and_intact_evidence(self):
        before = self.state.read_bytes()
        for reason, evidence in [('', []), ('记录完整', [])]:
            with self.assertRaises(ValueError):
                flow.set_stage(self.state, 'scope', 'complete', reason, evidence)
        self.assertEqual(before, self.state.read_bytes())
        self.deliver()
        with self.assertRaises(ValueError):
            flow.set_stage(self.state, 'search', 'complete', '错误复用文件类型', ['A1'])
        self.assertEqual(flow.check_state(self.state)['stage_records_complete'], 1)

    def test_changed_artifact_flags_review_without_rewriting_history(self):
        source = self.deliver()
        history_before = {p.name: p.read_bytes() for p in (self.project / '.research_history').iterdir()}
        state_before = self.state.read_bytes()
        source.write_text('测试夹具发生字节变化', encoding='utf-8')
        report = flow.check_state(self.state)
        self.assertFalse(report['passed'])
        self.assertEqual(report['stages']['scope']['effective_status'], 'needs_review')
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(history_before, {p.name: p.read_bytes() for p in (self.project / '.research_history').iterdir()})

    def test_outside_root_rejected(self):
        outside = self.root / 'outside.md'
        outside.write_text('外部夹具', encoding='utf-8')
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            flow.register_artifact(self.state, outside, 'other', 'X', '不能越界')
        self.assertEqual(self.state.read_bytes(), before)

    def test_lock_and_revision_history(self):
        with flow.locked(self.state):
            with self.assertRaises(FileExistsError):
                flow.add_note(self.state, 'decision', '重复写入')
        flow.add_note(self.state, 'decision', '测试中已确认的决定')
        self.assertEqual(flow.read_state(self.state)['revision'], 1)
        self.assertEqual(len(list((self.project / '.research_history').iterdir())), 1)

    def test_metadata_handoff_exposes_missing_sources(self):
        self.deliver()
        package = self.root / 'metadata.zip'
        report = flow.pack(self.state, package)
        self.assertEqual(report['omitted_artifacts'], ['可行性.md'])
        dest = self.root / 'meta-restored'
        flow.restore(package, dest)
        restored = flow.check_state(dest / 'project/research_state.json')
        self.assertEqual(restored['stages']['scope']['effective_status'], 'needs_review')
        self.assertEqual(restored['artifacts']['A1']['status'], 'missing')

    def test_full_handoff_relocated_and_cli_works(self):
        self.deliver()
        flow.add_note(self.state, 'next_action', '下一步进行来源检索')
        package = self.root / '完整包.zip'
        flow.pack(self.state, package, True)
        dest = self.root / '另一电脑路径 含空格'
        flow.restore(package, dest)
        state = dest / 'project/research_state.json'
        self.assertTrue(flow.check_state(state)['passed'])
        result = subprocess.run([sys.executable, str(dest / 'tools/research_flow.py'), 'status', '--state', str(state)], cwd=self.root,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['stage_records_complete'], 1)
        self.assertEqual(report['notes'][0]['text'], '下一步进行来源检索')
        expected_history = {p.name: p.read_bytes() for p in (self.project / '.research_history').iterdir()}
        actual_history = {p.name: p.read_bytes() for p in (dest / 'project/.research_history').iterdir()}
        self.assertEqual(expected_history, actual_history)
        with self.assertRaises(FileExistsError):
            flow.restore(package, dest)
        with self.assertRaises(FileExistsError):
            flow.pack(self.state, package, True)

    def test_tampered_package_rejected_before_restore(self):
        package = self.root / 'ok.zip'
        flow.pack(self.state, package)
        corrupt = self.root / 'corrupt.zip'
        with zipfile.ZipFile(package) as original, zipfile.ZipFile(corrupt, 'w') as changed:
            for name in original.namelist():
                blob = original.read(name)
                changed.writestr(name, blob + b'changed' if name == 'handoff.md' else blob)
        dest = self.root / 'must-not-exist'
        with self.assertRaises(ValueError):
            flow.restore(corrupt, dest)
        self.assertFalse(dest.exists())

    def test_unsafe_package_paths_rejected(self):
        for name in ('../escape', '/absolute', 'C:/drive', 'project\\escape', 'project/../escape'):
            with self.assertRaises(ValueError):
                flow.safe_member(name)

    def test_pack_does_not_delete_a_file_created_by_another_writer(self):
        output = self.root / 'racing.zip'
        def racing_writer(path, mode, **kwargs):
            Path(path).write_bytes(b'other writer file')
            raise FileExistsError('simulated exclusive creation conflict')
        with mock.patch.object(flow.zipfile, 'ZipFile', side_effect=racing_writer):
            with self.assertRaises(FileExistsError):
                flow.pack(self.state, output)
        self.assertEqual(output.read_bytes(), b'other writer file')

    def fact_ledger(self):
        evidence = self.project / 'result.txt'
        manuscript = self.project / 'draft.md'
        evidence.write_text('测试夹具：固定口径结果0.80。', encoding='utf-8')
        manuscript.write_text('测试夹具：摘要报告0.8。', encoding='utf-8')
        expected = {'value': '0.80', 'unit': 'fraction', 'context': {'split': 'fixture'}}
        fact = {'id': 'F1', 'definition': '测试夹具的同一指标与条件', 'expected': expected,
                'evidence': {'path': 'result.txt', 'sha256': core.sha(evidence), 'locator': '第1行', 'excerpt': '固定口径结果0.80'},
                'mentions': [{'path': 'draft.md', 'sha256': core.sha(manuscript), 'locator': '摘要', 'excerpt': '摘要报告0.8',
                              'value': '0.8', 'unit': 'fraction', 'context': {'split': 'fixture'}}]}
        ledger = self.project / 'facts.json'
        core.save(ledger, {'schema': 'research-facts/1.0', 'facts': [fact]})
        return ledger, fact

    def test_explicit_fact_values_units_context_and_excerpts(self):
        ledger, fact = self.fact_ledger()
        self.assertTrue(flow.check_facts(ledger, self.project)['passed'])
        for key, value in [('value', '0.9'), ('unit', 'percent'), ('context', {'split': 'different'}), ('excerpt', 'not in manuscript')]:
            data = core.load(ledger)
            data['facts'][0]['mentions'][0][key] = value
            temp = self.project / 'mismatch.json'
            core.save(temp, data)
            self.assertFalse(flow.check_facts(temp, self.project)['passed'], key)
        core.save(ledger, {'schema': 'research-facts/1.0', 'facts': []})
        self.assertFalse(flow.check_facts(ledger, self.project)['passed'])

    def test_graph_audit_propagates_changes_beyond_two_hops(self):
        source = self.project / 'source.md'
        source.write_text('isolated fixture', encoding='utf-8')
        stable = self.project / 'stable.md'
        stable.write_text('stable fixture relations', encoding='utf-8')
        axes = {axis: 'unknown' for axis in core.AXES}
        nodes = [{'id': 'test-project::' + str(i), 'type': 'claim', 'label': 'same' if i < 2 else str(i),
                  'state': axes.copy(), 'source_refs': [{'source_id': 'test-project::s', 'locator': 'fixture'}] if i == 0 else []} for i in range(6)]
        edges = [{'id': 'e' + str(i), 'from': nodes[i]['id'], 'to': nodes[i + 1]['id'], 'relation': 'related_to',
                  'source_refs': [{'source_id': 'test-project::stable', 'locator': 'fixture'}]} for i in range(5)]
        graph = {'schema': core.SCHEMA_VERSION, 'instance_id': 'test-project', 'nodes': nodes, 'edges': edges,
                 'sources': [{'id': 'test-project::s', 'local_path': 'source.md', 'sha256': core.sha(source)},
                             {'id': 'test-project::stable', 'local_path': 'stable.md', 'sha256': core.sha(stable)}]}
        file = self.project / 'graph.json'
        core.save(file, graph)
        report = flow.graph_audit(file, self.project)
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['duplicate_label_review']), 1)
        self.assertFalse(report['automatic_merge'])
        source.write_text('modified fixture', encoding='utf-8')
        changed = flow.graph_audit(file, self.project)
        self.assertFalse(changed['passed'])
        self.assertIn('test-project::5', changed['transitive_review_queue'])

    def test_graph_inherited_review_does_not_become_new_or_pending(self):
        source = self.project / 'evidence.md'
        source.write_text('fixture evidence', encoding='utf-8')
        axes = {axis: 'unknown' for axis in core.AXES}
        nodes = [{'id': 'test-project::ev', 'type': 'evidence', 'label': 'evidence', 'state': axes.copy(), 'source_refs': []},
                 {'id': 'test-project::c', 'type': 'claim', 'label': 'claim', 'state': axes.copy(), 'source_refs': []}]
        edge = {'id': 'E', 'from': nodes[0]['id'], 'to': nodes[1]['id'], 'relation': 'supports',
                'source_refs': [{'source_id': 'test-project::s', 'locator': 'fixture'}],
                'conditions': ['fixture only'], 'review_status': 'inherited_source_scoped_record_not_new_review'}
        graph = {'schema': core.SCHEMA_VERSION, 'instance_id': 'test-project', 'nodes': nodes, 'edges': [edge],
                 'sources': [{'id': 'test-project::s', 'local_path': source.name, 'sha256': core.sha(source)}]}
        file = self.project / 'inherited.json'
        core.save(file, graph)
        report = flow.graph_audit(file, self.project)
        self.assertTrue(report['passed'])
        self.assertEqual(report['evidence_review_pending'], [])
        self.assertEqual(report['evidence_review_inherited'], ['E'])
