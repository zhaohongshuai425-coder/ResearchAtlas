"""Completed synthetic input for installer tests run during an unfinished build."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,shutil,tempfile

@contextmanager
def completed_release(source):
    with tempfile.TemporaryDirectory(prefix='Synthetic completed installer input ') as temporary:
        root=Path(temporary)/'fixture'
        shutil.copytree(source,root,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        version=json.loads((root/'release_metadata.json').read_text(encoding='utf-8'))['version']
        report={'passed':True,'version':version,'completion_state':'complete','installable':True,
                'fixture_only':True,'tests_performed':False,'scope':'Synthetic completed input for installer behavior tests, not actual release acceptance.'}
        (root/'build_report.json').write_text(json.dumps(report),encoding='utf-8')
        files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='manifest.json'}
        (root/'manifest.json').write_text(json.dumps({'schema':'research-skill-release/1.0','version':version,'files':files}),encoding='utf-8')
        yield root
