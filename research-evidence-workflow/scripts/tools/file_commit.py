"""Recoverable two-file commits and OS-released local locks; standard library only."""
from contextlib import contextmanager
from pathlib import Path
import hashlib, json, os, tempfile

def digest(data): return hashlib.sha256(data).hexdigest()

@contextmanager
def locked(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()

def paths(output):
    output=Path(output).resolve()
    ident=digest(output.name.encode())[:20]
    return output,output.with_name(output.name+'.receipt.json'),output.parent/('.export-'+ident+'.pending.json'),output.parent/('.export-'+ident+'.lock')

def _publish(source,target):
    # Both paths are on the same volume. A hard link publishes the complete
    # bytes atomically and cannot overwrite an unrelated existing file.
    os.link(source,target)

def _finish(output,receipt,journal,plan):
    if plan.get('schema')!='research-pair-commit/1.0':raise ValueError('Invalid export recovery journal')
    stage=(output.parent/plan['stage']).resolve()
    if stage.parent!=output.parent or not stage.name.startswith('.export-stage-'):raise ValueError('Invalid export staging directory')
    if [r.get('target') for r in plan['files']]!=[output.name,receipt.name]:raise ValueError('Recovery targets differ')
    for row in plan['files']:
        if row['staged'] not in ('payload.tmp','receipt.tmp'):raise ValueError('Invalid staged file')
        source=stage/row['staged'];target=output.parent/row['target']
        if target.exists():
            if not target.is_file() or digest(target.read_bytes())!=row['sha256']:raise FileExistsError('Recovery preserves changed output: '+target.name)
        else:
            if not source.is_file() or digest(source.read_bytes())!=row['sha256']:raise ValueError('Staged export changed')
            _publish(source,target)
    # The matching receipt is the commit marker; a pending journal can be
    # safely replayed after any earlier publish step.
    # Publication is complete. A cleanup failure is an explicit warning,
    # retaining the journal so the same request or recover-only can retry it.
    try:
        for name in ('payload.tmp','receipt.tmp'):
            if (stage/name).exists():(stage/name).unlink()
        if stage.exists():stage.rmdir()
        journal.unlink()
    except OSError as exc:
        return {'committed':True,'cleanup_pending':True,'cleanup_warnings':[str(exc)]}
    return {'committed':True,'cleanup_pending':False,'cleanup_warnings':[]}

def commit_pair(output,payload,receipt_data,allow_identical=False):
    output,receipt,journal,lock=paths(output);output.parent.mkdir(parents=True,exist_ok=True)
    expected=[digest(payload),digest(receipt_data)]
    with locked(lock):
        if journal.exists():
            plan=json.loads(journal.read_text(encoding='utf-8'))
            if [r['sha256'] for r in plan.get('files',[])]!=expected:raise FileExistsError('A previous export is pending; recover it before changing the request')
            finished=_finish(output,receipt,journal,plan);return {'recovered':True,**finished}
        if output.exists() or receipt.exists():
            if (allow_identical and output.is_file() and receipt.is_file()
                    and [digest(output.read_bytes()),digest(receipt.read_bytes())]==expected):
                return {'recovered':False,'committed':True,'unchanged':True,'cleanup_pending':False,'cleanup_warnings':[]}
            raise FileExistsError('Preserve existing output and receipt')
        stage=Path(tempfile.mkdtemp(prefix='.export-stage-',dir=output.parent))
        try:
            for name,data in (('payload.tmp',payload),('receipt.tmp',receipt_data)):
                with (stage/name).open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            plan={'schema':'research-pair-commit/1.0','stage':stage.name,'files':[
                {'target':output.name,'staged':'payload.tmp','sha256':expected[0]},
                {'target':receipt.name,'staged':'receipt.tmp','sha256':expected[1]}]}
            atomic_json(journal,plan)
            finished=_finish(output,receipt,journal,plan)
        except BaseException:
            if not journal.exists():
                for name in ('payload.tmp','receipt.tmp'):
                    if (stage/name).exists():(stage/name).unlink()
                stage.rmdir()
            raise
    return {'recovered':False,**finished}

def recover_pair(output):
    output,receipt,journal,lock=paths(output)
    with locked(lock):
        if not journal.exists():raise FileNotFoundError('No pending export at this path')
        finished=_finish(output,receipt,journal,json.loads(journal.read_text(encoding='utf-8')))
    return {'passed':True,'recovered':True,'output':str(output),**finished}
