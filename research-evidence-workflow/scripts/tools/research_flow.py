"""Local research checkpoints, provenance checks and portable handoffs.

This tool records work; it does not perform searches, certify reading, select
scientific hypotheses, run training, or infer scientific validity from files.
"""
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from collections import defaultdict
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile

import core
from source_snapshot import archive_copy

SCHEMA = 'research-flow/1.0'
STAGES = [
    ('scope', '明确方向与可行性', {'feasibility'}),
    ('search', '搜集文献与覆盖复核', {'search_log'}),
    ('library', '原文与文献登记', {'literature_registry'}),
    ('graph_initial', '初建知识图谱', {'graph'}),
    ('reading', '筛选与精读', {'reading_notes'}),
    ('graph_review', '补充并核验图谱', {'graph', 'audit'}),
    ('comparison', '全库比较与诊断', {'comparison'}),
    ('ideas', '改进或创新候选', {'proposal'}),
    ('design', '数据审核与验证设计', {'protocol'}),
    ('execution', '执行授权验证', {'result'}),
    ('decision', '结果解释与取舍', {'decision'}),
    ('writing_plan', '规范与写作准备', {'writing_plan'}),
    ('writing', '写作核查与归档', {'manuscript', 'audit'}),
]
GATES = {key: kinds for key, _, kinds in STAGES}
KINDS = set().union(*GATES.values()) | {'other'}
STATUSES = {'pending', 'in_progress', 'complete', 'needs_review', 'skipped'}
VALIDATION_MODES = {'theory', 'measurement', 'simulation', 'empirical', 'synthesis', 'other'}


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(dump(value))
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def read_state(path):
    value = core.load(path)
    if value.get('schema') != SCHEMA:
        raise ValueError('Unsupported research state schema')
    if set(value.get('stages', {})) != set(GATES):
        raise ValueError('Research stages missing or unknown')
    for key, item in value['stages'].items():
        if item.get('status') not in STATUSES:
            raise ValueError('Unknown stage status: ' + key)
    ids = [a['id'] for a in value.get('artifacts', [])]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate artifact ID')
    if 'validation_route' in value:
        route = value['validation_route']
        if (route.get('schema') != 'research-validation-route/1.0' or not route.get('modes')
                or not set(route['modes']) <= VALIDATION_MODES
                or not str(route.get('reason', '')).strip()
                or not str(route.get('expected_evidence', '')).strip()):
            raise ValueError('Invalid explicit validation route')
    return value


@contextmanager
def locked(path):
    legacy = Path(path).with_name('.research_flow.lock')
    if legacy.exists():raise FileExistsError('A legacy writer lock exists; verify its owner before using this version')
    from file_commit import locked as os_locked
    entered=False
    try:
        with os_locked(Path(path).with_name('.research_flow_v2.lock')):
            entered=True
            if legacy.exists():raise FileExistsError('A legacy writer appeared; do not mix writer versions')
            yield
    except OSError as exc:
        if not entered:raise FileExistsError('Another writer owns this project lock') from exc
        raise


def commit(path, state):
    path = Path(path)
    old_bytes = path.read_bytes()
    old = core.load(path)
    old_hash = hashlib.sha256(old_bytes).hexdigest()
    history = path.parent / '.research_history'
    history.mkdir(exist_ok=True)
    snapshot = history / (f"r{old['revision']:06d}_{old_hash}.json")
    if snapshot.exists() and snapshot.read_bytes() != old_bytes:
        raise ValueError('Existing immutable history differs')
    if not snapshot.exists():
        snapshot.write_bytes(old_bytes)
    state['revision'] = old['revision'] + 1
    state['updated_at'] = now()
    atomic(path, state)


def initialize(folder, topic, project_id, existing=False):
    folder = Path(folder).resolve()
    if not topic.strip():
        raise ValueError('A nonempty research direction/title is required')
    core.qualified(project_id, 'check')
    if folder.exists() and not existing:
        raise FileExistsError('Use --existing explicitly to add state to an existing project')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'research_state.json'
    if path.exists():
        raise FileExistsError('Existing project state is preserved')
    state = {'schema': SCHEMA, 'project_id': project_id, 'topic': topic,
             'revision': 0, 'created_at': now(), 'updated_at': now(),
             'stages': {key: {'label': label, 'status': 'pending', 'reason': '',
                              'evidence_ids': []} for key, label, _ in STAGES},
             'artifacts': [], 'notes': [],
             'scope': 'New work record only; no literature, reading or experiment facts created.'}
    # The exclusive write also protects against two simultaneous initializers.
    with path.open('x', encoding='utf-8') as stream:
        stream.write(dump(state))
    return {'created': str(path), 'topic': topic, 'stages_recorded_complete': 0}


