"""Verify, install, upgrade, recover and roll back a local Skill without replacing user edits."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, os, shutil, stat, sys, tempfile, uuid

ROOT=Path(__file__).resolve().parent;NAME='research-evidence-workflow'
sys.path.insert(0,str(ROOT/NAME/'scripts/tools'))
from file_commit import atomic_json, locked

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def tree(folder):
    folder=Path(folder)
    def reject_link(path):
        attributes=path.lstat()
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise ValueError('Linked/reparse Skill entries are not supported; installation preserved: '+str(path))
    try:reject_link(folder)
    except FileNotFoundError:return None
    if not folder.is_dir():return None
    result={}
    for p in folder.rglob('*'):
        reject_link(p)
        if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':result[p.relative_to(folder).as_posix()]=sha(p)
    return result

def skill_files(manifest):
    files=manifest['files'];prefix=NAME+'/'
    return {k[len(prefix):]:v for k,v in files.items() if k.startswith(prefix)} if any(k.startswith(prefix) for k in files) else files

def verify(root=None,require_complete=True):
    root=Path(root or ROOT).resolve();manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!='research-skill-release/1.0':raise ValueError('Unsupported release manifest')
    for name,digest in manifest['files'].items():
        path=(root/name).resolve();value=PurePosixPath(name)
        if value.is_absolute() or '..' in value.parts or not path.is_relative_to(root):raise ValueError('Invalid manifest path')
        if not path.is_file() or sha(path)!=digest:raise ValueError('Missing or changed release file: '+name)
    if 'release_metadata.json' in manifest['files']:
        metadata=json.loads((root/'release_metadata.json').read_text(encoding='utf-8'))
        if not isinstance(metadata.get('version'),str) or metadata['version']!=manifest.get('version'):raise ValueError('Manifest and release metadata versions differ')
        if metadata.get('name',NAME)!=NAME:raise ValueError('Release metadata names another Skill')
        if require_complete and 'build_report.json' not in manifest['files']:
            raise ValueError('Incomplete build: a completed build report is required for installation')
    if 'build_report.json' in manifest['files']:
        report=json.loads((root/'build_report.json').read_text(encoding='utf-8'))
        if report.get('passed') is not True:raise ValueError('Build was not accepted; do not install this output')
        if report.get('version',manifest.get('version'))!=manifest.get('version'):raise ValueError('Build and manifest versions differ')
        if require_complete and (report.get('completion_state','complete')!='complete' or report.get('installable',True) is not True):
            raise ValueError('Incomplete build: verification has not completed')
    if not (root/NAME/'SKILL.md').is_file() or tree(root/NAME)!=skill_files(manifest):raise ValueError('Unexpected or incomplete Skill files')
    return manifest

def resolve_parent(parent=None,target='codex',project=None):
    if target not in ('codex','opencode','agent','harness'):raise ValueError('Unknown install target')
    if parent is not None and project is not None:raise ValueError('Choose either skills-dir or project')
    if parent is not None:return Path(parent).expanduser().resolve()
    if project is not None:
        project=Path(project).expanduser().resolve()
        if not project.is_dir():raise ValueError('Use an existing project directory')
        return project/('.dsh' if target=='harness' else '.opencode' if target=='opencode' else '.agents')/'skills'
    if target=='opencode':return (Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config')).expanduser()/'opencode/skills').resolve()
    if target=='agent':return Path.home()/'.agents/skills'
    return Path(os.environ.get('DSH_HOME' if target=='harness' else 'CODEX_HOME',Path.home()/('.dsh' if target=='harness' else '.codex'))).expanduser().resolve()/'skills'

def locations(parent):
    parent=Path(parent).resolve();meta=parent/'.research-skill-releases'/NAME
    return parent,parent/NAME,meta,meta/'current.json',meta/'pending.json'

def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def checked_child(parent,value,prefix):
    path=(parent/value).resolve()
    if path.parent!=parent.resolve() or not path.name.startswith(prefix):raise ValueError('Invalid installation recovery path')
    return path

def _recover(parent):
    parent,dest,meta,current,journal=locations(parent)
    if not journal.exists():return None
    plan=load(journal)
    if plan.get('schema')!='research-skill-install-transaction/1.0':raise ValueError('Invalid install recovery journal')
    stage=checked_child(parent,plan['stage'],'.research-skill-stage-')
    backup=checked_child(meta/'backups',plan['backup'],'r-') if plan.get('backup') else None
    old,new=plan['before'],plan['after'];actual=tree(dest)
    staged=tree(stage/NAME)
    # A staged tree means publication has not happened, even when old/new
    # payload bytes happen to be identical (e.g. a metadata-only release).
    if staged is not None:
        if staged!=new['files']:raise ValueError('Staged upgrade changed; existing installation is preserved')
        if actual is not None:
            if old is None or actual!=old['files']:raise ValueError('User changes preserved; cannot resume replacement')
            if backup is None or backup.exists():raise ValueError('Conflicting installation backup')
            backup.parent.mkdir(parents=True,exist_ok=True)
            # Resolved targets are confined to this explicitly chosen Skill
            # parent or its private ledger, on the same filesystem.
            dest.rename(backup)
        elif old is not None and (backup is None or tree(backup)!=old['files']):raise ValueError('Missing verified old installation')
        (stage/NAME).rename(dest)
    else:
        if actual!=new['files']:raise ValueError('Published installation differs from the recovery plan')
        if old is not None and (backup is None or tree(backup)!=old['files']):raise ValueError('Published upgrade is missing its verified backup')
    atomic_json(current,new)
    journal.unlink()
    warnings=[]
    try:
        if stage.exists():stage.rmdir()
    except OSError as exc:warnings.append(str(exc))
    return {'passed':True,'installed':str(dest),'recovered':True,'version':new['version'],'files':len(new['files']),
            'cleanup_warnings':warnings,'cleanup_pending':bool(warnings)}

def status(parent):
    _,dest,_,current,journal=locations(parent);record=load(current) if current.exists() else None
    actual=tree(dest)
    return {'passed':record is None or actual==record['files'],'installed':str(dest),'exists':actual is not None,
        'managed':record is not None,'version':record.get('version') if record else None,
        'user_changes_detected':record is not None and actual!=record['files'],
        'pending_operation':journal.exists(),'backups':record.get('backups',[]) if record else []}

def _replace(parent,source,expected,version,old,action=None):
    parent,dest,meta,current,journal=locations(parent)
    if old is not None and old['files']==expected and old['version']==version:
        adopted=not current.exists()
        if adopted:atomic_json(current,{'schema':'research-skill-install/1.0',**old})
        return {'passed':True,'installed':str(dest),'recovered':False,'version':version,'files':len(expected),
                'unchanged':True,'adopted_matching_unmanaged_install':adopted,
                'existing_files_overwritten':False,'user_edits_overwritten':False,'network_used':False}
    stage=Path(tempfile.mkdtemp(prefix='.research-skill-stage-',dir=parent))
    shutil.copytree(source,stage/NAME,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if tree(stage/NAME)!=expected:raise ValueError('Staged files differ from the selected manifest; stage retained')
    backup='r-'+uuid.uuid4().hex if old else None
    backups=list(old.get('backups',[])) if old else []
    if old:backups.append({'id':backup,'version':old['version'],'files':old['files']})
    after={'schema':'research-skill-install/1.0','version':version,'files':expected,'backups':backups}
    plan={'schema':'research-skill-install-transaction/1.0','stage':stage.name,'backup':backup,'before':old,'after':after,'action':action}
    atomic_json(journal,plan)
    result=_recover(parent);result['recovered']=False
    result.update(existing_files_overwritten=old is not None,user_edits_overwritten=False,network_used=False)
    return result

def install(parent=None,target='codex',project=None,upgrade=False,baseline=None):
    manifest=verify();parent=resolve_parent(parent,target,project);parent.mkdir(parents=True,exist_ok=True)
    _,dest,meta,current,journal=locations(parent)
    with locked(meta/'operation.lock'):
        pending=load(journal) if journal.exists() else None
        recovered=_recover(parent) if pending else None
        old=load(current) if current.exists() else None
        actual=tree(dest)
        if actual is not None:
            resumed_fresh=(pending is not None and pending.get('before') is None and actual==skill_files(manifest) and pending['after']['version']==manifest.get('version'))
            if not upgrade and not resumed_fresh:raise FileExistsError('Skill already exists; use --upgrade after reviewing its version')
            if old is None:
                if baseline is None:raise ValueError('Existing Skill is unmanaged; provide the previous verified --baseline manifest or receipt')
                previous=load(baseline)
                old={'version':previous.get('version',previous.get('new_version','unknown')),
                     'files':skill_files(previous),'backups':[]}
            if actual!=old['files']:raise ValueError('User edits detected; installation preserved. Merge them before upgrading.')
        elif old is not None:raise ValueError('Managed installation is missing; use status/recovery before proceeding')
        result=_replace(parent,ROOT/NAME,skill_files(manifest),manifest.get('version','unknown'),old,
                        action={'kind':'upgrade' if upgrade else 'install'})
        result.update(target=target,requested_action_completed=True)
        if recovered:result['recovered_previous_transaction']=recovered
        return result

def rollback(parent,backup_id=None):
    parent,dest,meta,current,journal=locations(parent)
    with locked(meta/'operation.lock'):
        pending=load(journal) if journal.exists() else None
        recovered=_recover(parent) if pending else None
        previous_action=(pending or {}).get('action') or {}
        if recovered and not previous_action:
            return {**recovered,'passed':False,'requested_action_completed':False,'recovery_only':True,
                    'error':'Legacy pending transaction has no recorded action; inspect the recovered version and request an explicit rollback target'}
        if recovered and previous_action.get('kind')=='rollback' and (backup_id is None or backup_id==previous_action.get('backup_id')):
            return {**recovered,'rolled_back':True,'requested_action_completed':True,'resumed_same_rollback':True}
        old=load(current)
        if tree(dest)!=old['files']:raise ValueError('User edits detected; rollback will not overwrite them')
        choices=old.get('backups',[])
        selected=next((r for r in choices if r['id']==backup_id),None) if backup_id else (choices[-1] if choices else None)
        if selected is None:raise ValueError('No matching rollback backup')
        source=checked_child(meta/'backups',selected['id'],'r-')
        if tree(source)!=selected['files']:raise ValueError('Rollback backup changed')
        result=_replace(parent,source,selected['files'],selected['version'],old,
                        action={'kind':'rollback','backup_id':selected['id']})
        result.update(rolled_back=True,requested_action_completed=True)
        if recovered:result['recovered_previous_transaction']=recovered
        return result

def recover(parent):
    _,_,meta,_,_=locations(parent)
    with locked(meta/'operation.lock'):
        result=_recover(parent)
        if result is None:raise FileNotFoundError('No pending installation operation')
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',choices=['codex','opencode','agent','harness'],default='codex')
    loc=p.add_mutually_exclusive_group();loc.add_argument('--skills-dir');loc.add_argument('--project')
    action=p.add_mutually_exclusive_group();action.add_argument('--verify-only',action='store_true');action.add_argument('--upgrade',action='store_true')
    action.add_argument('--verify-content-only',action='store_true',help='Check payload bytes only; does not accept an unfinished build for installation')
    action.add_argument('--rollback',action='store_true');action.add_argument('--recover',action='store_true');action.add_argument('--status',action='store_true')
    p.add_argument('--baseline');p.add_argument('--backup-id');a=p.parse_args()
    try:
        parent=resolve_parent(a.skills_dir,a.target,a.project)
        if a.verify_only:out={'passed':True,'verified_files':len(verify()['files'])}
        elif a.verify_content_only:out={'passed':True,'verified_files':len(verify(require_complete=False)['files']),
                                      'verification_scope':'content_only_not_build_acceptance'}
        elif a.status:out=status(parent)
        elif a.recover:out=recover(parent)
        elif a.rollback:out=rollback(parent,a.backup_id)
        else:out=install(a.skills_dir,a.target,a.project,a.upgrade,a.baseline)
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['passed'] else 1
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'passed':False,'error':str(exc)},ensure_ascii=False));return 1
if __name__=='__main__':raise SystemExit(main())
