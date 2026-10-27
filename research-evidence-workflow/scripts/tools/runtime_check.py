"""Read-only runtime discovery. Does not install packages, fetch models or start Codex."""
from importlib import metadata, util
from pathlib import Path
import argparse
import json
import platform
import shutil
import sqlite3
import sys
import subprocess


def package(module, distribution=None):
    available = util.find_spec(module) is not None
    version = None
    if available:
        try:
            version = metadata.version(distribution or module)
        except metadata.PackageNotFoundError:
            pass
    return {'available': available, 'version': version, 'import_tested': False}


def check(required=(),probe=False,harness_cli=None):
    packages = {m: package(m, d) for m, d in (
        ('pdfplumber', 'pdfplumber'), ('pypdf', 'pypdf'), ('pypdfium2', 'pypdfium2'),
        ('numpy', 'numpy'), ('torch', 'torch'), ('transformers', 'transformers'),
        ('safetensors', 'safetensors'), ('sentencepiece','sentencepiece'), ('sacremoses','sacremoses'))}
    groups = {
        'core': {'available': sys.version_info >= (3, 10), 'note': 'Python 3.10+; SQLite ' + sqlite3.sqlite_version},
        'pdf': {'available': packages['pdfplumber']['available'] and packages['pypdfium2']['available'],
                'note': 'Recommended PDF stack; pypdf alone supports basic text extraction only.'},
        'semantic': {'available': all(packages[m]['available'] for m in ('numpy', 'torch', 'transformers', 'safetensors')),
                     'note': 'Packages detected only; a verified local pinned model and actual encode test are still needed.'},
        'translation': {'available':all(packages[m]['available'] for m in ('torch','transformers','sentencepiece','sacremoses')),
                        'note':'Optional query translation dependencies; pinned local weights and a real translation test are still required.'},
        'ocr': {'available': sys.platform == 'win32' and shutil.which('powershell') is not None
                and packages['pdfplumber']['available'],
                'note': 'Entry detected only; Windows OCR APIs and language packs need a real page test.'},
        'runner': {'available': shutil.which('codex') is not None,
                   'note': 'Entry detected only; login and app-server compatibility are not checked here.'},
        'opencode': {'available': shutil.which('opencode') is not None,
                     'note': 'Entry detected only; DeepSeek connection, model and permissions need an actual client test. Not required for chat instructions.'},
        'harness': {'available': bool(harness_cli and Path(harness_cli).is_file()) or shutil.which('dsh') is not None,
                    'note': 'DeepSeek Harness CLI entry detected only; plugins, model credentials and native Skill loading need an actual runtime test.'},
    }
    probes={}
    if probe:
        selected={'sqlite3'}
        if 'pdf' in required:selected.update(['pdfplumber','pypdfium2'])
        if 'semantic' in required:selected.update(['numpy','torch','transformers','safetensors'])
        if 'translation' in required:selected.update(['torch','transformers','sentencepiece','sacremoses'])
        for module in sorted(selected):
            try:
                result=subprocess.run([sys.executable,'-I','-c','import importlib;importlib.import_module('+repr(module)+')'],
                    capture_output=True,text=True,timeout=25)
                probes[module]={'passed':result.returncode==0,'exit_code':result.returncode,
                                'error':result.stderr[-500:] if result.returncode else None}
            except (OSError,subprocess.TimeoutExpired) as exc:probes[module]={'passed':False,'error':str(exc)[:300]}
    failures = sorted(name for name in {'core', *required} if not groups[name]['available'])
    return {'schema': 'research-runtime/1.0', 'passed': not failures and all(r['passed'] for r in probes.values()), 'read_only': True,
            'python': {'executable': sys.executable, 'version': platform.python_version()},
            'required_groups': ['core', *sorted(set(required) - {'core'})], 'unavailable_required_groups': failures,
            'groups': groups, 'packages': packages, 'network_used': False, 'model_started': False,
            'import_probes':probes,'harness_cli':str(harness_cli) if harness_cli else None,
            'scope': 'Dependency discovery is not an end-to-end feature acceptance test.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--require', action='append', default=[], choices=['core', 'pdf', 'semantic', 'translation', 'ocr', 'runner', 'opencode', 'harness'])
    p.add_argument('--probe',action='store_true',help='Actually import selected dependencies in bounded child processes; no model calls')
    p.add_argument('--harness-cli',help='Explicit existing Harness CLI launcher when it is not on PATH')
    a = p.parse_args()
    result = check(a.require,a.probe,a.harness_cli)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