def artifact_checks(state, root):
    result = {}
    for item in state['artifacts']:
        try:
            path = core.inside(root, item['path'])
            status = ('missing' if not path.is_file() else
                      'intact' if core.sha(path) == item['sha256'] else 'changed')
        except ValueError:
            status = 'outside_root'
        result[item['id']] = {'path': item['path'], 'status': status, 'kind': item['kind']}
    return result


def check_state(path):
    state = read_state(path)
    checks = artifact_checks(state, Path(path).resolve().parent)
    from handoff_dependencies import inspect
    dependencies = inspect(Path(path).resolve().parent, state['artifacts'])
    issues = []
    stages = {}
    for key, item in state['stages'].items():
        effective = item['status']
        if item['status'] in {'complete', 'skipped'} and not item.get('reason', '').strip():
            issues.append({'stage': key, 'problem': 'Missing completion/skip rationale'})
            effective = 'needs_review'
        if item['status'] == 'complete':
            refs = item.get('evidence_ids', [])
            intact = [checks[r] for r in refs if r in checks and checks[r]['status'] == 'intact']
            if not refs or len(intact) != len(refs) or not GATES[key] <= {r['kind'] for r in intact}:
                issues.append({'stage': key, 'problem': 'Missing, changed or insufficient registered deliverables'})
                effective = 'needs_review'
        stages[key] = {**item, 'effective_status': effective}
    completed = sum(x['effective_status'] == 'complete' for x in stages.values())
    skipped = sum(x['effective_status'] == 'skipped' for x in stages.values())
    pending = [key for key, value in stages.items() if value['effective_status'] not in {'complete', 'skipped'}]
    return {'passed': not issues and all(r['status'] == 'intact' for r in checks.values()) and dependencies['files_intact'],
            'handoff_ready': dependencies['passed'], 'dependencies': dependencies,
            'project_id': state['project_id'], 'topic': state['topic'], 'revision': state['revision'],
            'validation_route': state.get('validation_route'),
            'stage_records_complete': completed, 'stage_records_skipped': skipped, 'stage_records_total': len(STAGES),
            'next_unfinished_stage': pending[0] if pending else None,
            'stages': stages, 'artifacts': checks, 'issues': issues, 'notes': state['notes'],
            'scope': 'Checks file bindings and recorded milestones, not reading quality or scientific validity.'}


def register_artifact(path, file, kind, identity, description):
    if kind not in KINDS or not description.strip():
        raise ValueError('Known artifact kind and nonempty description required')
    with locked(path):
        state = read_state(path)
        if not identity.strip() or any(a['id'] == identity for a in state['artifacts']):
            raise ValueError('Artifact ID must be new and nonempty; retain old records')
        root = Path(path).resolve().parent
        source = core.inside(root, file)
        if not source.is_file() or source == Path(path).resolve():
            raise ValueError('Register a real project deliverable, not the live state itself')
        item = {'id': identity, 'path': source.relative_to(root).as_posix(), 'kind': kind,
                'sha256': core.sha(source), 'description': description, 'registered_at': now()}
        state['artifacts'].append(item)
        commit(path, state)
    return item


def add_note(path, kind, text):
    if not text.strip():
        raise ValueError('Empty note is not a decision or next action')
    with locked(path):
        state = read_state(path)
        item = {'id': f"N{len(state['notes']) + 1:05d}", 'kind': kind, 'text': text, 'created_at': now()}
        state['notes'].append(item)
        commit(path, state)
    return item


