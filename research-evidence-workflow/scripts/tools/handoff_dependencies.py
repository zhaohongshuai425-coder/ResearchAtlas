"""Read-only dependency closure for explicit research evidence protocols.

Only registered roots and their declared file bindings are inspected. No crawl,
registration, external-root reads, model loading or scientific certification.
"""
from collections import deque
from pathlib import Path
import json
import sqlite3
import core

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_FILES = 20000


def inspect(root, artifacts, included=None):
    root = Path(root).resolve()
    registered = {a['path'] for a in artifacts}
    included = registered if included is None else set(included)
    queue = deque((dict(a), '.', 'registered:' + a['id'], None) for a in artifacts)
    seen, files, issues, opaque, contexts = set(), {}, [], set(), []

    def historical_context(data, owner):
        """Registry identity is provenance, not a passage dependency of a snapshot.

        Only use this exception after the original resolver has actually replayed
        every preserved candidate. Source paths still stay inside the project.
        """
        binding=data.get('source')
        if not isinstance(binding,dict) or not binding.get('path') or not binding.get('sha256'):
            raise ValueError('Historical query requires its original registry binding')
        path=core.inside(root,binding['path']);name=path.relative_to(root).as_posix()
        digest=core.sha(path) if path.is_file() else None
        if digest!=binding['sha256'] or name not in included:
            import evidence_search
            candidates=data.get('candidates')
            if not isinstance(candidates,list):raise ValueError('Historical query candidates must be a list')
            for candidate in candidates:
                evidence_search.resolve(core.inside(root,owner),candidate['candidate_id'],root)
            contexts.append({'query':owner,'path':name,'status':'missing' if digest is None else 'changed' if digest!=binding['sha256'] else 'not_included',
                'historical_sha256':binding['sha256'],'current_sha256':digest,'included':name in included,
                'required_for_passage_replay':False,'preserved_candidates_replayed':len(candidates),
                'ranking_replayed':False,'support_verified':False})

    def add(binding, base, owner, context=None):
        if not isinstance(binding, dict) or not binding.get('path') or not binding.get('sha256'):
            issues.append({'owner': owner, 'status': 'invalid_binding'})
        else:
            queue.append((binding, base, owner, context))

    def bindings(data, path, context):
        schema = data.get('schema')
        parent = Path(path).parent.as_posix()
        def emit(b, base='.', kind=None): add(b, base, path, kind)
        def versions():
            for b in data.get('review_input_versions', {}).values(): emit(b)
        if schema == 'research-kb/1.0':
            for b in data.get('sources', []): emit({'path': b.get('local_path'), 'sha256': b.get('sha256')})
            versions()
        elif schema == 'research-graph-commit/1.0':
            emit(data.get('graph')); versions()
        elif schema == 'research-literature/1.0':
            from literature_tools import library_dependencies
            for b in library_dependencies(data): emit(b, parent)
        elif schema == 'research-evidence-query/1.0':
            backend=data.get('backend');snapshot=data.get('source_snapshot')
            if backend not in {'library','corpus','graph'}:raise ValueError('Unsupported preserved query backend')
            preserved=backend=='library' or backend=='corpus' and isinstance(snapshot,dict) and snapshot.get('schema')=='research-corpus/1.0'
            if preserved:
                # resolve() consumes the preserved index and candidate bindings,
                # not an old mutable catalog or a new retrieval ranking.
                if backend=='corpus':emit(snapshot.get('search_index'))
            else:
                # Graph versions and legacy corpus queries remain strict.
                emit(data.get('source'))
            for c in data.get('candidates', []):
                base = c.get('source_root', '.')
                for b in c.get('source_bindings', []):
                    emit({'path':b.get('local_path'),'sha256':b.get('sha256')} if backend=='graph' else b,base)
                native=c.get('native',{})
                if backend=='library':
                    emit(native.get('source'),base);emit(native.get('index_binding'),base)
                elif backend=='corpus':emit(native.get('source_binding'),base)
            if preserved:historical_context(data,path)
        elif schema == 'research-corpus/1.0':
            emit(data.get('catalog'), kind='catalog')
            for b in data.get('metadata_bindings', []): emit(b)
            if data.get('search_index'): emit(data['search_index'])
        elif context == 'catalog':
            for d in data.get('documents', []):
                if d.get('metadata_binding'): emit(d['metadata_binding'])
                for b in d.get('reports', []):
                    if b.get('path') and b.get('sha256'): emit(b)
        elif schema == 'research-scoped-reading/1.0':
            for r in data.get('reviews', []):
                emit(r.get('query')); emit(r.get('inspection'))
                for b in r.get('visual_checks', []): emit(b)
        elif schema == 'research-claim-review/1.0':
            independent = data.get('independent_review')
            if independent:
                if not isinstance(independent, dict):
                    raise ValueError('Independent review must declare a report binding')
                emit(independent.get('report'))
            for part in data.get('parts', []):
                for c in part.get('citations', []):
                    emit(c.get('query'))
                    if c.get('visual_check'): emit(c['visual_check'])
        elif schema == 'research-semantic-index/1.0':
            emit(data.get('source'))
            for b in data.get('bindings', {}).values(): emit(b, parent)
            if isinstance(data.get('source_snapshot'), dict): bindings(data['source_snapshot'], path, None)
        elif schema not in {'research-independent-review/1.0'}:
            opaque.add(path)

    while queue:
        binding, base, owner, context = queue.popleft()
        try:
            path = core.inside(root, Path(base) / binding['path'])
            name = path.relative_to(root).as_posix()
        except (ValueError, TypeError, OSError) as exc:
            issues.append({'owner': owner, 'path': str(binding.get('path')), 'status': 'outside_or_invalid_root', 'error': str(exc)})
            continue
        # A later binding of the same original may declare an additional cache.
        # Follow that edge even when the original's bytes were checked earlier.
        if binding.get('cache'): add(binding['cache'], base, name)
        key = (name, binding.get('sha256'), context)
        if key in seen: continue
        seen.add(key)
        if len(seen) > MAX_FILES:
            issues.append({'owner': owner, 'status': 'dependency_scan_limit', 'limit': MAX_FILES}); break
        status = 'missing' if not path.is_file() else 'intact' if core.sha(path) == binding['sha256'] else 'changed'
        files[name] = {'path': name, 'status': status, 'registered': name in registered, 'included': name in included}
        if status != 'intact':
            issues.append({'owner': owner, 'path': name, 'status': status}); continue
        if name not in included:
            issues.append({'owner': owner, 'path': name, 'status': 'not_included' if name in registered else 'unregistered_dependency'})
        if path.suffix.lower() != '.json' and context != 'catalog': continue
        if path.stat().st_size > MAX_JSON_BYTES:
            issues.append({'owner': owner, 'path': name, 'status': 'dependency_scan_limit', 'limit_bytes': MAX_JSON_BYTES}); continue
        try:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(value, dict): bindings(value, name, context)
            else: opaque.add(name)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            issues.append({'owner': name, 'status': 'invalid_dependency_document', 'error': str(exc)})
    issues = sorted({json.dumps(i, sort_keys=True): i for i in issues}.values(), key=lambda i: (i.get('path', ''), i['status'], i['owner']))
    return {'schema': 'research-handoff-dependencies/1.0', 'passed': not issues,
            'files_intact': not any(i['status'] not in {'unregistered_dependency', 'not_included'} for i in issues),
            'files': sorted(files.values(), key=lambda v: v['path']), 'issues': issues,
            'unregistered_dependencies': sorted({i['path'] for i in issues if i['status'] == 'unregistered_dependency'}),
            'opaque_json_documents': sorted(opaque), 'automatic_registration': False,
            'context_warnings':contexts,
            'scope': 'Known evidence protocols and declared bindings only; opaque files are byte-checked, not interpreted. No scientific or model-runtime certification.'}


class IncompleteHandoff(ValueError):
    def __init__(self, report):
        self.report = report
        detail = '; '.join(i.get('path', i['owner']) + ': ' + i['status'] for i in report['issues'][:12])
        super().__init__('Incomplete evidence dependencies; explicitly register/restore the declared files before --include-artifacts: ' + detail)
