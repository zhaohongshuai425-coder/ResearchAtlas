"""Offline DeepSeek and harness adapter checks. No provider credentials or model requests."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from release_fixtures import completed_release
from unittest.mock import patch

RELEASE = Path(__file__).resolve().parents[1]
SKILL = RELEASE / 'research-evidence-workflow'
TOOLS = SKILL / 'scripts/tools'
sys.path.insert(0, str(TOOLS))
import core
import research_flow as flow
import literature_tools as lt
import portable_context as pc
import harness_tools as ht
import runtime_check
spec = importlib.util.spec_from_file_location('platform_installer', RELEASE / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class ContextTests(unittest.TestCase):
    def test_source_newlines_normalized_but_raw_hash_retained(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            source = d / 'SKILL.md'
            source.write_bytes(b'first\r\nsecond\r\n')
            with patch.object(pc, 'SKILL', d):
                text, digest = pc.source('SKILL.md')
            self.assertEqual(text, 'first\nsecond\n')
            self.assertEqual(digest, core.sha(source))

    def test_export_bytes_equal_compiled_context_on_windows(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'context.txt'
            pc.export(out)
            self.assertEqual(out.read_bytes(), pc.build()['context'].encode('utf-8'))
            self.assertNotIn(b'\r\r\n', out.read_bytes())

    def test_all_modes_and_stages_compile_from_real_sources(self):
        for mode in pc.CAPABILITIES:
            for stage in pc.STAGES:
                with self.subTest(mode=mode, stage=stage):
                    result = pc.build(mode, stage)
                    self.assertLessEqual(result['characters'], 60000)
                    self.assertFalse(result['project_materials_included'])
                    for row in result['sources']:
                        self.assertEqual(core.sha(SKILL / row['path']), row['sha256'])

    def test_api_requires_explicit_model(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'request.json'
            with self.assertRaises(ValueError):
                pc.export(out, mode='api', task='Check a study')
            self.assertFalse(out.exists())

    def test_api_requires_explicit_task(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                pc.export(Path(d) / 'r.json', mode='api', model='user-selected-model')

    def test_api_request_shape_and_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'request.json'
            result = pc.export(out, mode='api', model='user-selected-model', task='My actual task')
            payload = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(payload['model'], 'user-selected-model')
            self.assertEqual([m['role'] for m in payload['messages']], ['system', 'user'])
            self.assertEqual(payload['messages'][1]['content'], 'My actual task')
            self.assertNotIn('tools', payload)
            self.assertFalse(result['request_sent'])
            self.assertEqual(result['output_sha256'], core.sha(out))

    def test_api_can_declare_executable_harness_tools(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'request.json'
            result = pc.export(out, mode='api', model='chosen-model', task='Read the state', with_tools=True)
            payload = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(payload['tools'], ht.definitions())
            self.assertEqual(payload['tool_choice'], 'auto')
            self.assertTrue(result['function_tools_declared'])
            self.assertFalse(result['request_sent'])

    def test_tool_flag_rejected_for_chat(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                pc.export(Path(d) / 'context.txt', with_tools=True)

    def test_does_not_overwrite_output_or_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'context.txt'
            pc.export(out)
            before = core.sha(out)
            with self.assertRaises(FileExistsError):
                pc.export(out, task='replacement')
            self.assertEqual(before, core.sha(out))

    def test_small_budget_rejected_without_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'context.txt'
            with self.assertRaises(ValueError):
                pc.export(out, max_chars=1000)
            self.assertFalse(out.exists())

    def test_unknown_mode_and_stage_rejected(self):
        with self.assertRaises(ValueError):
            pc.build('unknown')
        with self.assertRaises(ValueError):
            pc.build(stage='unknown')

    def test_reference_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            pc.source('../README.md')

    def test_export_runs_without_environment_or_network_access(self):
        # The compiler has no network path; fail if a library tries to make one.
        import urllib.request
        import socket
        with tempfile.TemporaryDirectory() as d, patch.object(urllib.request, 'urlopen', side_effect=AssertionError('network')), patch.object(socket, 'create_connection', side_effect=AssertionError('network')):
            result = pc.export(Path(d) / 'context.txt')
            self.assertFalse(result['credentials_read'])
            self.assertFalse(result['network_used'])

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Harness 科研 空格 ')
        self.root = Path(self.temp.name)
        flow.initialize(self.root / 'project', 'A real research question', 'harness-fixture')
        self.project = self.root / 'project'

    def tearDown(self):
        self.temp.cleanup()

    def test_status_request_executes_and_keeps_pending_stages(self):
        result = ht.dispatch({'id': 't1', 'tool': 'research_status', 'arguments': {'state': 'research_state.json'}}, self.project)
        self.assertTrue(result['ok'])
        self.assertEqual(result['id'], 't1')
        self.assertEqual(result['result']['stage_records_complete'], 0)
        self.assertFalse(result['model_called'])

    def test_library_metadata_tool_is_real(self):
        lt.initialize(self.project / 'literature.json', 'Research', 'fixture')
        result = ht.dispatch({'tool': 'research_library', 'arguments': {'library': 'literature.json'}}, self.project)
        self.assertEqual(result['result']['registered_documents_total'], 0)

    def test_graph_tool_is_real(self):
        value = {'schema': core.SCHEMA_VERSION, 'instance_id': 'fixture', 'nodes': [], 'edges': [], 'sources': []}
        core.save(self.project / 'graph.json', value)
        result = ht.dispatch({'tool': 'research_graph', 'arguments': {'graph': 'graph.json'}}, self.project)
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['node_total'], 0)

    def test_missing_source_remains_a_failure(self):
        state = core.load(self.project / 'research_state.json')
        state['artifacts'] = [{'id': 'bad', 'path': 'missing.json', 'sha256': '0'*64,
                              'kind': 'other', 'description': 'missing'}]
        core.save(self.project / 'research_state.json', state)
        result = ht.dispatch({'tool': 'research_status', 'arguments': {'state': 'research_state.json'}}, self.project)
        self.assertFalse(result['ok'])

    def test_reference_pagination_preserves_exact_source(self):
        offset = 0
        pieces = []
        while offset is not None:
            result = ht.execute('research_reference', {'path': 'SKILL.md', 'offset': offset, 'limit': 1000}, self.project)
            pieces.append(result['text'])
            offset = result['next_offset']
        self.assertEqual(''.join(pieces), (SKILL / 'SKILL.md').read_text(encoding='utf-8-sig'))

    def test_arbitrary_root_and_shell_parameters_rejected(self):
        for args in ({'state': 'research_state.json', 'root': 'C:/'},
                     {'state': 'research_state.json', 'command': 'do something'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ht.execute('research_status', args, self.project)

    def test_parent_and_absolute_paths_rejected(self):
        for value in ('../outside.json', str(self.root / 'outside.json')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ht.execute('research_status', {'state': value}, self.project)

    def test_wrong_types_and_unbounded_pages_rejected(self):
        for limit in (True, 0, 11, '3'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ht.execute('research_library', {'library': 'literature.json', 'limit': limit}, self.project)

    def test_non_reference_file_is_not_exposed(self):
        for path in ('scripts/tools/core.py', 'references/../../scripts/workflow.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ht.execute('research_reference', {'path': path}, self.project)

    def test_request_shape_and_unknown_tool_rejected(self):
        for request in ([], {'tool': 'shell', 'arguments': {}}, {'tool': 'research_status', 'arguments': {}, 'root': 'elsewhere'}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                ht.dispatch(request, self.project)

    def test_bridge_preserves_project_bytes(self):
        before = {p.relative_to(self.project): core.sha(p) for p in self.project.rglob('*') if p.is_file()}
        ht.execute('research_status', {'state': 'research_state.json'}, self.project)
        after = {p.relative_to(self.project): core.sha(p) for p in self.project.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_synthetic_function_call_round_trip_without_model(self):
        message = {'role': 'assistant', 'tool_calls': [{'id': 'call-fixture', 'type': 'function', 'function': {
            'name': 'research_status', 'arguments': json.dumps({'state': 'research_state.json'})}}]}
        call = message['tool_calls'][0]
        result = ht.dispatch({'id': call['id'], 'tool': call['function']['name'], 'arguments': json.loads(call['function']['arguments'])}, self.project)
        reply = {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result)}
        self.assertEqual(reply['tool_call_id'], result['id'])
        self.assertTrue(json.loads(reply['content'])['ok'])

    def test_bridge_cli_executes_one_request(self):
        request = self.root / 'request.json'
        core.save(request, {'id': 1, 'tool': 'research_status', 'arguments': {'state': 'research_state.json'}})
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SKILL / 'scripts/workflow.py'),
            'harness-tools', '--root', str(self.project), '--input', str(request)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])

class InstallTargetTests(unittest.TestCase):
    def test_opencode_project_install_path(self):
        with tempfile.TemporaryDirectory() as d,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            value = installer.install(target='opencode', project=d)
            self.assertEqual(Path(value['installed']), Path(d) / '.opencode/skills/research-evidence-workflow')

    def test_harness_project_install_path(self):
        with tempfile.TemporaryDirectory() as d,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            value = installer.install(target='harness', project=d)
            self.assertEqual(Path(value['installed']), Path(d) / '.dsh/skills/research-evidence-workflow')

    def test_shared_agent_project_path(self):
        with tempfile.TemporaryDirectory() as d,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            value = installer.install(target='agent', project=d)
            self.assertEqual(Path(value['installed']), Path(d) / '.agents/skills/research-evidence-workflow')

    def test_harness_user_home_respects_explicit_config(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'DSH_HOME': d}):
            self.assertEqual(installer.resolve_parent(target='harness'), Path(d).resolve() / 'skills')

    def test_opencode_config_home_respected(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'XDG_CONFIG_HOME': d}):
            self.assertEqual(installer.resolve_parent(target='opencode'), Path(d).resolve() / 'opencode/skills')

    def test_conflicting_install_locations_rejected(self):
        with self.assertRaises(ValueError):
            installer.resolve_parent('first', project='second')

    def test_missing_client_does_not_block_core_tools(self):
        with patch.object(runtime_check.shutil, 'which', return_value=None):
            self.assertTrue(runtime_check.check()['passed'])
            self.assertFalse(runtime_check.check(['harness'])['passed'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