def configure_route(path, modes, reason, expected_evidence):
    if (not modes or len(set(modes)) != len(modes) or not set(modes) <= VALIDATION_MODES
            or not reason.strip() or not expected_evidence.strip()):
        raise ValueError('Explicit validation modes, rationale and expected evidence are required')
    with locked(path):
        state = read_state(path)
        previous = state.get('validation_route')
        if previous and all(previous[key] == value for key, value in
                            [('modes', modes), ('reason', reason), ('expected_evidence', expected_evidence)]):
            return previous
        route = {'schema': 'research-validation-route/1.0', 'modes': list(modes),
                 'reason': reason, 'expected_evidence': expected_evidence, 'updated_at': now(),
                 'scope': 'A declared validation plan; no experiment, proof, review or stage completion implied.'}
        state['validation_route'] = route
        # A substantive route change requires reconsidering downstream work;
        # immutable state history preserves any previous completion record.
        if previous:
            for key in ('design', 'execution', 'decision', 'writing_plan', 'writing'):
                if state['stages'][key]['status'] == 'complete':
                    state['stages'][key].update(status='needs_review', reason='Validation route changed; reassess applicable evidence')
        commit(path, state)
    return route


def set_stage(path, key, status, reason, evidence):
    if key not in GATES or status not in STATUSES:
        raise ValueError('Unknown stage or status')
    if status in {'complete', 'skipped'} and not reason.strip():
        raise ValueError('Completion or skip requires a substantive rationale')
    with locked(path):
        state = read_state(path)
        if any(e not in {a['id'] for a in state['artifacts']} for e in evidence):
            raise ValueError('Unregistered artifact reference')
        state['stages'][key].update(status=status, reason=reason, evidence_ids=evidence, updated_at=now())
        if status == 'complete':
            checks = artifact_checks(state, Path(path).resolve().parent)
            intact = [checks[e] for e in evidence if checks[e]['status'] == 'intact']
            if len(intact) != len(evidence) or not GATES[key] <= {e['kind'] for e in intact}:
                raise ValueError('Completion needs unchanged registered deliverables of required kinds: ' + ', '.join(sorted(GATES[key])))
        commit(path, state)
    return state['stages'][key]


def graph_audit(graph_path, root):
    graph = core.load(graph_path)
    report = core.validate(graph, Path(root).resolve(), True)
    labels = defaultdict(list)
    claims = defaultdict(set)
    locators = []
    pending = []
    inherited = []
    review_states = defaultdict(list)
    for node in graph.get('nodes', []):
        labels[(node.get('type'), node.get('label', '').strip().casefold())].append(node['id'])
    for edge in graph.get('edges', []):
        if any(not r.get('locator') for r in edge.get('source_refs', [])):
            locators.append(edge['id'])
        if edge.get('relation') in {'supports', 'challenges'}:
            claims[edge.get('to')].add(edge['relation'])
            review_state = str(edge.get('review_status') or '')
            review_states[review_state].append(edge['id'])
            if review_state.casefold() in {'', 'pending', 'unknown', 'unreviewed', 'needs_review', 'todo'}:
                pending.append(edge['id'])
            elif review_state.casefold().startswith('inherited'):
                inherited.append(edge['id'])
    byte_impact = core.impact(graph, Path(root).resolve())
    affected = set(byte_impact['direct_entities'])
    adjacency = defaultdict(set)
    for edge in graph.get('edges', []):
        adjacency[edge.get('from')].add(edge.get('to'))
    pending_nodes = list(affected)
    while pending_nodes:
        for target in adjacency[pending_nodes.pop()]:
            if target not in affected:
                affected.add(target)
                pending_nodes.append(target)
    return {**report, 'duplicate_label_review': [ids for ids in labels.values() if len(ids) > 1],
            'mixed_evidence_review': sorted(k for k, v in claims.items() if len(v) > 1),
            'relations_without_locators': locators, 'evidence_review_pending': pending,
            'evidence_review_inherited': inherited, 'declared_evidence_review_states': dict(review_states),
            'byte_change_impact': byte_impact, 'transitive_review_queue': sorted(affected),
            'automatic_merge': False, 'scientific_conflict_certified': False,
            'scope': 'Mixed evidence and repeated labels are review leads, not automatic contradictions or duplicates.'}


