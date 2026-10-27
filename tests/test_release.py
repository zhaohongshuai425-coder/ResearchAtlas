"""Offline release checks using temporary projects; no model calls or training."""
from pathlib import Path
import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from release_fixtures import completed_release

RELEASE = Path(__file__).resolve().parents[1]
SKILL = RELEASE / 'research-evidence-workflow'
ENTRY = SKILL / 'scripts/workflow.py'
TOOLS = SKILL / 'scripts/tools'
sys.path.insert(0, str(TOOLS))
import core
import research_flow as flow
import runtime_check
import run_codex_task as runner

spec = importlib.util.spec_from_file_location('release_dispatcher', ENTRY)
dispatcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatcher)
install_spec = importlib.util.spec_from_file_location('release_installer', RELEASE / 'install.py')
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)

def run(*args, cwd=None):
    return subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ENTRY), *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)

class ReleaseTests(unittest.TestCase):
    def test_every_helper_help_runs(self):
        for name in dispatcher.COMMANDS:
            with self.subTest(command=name):
                result = run(name, '--help')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout.lower())

    def test_top_level_help_is_success(self):
        self.assertEqual(run('--help').returncode, 0)
        self.assertEqual(run().returncode, 0)

    def test_unknown_command_is_parameter_error(self):
        result = run('../core.py')
        self.assertEqual(result.returncode, 2)

    def test_missing_input_is_failure_not_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            result = run('brief', 'status', '--state', Path(d).resolve() / 'missing.json')
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['passed'])
            self.assertNotIn('Traceback', result.stderr)

    def test_new_project_round_trip_with_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix='科研 Skill 空格 ') as d:
            d = Path(d).resolve()
            result = run('research-flow', 'init', d / 'project', '--topic', '电池寿命预测', '--id', 'battery', cwd=d)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = run('brief', 'status', '--state', d / 'project/research_state.json', cwd=d)
            value = json.loads(result.stdout)
            self.assertTrue(value['passed'])
            self.assertEqual(value['stage_records_total'], 13)
            self.assertEqual(value['stage_records_complete'], 0)
            self.assertTrue(all(s['effective_status'] != 'completed' for s in value['stages'].values()))

    def test_handoff_still_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            flow.initialize(d / 'project', 'A source-linked research test', 'release-test')
            result = flow.pack(d / 'project/research_state.json', d / 'handoff.zip')
            self.assertTrue(result['passed'])
            self.assertTrue(flow.verify_package(d / 'handoff.zip')['passed'])
            with zipfile.ZipFile(d / 'handoff.zip') as z:
                self.assertIn('tools/runtime_check.py', z.namelist())

    def test_optional_dependencies_do_not_block_core(self):
        with patch.object(runtime_check, 'package', return_value={'available': False, 'version': None, 'import_tested': False}):
            result = runtime_check.check()
            self.assertTrue(result['passed'])
            self.assertFalse(result['groups']['semantic']['available'])
            self.assertFalse(result['network_used'])
            self.assertFalse(result['model_started'])

    def test_required_missing_dependency_fails(self):
        with patch.object(runtime_check, 'package', return_value={'available': False, 'version': None, 'import_tested': False}):
            result = runtime_check.check(['pdf', 'semantic'])
            self.assertFalse(result['passed'])
            self.assertEqual(result['unavailable_required_groups'], ['pdf', 'semantic'])

    def test_doctor_does_not_import_model_runtime(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; sys.path.insert(0,sys.argv[1]); import runtime_check; runtime_check.check(); '
            'assert not ({"torch","transformers"} & sys.modules.keys())', str(TOOLS)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_failure_has_receipt_and_closes_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            value = runner.run_task(d, 'fixture only', d / 'out', command=[str(d / 'absent.exe')])
            self.assertFalse(value['passed'])
            self.assertEqual(value['task_status'], 'not_started')
            self.assertTrue((d / 'out/result.json').is_file())
            # Windows refuses rename on open handles; this verifies failure cleanup.
            for file in ('events.jsonl', 'stderr.txt'):
                (d / 'out' / file).rename(d / (file + '.closed'))

    def test_missing_cli_leaves_no_empty_output_folder(self):
        with tempfile.TemporaryDirectory() as d, patch.object(runner.shutil, 'which', return_value=None):
            d = Path(d).resolve()
            with self.assertRaises(FileNotFoundError):
                runner.run_task(d, 'fixture only', d / 'out')
            self.assertFalse((d / 'out').exists())

    def test_invalid_function_timeout_rejected_before_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            with self.assertRaises(ValueError):
                runner.run_task(d, 'fixture only', d / 'out', timeout=0, command=['unused'])
            self.assertFalse((d / 'out').exists())

    def test_dispatch_restores_interpreter_state(self):
        argv, paths = sys.argv[:], sys.path[:]
        with patch.object(dispatcher.runpy, 'run_path', return_value={}):
            self.assertEqual(dispatcher.main(['core', '--help']), 0)
        self.assertEqual(sys.argv, argv)
        self.assertEqual(sys.path, paths)

    def test_missing_dispatch_target_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as d, patch.object(dispatcher, 'TOOLS', Path(d).resolve()):
            self.assertEqual(dispatcher.main(['core']), 1)

    def test_all_python_sources_parse(self):
        for path in SKILL.rglob('*.py'):
            with self.subTest(path=str(path)):
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))

    def test_public_format_preset_works_without_private_template(self):
        self.assertFalse((TOOLS / 'assets/学位论文默认格式/格式.docx').exists())
        self.assertTrue((TOOLS / 'references/学位论文默认格式_沈阳农业大学本地版.md').is_file())
        self.assertTrue((SKILL / 'LICENSE').is_file())

    def test_release_manifest_verifies(self):
        # During a build this checks payload consistency, not install approval.
        self.assertTrue(installer.verify(require_complete=False)['files'])

    def test_install_into_new_directory_is_exact(self):
        with tempfile.TemporaryDirectory() as d,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            value = installer.install(d)
            self.assertTrue(value['passed'])
            installed = Path(value['installed'])
            for source in SKILL.rglob('*'):
                if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                    self.assertEqual(core.sha(source), core.sha(installed / source.relative_to(SKILL)))

    def test_existing_install_is_preserved(self):
        with tempfile.TemporaryDirectory() as d,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            existing = Path(d).resolve() / installer.NAME
            existing.mkdir()
            marker = existing / 'user.txt'
            marker.write_text('preserve me', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                installer.install(d)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve me')

    def test_tampered_release_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            shutil.copytree(RELEASE, d / 'release', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (d / 'release/research-evidence-workflow/SKILL.md').write_text('modified', encoding='utf-8')
            with self.assertRaises(ValueError):
                installer.verify(d / 'release')

if __name__ == '__main__':
    unittest.main(verbosity=2)
