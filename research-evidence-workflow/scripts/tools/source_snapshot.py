"""Bind parsed JSON or an archive copy to the exact bytes consumed; stdlib only."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,os,tempfile


def read_json(path):
    content=Path(path).read_bytes()
    return json.loads(content.decode('utf-8-sig')),hashlib.sha256(content).hexdigest()


def fingerprint(value):
    return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns


class FileSnapshot:
    def __init__(self,path):
        self.path=path;self.sha256=None;self.cleanup_warnings=[]


@contextmanager
def archive_copy(source,parent):
    """Own a bounded-memory disk copy for both verification and extraction.

    This freezes the read version, not a publisher signature or authenticity.
    Later changes to the original path do not replace the verified copy.
    """
    source=Path(source).resolve();parent=Path(parent).resolve()
    folder=Path(tempfile.mkdtemp(prefix='.research-source-',dir=parent))
    snapshot=FileSnapshot(folder/'archive.snapshot')
    try:
        before=source.stat();digest=hashlib.sha256()
        with source.open('rb') as incoming,snapshot.path.open('xb') as outgoing:
            opened=os.fstat(incoming.fileno())
            if fingerprint(before)!=fingerprint(opened):raise ValueError('Archive changed before snapshot copy')
            for block in iter(lambda:incoming.read(1024*1024),b''):
                outgoing.write(block);digest.update(block)
            outgoing.flush();os.fsync(outgoing.fileno())
            if fingerprint(os.fstat(incoming.fileno()))!=fingerprint(opened):raise ValueError('Archive changed during snapshot copy')
        if fingerprint(source.stat())!=fingerprint(before):raise ValueError('Archive path changed during snapshot copy')
        snapshot.sha256=digest.hexdigest()
        yield snapshot
    finally:
        # Only remove this context's own file and empty directory. Publication
        # already completed by the caller must not become an apparent failure.
        for path in (snapshot.path,folder):
            try:
                if path==folder:path.rmdir()
                else:path.unlink(missing_ok=True)
            except OSError as exc:snapshot.cleanup_warnings.append(str(exc))
