"""Bounded intake candidates and batch-owned, hashed ZIP snapshots."""
from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib,os,stat,tempfile,zipfile

class CountBudgetError(ValueError):pass
class ByteBudgetError(ValueError):pass

def bounded_read(stream,budget):
    data=stream.read(budget+1)
    if len(data)>budget:raise ByteBudgetError('File exceeds byte budget')
    return data

def zip_name(name):
    p=PurePosixPath(name.replace('\\','/'))
    if p.is_absolute() or PureWindowsPath(name).drive or '..' in p.parts:raise ValueError('Archive member has an invalid path')
    return p.as_posix()

def fingerprint(value):
    # Windows can expose different creation-time semantics through stat/fstat.
    # Content identity is bound by the snapshot SHA; compare portable file
    # identity, length and modification time for the live-source change check.
    return tuple(getattr(value,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns'))

def failure_reader(error):
    def read():raise error
    return read

class CandidateBatch(list):
    def __init__(self):
        super().__init__();self.stack=ExitStack();self.folder=None
        self.snapshot_bytes=0;self.snapshots_created=0;self.snapshot_attempts=0;self.cleanup_warnings=[]
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        try:self.stack.close()
        except OSError as exc:self.cleanup_warnings.append(str(exc))
    def snapshot(self,path,budget):
        if self.folder is None:self.folder=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='research-intake-')))
        if path.stat().st_size>budget-self.snapshot_bytes:raise ByteBudgetError('Archive snapshot byte budget exceeded')
        remaining=budget-self.snapshot_bytes
        target=self.folder/f'archive-{self.snapshot_attempts}.zip';self.snapshot_attempts+=1
        h=hashlib.sha256();size=0
        with path.open('rb') as source,target.open('xb') as dest:
            before=fingerprint(os.fstat(source.fileno()))
            for block in iter(lambda:source.read(1024*1024),b''):
                size+=len(block)
                if size>remaining:raise ByteBudgetError('Archive snapshot byte budget exceeded')
                dest.write(block);h.update(block);self.snapshot_bytes+=len(block)
            if fingerprint(os.fstat(source.fileno()))!=before or fingerprint(path.stat())!=before:
                raise ValueError('Archive changed while creating its batch snapshot')
        self.snapshots_created+=1
        archive=self.stack.enter_context(zipfile.ZipFile(target))
        return archive,{'path':str(path),'sha256':h.hexdigest()},before

def input_paths(inputs):
    seen=set()
    for entry in inputs:
        p=Path(entry).resolve()
        if p.is_dir():
            for folder,dirs,files in os.walk(p):
                dirs[:]=sorted(n for n in dirs if not (Path(folder)/n).is_symlink())
                for name in sorted(files):
                    child=Path(folder)/name
                    if child.is_symlink() or child.suffix.lower() not in ('.pdf','.zip'):continue
                    if child not in seen:seen.add(child);yield child
        elif p not in seen:seen.add(p);yield p

def candidates(inputs,max_files,max_file,total_budget):
    rows=CandidateBatch();used=0
    def add(locator,reader,size=0,container=None,member=None):
        nonlocal used
        if len(rows)>=max_files:raise CountBudgetError('Input count exceeds declared budget; no intake has started')
        if size>max_file or used+size>total_budget:raise ByteBudgetError('Input byte budget exceeded')
        used+=size;rows.append((locator,reader,container,member))
    def error(locator,exc,container=None,member=None):add(locator,failure_reader(exc),0,container,member)
    try:
        for path in input_paths(inputs):
            if len(rows)>=max_files:raise CountBudgetError('Input count exceeds declared budget; no intake has started')
            if not path.is_file():error(str(path),FileNotFoundError('Input does not exist'));continue
            if path.suffix.lower()=='.zip':
                try:archive,container,original=rows.snapshot(path,total_budget)
                except (OSError,ValueError,zipfile.BadZipFile) as exc:error(str(path),ValueError(str(exc)));continue
                seen=set()
                for info in archive.infolist():
                    if info.is_dir():continue
                    locator=str(path)+'!'+info.filename
                    try:
                        name=zip_name(info.filename)
                        if name in seen:raise ValueError('Duplicate archive member name')
                        seen.add(name)
                        if stat.S_ISLNK(info.external_attr>>16):raise ValueError('Archive symlink is not imported')
                        if not name.lower().endswith('.pdf'):continue
                        if info.flag_bits&1:raise ValueError('Encrypted PDF member requires user preparation')
                        def reader(p=path,z=archive,member=info,expected=original):
                            if fingerprint(p.stat())!=expected:raise ValueError('Archive changed during intake; fixed snapshot was not mixed with a new version')
                            with z.open(member) as stream:return bounded_read(stream,max_file)
                        add(locator,reader,info.file_size,container,name)
                    except CountBudgetError:raise
                    except (OSError,ValueError) as exc:error(locator,ValueError(str(exc)),container,info.filename)
            elif path.suffix.lower()=='.pdf':
                def reader(p=path):
                    with p.open('rb') as stream:return bounded_read(stream,max_file)
                try:add(str(path),reader,path.stat().st_size)
                except ByteBudgetError as exc:error(str(path),exc)
            else:error(str(path),ValueError('Only PDF, folder or ZIP is supported'))
        return rows
    except BaseException:
        rows.close();raise