def normalized_value(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('A fact needs an explicit non-boolean value')
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite reported value')
        return ('number', number)
    except InvalidOperation:
        return ('text', str(value))


def check_facts(file, root):
    data = core.load(file)
    if data.get('schema') != 'research-facts/1.0':
        raise ValueError('Unsupported fact ledger schema')
    issues, warnings = [], []
    seen = set()
    for fact in data.get('facts', []):
        identity = fact.get('id')
        if not identity or identity in seen:
            issues.append({'id': identity, 'problem': 'Missing or duplicated fact ID'})
        seen.add(identity)
        expected = fact.get('expected', {})
        if not fact.get('definition') or 'unit' not in expected or not expected.get('context'):
            issues.append({'id': identity, 'problem': 'Missing definition, unit or scope/context'})
        try:
            value = normalized_value(expected.get('value'))
        except ValueError as exc:
            issues.append({'id': identity, 'problem': str(exc)})
            value = None
        bindings = [('evidence', fact.get('evidence', {}))]
        mentions = fact.get('mentions', [])
        if not mentions:
            issues.append({'id': identity, 'problem': 'No manuscript/figure mentions registered'})
        for mention in mentions:
            bindings.append(('mention', mention))
            try:
                different = (normalized_value(mention.get('value')) != value or
                             mention.get('unit') != expected.get('unit') or
                             mention.get('context') != expected.get('context'))
            except ValueError:
                different = True
            if different:
                issues.append({'id': identity, 'problem': 'Value/unit/context differs', 'path': mention.get('path')})
        for role, binding in bindings:
            try:
                if not binding.get('path') or not binding.get('locator') or not binding.get('sha256'):
                    raise ValueError('File, hash and evidence locator are required')
                path = core.inside(Path(root).resolve(), binding['path'])
                if not path.is_file() or core.sha(path) != binding['sha256']:
                    raise ValueError('Bound file missing or changed')
                excerpt = binding.get('excerpt')
                if excerpt and path.suffix.lower() in {'.md', '.txt', '.csv', '.json'}:
                    if excerpt not in path.read_text(encoding='utf-8-sig'):
                        raise ValueError('Registered excerpt not found')
                else:
                    warnings.append({'id': identity, 'role': role, 'problem': 'Locator/extraction needs substantive review'})
            except (ValueError, OSError) as exc:
                issues.append({'id': identity, 'role': role, 'problem': str(exc)})
    if not seen:
        issues.append({'problem': 'Empty ledger cannot certify a manuscript'})
    return {'passed': not issues, 'facts': len(seen), 'issues': issues, 'warnings': warnings,
            'automatic_unit_conversion': False, 'extraction_certified': False,
            'scope': 'Compares explicitly extracted fact records and bound bytes; semantic, formula, citation and visual review remain required.'}


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or '..' in path.parts or
            any(':' in part for part in path.parts) or path.as_posix() != name):
        raise ValueError('Unsafe package member: ' + name)
    return name


def verify_package(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        for info in z.infolist():
            safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Symlink archive member')
        manifest = json.loads(z.read('package_manifest.json'))
        if manifest.get('schema') != 'research-handoff/1.0':
            raise ValueError('Unsupported handoff schema')
        expected = manifest['files']
        if set(names) != set(expected) | {'package_manifest.json'}:
            raise ValueError('Archive entries differ from manifest')
        for name, digest in expected.items():
            h = hashlib.sha256()
            with z.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != digest:
                raise ValueError('Archive hash mismatch: ' + name)
    return {'passed': True, 'files': len(expected), 'omitted_artifacts': manifest['omitted_artifacts'],
            'metadata_only': manifest.get('metadata_only'),
            'evidence_dependencies_complete': manifest.get('dependency_check', {}).get('passed'),
            'dependency_check': manifest.get('dependency_check'),
            'scope': 'Byte-integrity verification, not scientific validation.'}


def pack(path, output, include_artifacts=False):
    path = Path(path).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Preserve existing handoff package')
    with locked(path):
        state = read_state(path)
        status = check_state(path)
        if include_artifacts and not status['dependencies']['passed']:
            from handoff_dependencies import IncompleteHandoff
            raise IncompleteHandoff(status['dependencies'])
        files = {'project/research_state.json': path}
        for snapshot in sorted((path.parent / '.research_history').glob('*.json')):
            snapshot = core.inside(path.parent, snapshot)
            files['project/.research_history/' + snapshot.name] = snapshot
        if include_artifacts:
            for item in state['artifacts']:
                if status['artifacts'][item['id']]['status'] != 'intact':
                    raise ValueError('Artifact changed/missing; export metadata only or resolve registered versions')
                files['project/' + safe_member(item['path'])] = core.inside(path.parent, item['path'])
        base = Path(__file__).resolve().parent
        for relative in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'research_flow.py', 'handoff_dependencies.py', 'research_brief.py', 'runtime_check.py', 'file_commit.py', 'source_snapshot.py', 'local_ingest.py', 'intake_sources.py', 'run_codex_task.py', 'core.py', 'literature_tools.py', 'corpus_tools.py', 'evidence_search.py', 'evidence_followup.py', 'evidence_quality.py', 'evidence_decisions.py', 'semantic_retrieval.py', 'query_translation.py', 'research_query.py', 'source_providers.py', 'receiver_check.py', 'prototype_acceptance.py', 'pdf_quality.py', 'review_workbench.py', 'pdf_ocr.py', 'windows_ocr.ps1',
                         'references/文献来源选择与上传接入.md',
                         'references/摘要读取与单次运行.md',
                         'references/原型本地验收与补读.md',
                         'references/证据审查与外部验收.md',
                         'references/泛化执行与质量评估.md',
                         'references/全库接入_解析质量_方法证据链.md', 'references/文献获取与原文证据检索.md', 'references/执行与验收规则.md',
                         'references/工作记录模板.json', 'references/学位论文默认格式_沈阳农业大学本地版.md'):
            item = base / relative
            if item.is_file():
                files['tools/' + relative] = item
        if output in [p.resolve() for p in files.values()]:
            raise ValueError('Output cannot overwrite a source')
        omitted = [] if include_artifacts else [a['path'] for a in state['artifacts']]
        text = '# 科研任务接续\n\n' + state['topic'] + '\n\n'
        text += '先使用tools/research_brief.py status --state <状态路径>核对当前文件与阶段摘要；按需再展开project/research_state.json及具体证据。\n'
        text += '按状态中的决定、问题和下一步续接；缺原件或字节变化的阶段须复核。阶段完成记录不代表科学验收。\n'
        text += '本包没有启动训练或后台监控。metadata_only=' + str(not include_artifacts) + '\n'
        from handoff_dependencies import inspect
        exported_dependencies = status['dependencies'] if include_artifacts else inspect(path.parent, state['artifacts'], [])
        text += '证据依赖随包完整=' + str(exported_dependencies['passed']) + '；详见package_manifest.json的dependency_check。\n'
        text += '接收后运行：python -B tools/receiver_check.py --folder .\n'
        text += '未打包的登记成果：\n' + '\n'.join('- ' + p for p in omitted) + '\n\n'
        text += '阶段记录：\n' + '\n'.join('- ' + v['label'] + ': ' + v['effective_status'] for v in status['stages'].values()) + '\n\n'
        text += '决定、问题与下一步：\n' + '\n'.join('- ' + n['kind'] + ': ' + n['text'] for n in state['notes']) + '\n'
        blobs = {'handoff.md': text.encode('utf-8')}
        digests = {n: core.sha(p) for n, p in files.items()}
        digests.update({n: hashlib.sha256(b).hexdigest() for n, b in blobs.items()})
        manifest = {'schema': 'research-handoff/1.0', 'project_id': state['project_id'],
                    'created_at': now(), 'files': digests, 'omitted_artifacts': omitted,
                    'metadata_only': not include_artifacts, 'dependency_check': exported_dependencies}
        output.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
                created = True
                for name, source in files.items():
                    safe_member(name)
                    z.write(source, name)
                for name, blob in blobs.items():
                    z.writestr(name, blob)
                z.writestr('package_manifest.json', dump(manifest))
            result = verify_package(output)
        except BaseException:
            if created and output.exists():
                output.unlink()
            raise
    return {**result, 'package': str(output), 'evidence_dependencies_complete': exported_dependencies['passed'],
            'dependency_check': exported_dependencies}


def restore(package, folder):
    folder = Path(folder).resolve()
    package = Path(package).resolve()
    folder.parent.mkdir(parents=True,exist_ok=True)
    from file_commit import locked as restore_lock
    key = hashlib.sha256(folder.name.encode('utf-8')).hexdigest()[:20]
    with restore_lock(folder.parent/('.restore-'+key+'.lock')):
        if folder.exists():
            raise FileExistsError('Restore only into a new directory')
        with archive_copy(package,folder.parent) as snapshot:
            report = verify_package(snapshot.path)
            stage = Path(tempfile.mkdtemp(prefix='.research-restore-'+key+'-',dir=folder.parent))
            try:
                with zipfile.ZipFile(snapshot.path) as z:
                    manifest = json.loads(z.read('package_manifest.json'))
                    for info in z.infolist():
                        safe_member(info.filename)
                        target = core.inside(stage, info.filename)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(info) as source, target.open('xb') as dest:
                            shutil.copyfileobj(source, dest, length=1024 * 1024)
                if core.sha(snapshot.path) != snapshot.sha256:
                    raise ValueError('Private archive snapshot changed during restore')
                for name,digest in manifest['files'].items():
                    target = core.inside(stage,name)
                    if not target.is_file() or core.sha(target) != digest:
                        raise ValueError('Restored file differs from the verified package: '+name)
                if folder.exists():
                    raise FileExistsError('Restore preserves a target created during extraction')
                stage.rename(folder)
            except BaseException:
                if stage.exists() and stage.resolve().parent == folder.parent and not stage.is_symlink():
                    try:shutil.rmtree(stage)
                    except OSError:pass
                raise
    return {**report, 'restored': str(folder), 'state': str(folder / 'project/research_state.json'),
            'staged_restore':True,'input_snapshot_sha256':snapshot.sha256,
            'cleanup_warnings':snapshot.cleanup_warnings,'cleanup_pending':bool(snapshot.cleanup_warnings),
            'next': 'Recheck restored bindings; omitted sources must be restored separately.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('init')
    p.add_argument('folder'); p.add_argument('--topic', required=True); p.add_argument('--id', required=True)
    p.add_argument('--existing', action='store_true')
    for name in ('status', 'artifact', 'note', 'stage', 'pack', 'route'):
        p = commands.add_parser(name); p.add_argument('--state', required=True)
        if name == 'artifact':
            p.add_argument('--file', required=True); p.add_argument('--kind', choices=sorted(KINDS), required=True)
            p.add_argument('--id', required=True); p.add_argument('--description', required=True)
        elif name == 'note':
            p.add_argument('--kind', choices=['decision', 'question', 'constraint', 'next_action', 'scope_change'], required=True)
            p.add_argument('--text', required=True)
        elif name == 'stage':
            p.add_argument('--id', choices=list(GATES), required=True)
            p.add_argument('--status', choices=sorted(STATUSES), required=True)
            p.add_argument('--reason', default=''); p.add_argument('--evidence', action='append', default=[])
        elif name == 'pack':
            p.add_argument('--output', required=True); p.add_argument('--include-artifacts', action='store_true')
        elif name == 'route':
            p.add_argument('--mode', action='append', choices=sorted(VALIDATION_MODES), required=True)
            p.add_argument('--reason', required=True); p.add_argument('--expected-evidence', required=True)
    p = commands.add_parser('restore'); p.add_argument('package'); p.add_argument('folder')
    p = commands.add_parser('verify-pack'); p.add_argument('package')
    p = commands.add_parser('audit-graph'); p.add_argument('--graph', required=True); p.add_argument('--root', required=True)
    p = commands.add_parser('check-facts'); p.add_argument('--file', required=True); p.add_argument('--root', required=True)
    args = parser.parse_args()
    if args.command == 'init':
        out = initialize(args.folder, args.topic, args.id, args.existing)
    elif args.command == 'status':
        out = check_state(args.state)
    elif args.command == 'artifact':
        out = register_artifact(args.state, args.file, args.kind, args.id, args.description)
    elif args.command == 'note':
        out = add_note(args.state, args.kind, args.text)
    elif args.command == 'stage':
        out = set_stage(args.state, args.id, args.status, args.reason, args.evidence)
    elif args.command == 'route':
        out = configure_route(args.state, args.mode, args.reason, args.expected_evidence)
    elif args.command == 'pack':
        out = pack(args.state, args.output, args.include_artifacts)
    elif args.command == 'restore':
        out = restore(args.package, args.folder)
    elif args.command == 'verify-pack':
        out = verify_package(args.package)
    elif args.command == 'audit-graph':
        out = graph_audit(args.graph, args.root)
    else:
        out = check_facts(args.file, args.root)
    print(dump(out), end='')
    return 1 if out.get('passed') is False else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print(dump({'passed': False, 'error': str(exc),
                    **({'dependency_check': exc.report} if hasattr(exc, 'report') else {})}), end='')
        raise SystemExit(1)
