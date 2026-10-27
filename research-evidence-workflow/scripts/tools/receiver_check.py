"""Offline acceptance of a standard restored handoff or a legacy receiver bundle.

Automatic questions come from indexed text: functional controls, not independent
retrieval accuracy tests. Local semantic inference requires explicit --semantic.
No downloads, API calls, training or background jobs.
"""
from pathlib import Path
from importlib import util, metadata
import argparse,json,platform,sqlite3,sys,tempfile,zipfile
import core
import handoff_dependencies as deps


def _question(backend, source, root):
    import literature_tools as lt
    import pdf_quality
    def texts():
        if backend == 'library':
            for d in lt.load_library(source)['documents']:
                if d.get('selection_status') != 'included': continue
                b = next((b for b in d.get('indexes', []) if b['sha256'] == d.get('active_index')), None)
                if b: yield from (c['text'] for c in lt.read_index(source.parent, b)['chunks'])
        else:
            import corpus_tools as ct
            manifest, _, _ = ct.read_manifest(source, root)
            binding=manifest.get('search_index')
            if not binding:return
            index=core.inside(root,binding['path'])
            if core.sha(index)!=binding['sha256']:raise ValueError('Derived search index changed')
            con=sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)
            try:
                context=con.execute("SELECT value FROM context WHERE key='catalog_sha256'").fetchone()
                if not context or context[0]!=manifest['catalog']['sha256']:raise ValueError('Index/catalog version mismatch')
                for body,quality in con.execute("SELECT body,quality FROM item WHERE kind='pdf_page' ORDER BY id"):
                    if json.loads(quality)['gate']!='hold_text_for_review':yield body
            finally:con.close()
    for text in texts():
        words = [word for word in lt.tokens(text) if len(word)>=2]
        if words and pdf_quality.text_quality(text)['gate'] != 'hold_text_for_review': return ' '.join(words[:12])
    return None


def _semantic(folder, root, chosen, optional, scratch):
    if not (chosen and all(optional[n]['available'] for n in ['numpy','torch','transformers','sentencepiece','sacremoses'])
            and (folder/'models/e5/model_lock.json').is_file() and (folder/'models/translation/model_lock.json').is_file()):
        return {'status':'not_run_missing_runtime_models_or_query','passed':False}
    try:
        import semantic_retrieval as sr
        import evidence_search as es
        from query_translation import Translator
        backend,source,question=chosen[0];index=folder/'semantic/manifest.json';manifest=core.load(index)
        # Earlier dedicated bundles indexed paths from the package root; a
        # project-root index uses the project-relative name instead. Honor its
        # declared source binding, never silently reinterpret the same index.
        relative=manifest['source']['path']
        if relative==source.relative_to(root).as_posix(): semantic_root=root
        elif relative==source.relative_to(folder).as_posix(): semantic_root=folder
        else: raise ValueError('Semantic index is bound to a different source path')
        encoder=sr.Encoder(folder/'models/e5');translator=Translator(folder/'models/translation')
        q=es.query(backend,source,question,semantic_root,semantic_index=index,encoder=encoder,translator=translator,original_only=True)
        target=scratch/'semantic.json';core.save(target,q)
        replay=es.resolve(target,q['candidates'][0]['candidate_id'],semantic_root) if q['candidates'] else None
        return {'status':'executed','passed':bool(replay and replay['replayed']),'candidate_count':len(q['candidates']),
                'answer_generated':q['answer_generated'],'support_verified':False,
                'scope':'Explicit local inference, not accuracy or support certification'}
    except Exception as exc:
        return {'status':'failed','passed':False,'error':str(exc)}


def check(folder, query=None, library=None, semantic=False):
    folder = Path(folder).resolve()
    problems, smoke = [], []
    standard = (folder/'package_manifest.json').is_file()
    manifest = folder/('package_manifest.json' if standard else 'receiver_manifest.json')
    kind = 'research_handoff' if standard else 'legacy_receiver'
    state_report = dependency_report = None
    semantic_result = {'status': 'not_requested', 'passed': False}
    optional = {}
    for name in ['numpy','torch','transformers','sentencepiece','sacremoses','pypdf','pdfplumber','pypdfium2','docx']:
        present = util.find_spec(name) is not None
        try: version = metadata.version('python-docx' if name == 'docx' else name) if present else None
        except metadata.PackageNotFoundError: version = 'unknown'
        optional[name] = {'available': present, 'version': version}
    runtime = {'python': sys.version, 'executable': sys.executable, 'os': platform.platform(), 'machine': platform.machine(), 'local_only': True}
    phase='manifest_verification'
    try:
        spec = core.load(manifest)
        if not isinstance(spec, dict) or not isinstance(spec.get('files'), dict) or not spec['files']:
            raise ValueError('A nonempty manifest file mapping is required')
        if standard and spec.get('schema') != 'research-handoff/1.0': raise ValueError('Unsupported handoff schema')
        for name, expected in spec['files'].items():
            from research_flow import safe_member
            safe_member(name)
            path = core.inside(folder, name)
            if not path.is_file() or core.sha(path) != expected: problems.append({'file': name, 'reason': 'missing_or_changed'})
        if standard and 'project/research_state.json' not in spec['files']:
            raise ValueError('Standard handoff must bind project/research_state.json')
        root = core.inside(folder, 'project')
        artifacts = []
        phase='state_and_dependencies'
        if not problems and standard:
            import research_flow as flow
            artifacts = flow.read_state(root/'research_state.json')['artifacts']
            state_report = flow.check_state(root/'research_state.json')
            dependency_report = deps.inspect(root, artifacts, {n[len('project/'):] for n in spec['files'] if n.startswith('project/')})
        elif not problems:
            artifacts = [{'id': n, 'path': n[len('project/'):], 'sha256': h, 'kind': 'other'}
                         for n,h in spec['files'].items() if n.startswith('project/') and not n.startswith('project/.research_history/')]
            dependency_report = deps.inspect(root, artifacts)
        if not problems and dependency_report is not None and dependency_report['passed']:
            import evidence_search as es
            import evidence_decisions as ed
            sources, queries = [], []
            for item in artifacts:
                path = core.inside(root, item['path'])
                if path.suffix.lower() != '.json' or path.stat().st_size > deps.MAX_JSON_BYTES: continue
                try: value = core.load(path)
                except (ValueError, OSError): continue
                if not isinstance(value, dict): continue
                schema = value.get('schema')
                if schema in {'research-literature/1.0','research-corpus/1.0'}:
                    sources.append(('library' if schema == 'research-literature/1.0' else 'corpus', path))
                elif schema == es.SCHEMA: queries.append((path, value))
            if library:
                selected = core.inside(root, library); sources = [s for s in sources if s[1] == selected]
                if not sources: raise ValueError('--library must select a manifest-bound literature/corpus registry under project/')
            if query is not None and (not isinstance(query, str) or not query.strip()): raise ValueError('Explicit smoke query must be nonempty text')
            if query and not sources: raise ValueError('Explicit query requires a registered literature/corpus source')
            with tempfile.TemporaryDirectory(prefix='research receiver smoke ') as temporary:
                scratch = Path(temporary); chosen = []
                phase='original_retrieval'
                for i,(backend,source) in enumerate(sorted(set(sources))):
                    question = query or _question(backend, source, root)
                    case = {'case': 'original_lexical_query_and_replay', 'source': source.relative_to(root).as_posix(),
                            'question_origin': 'explicit_parameter' if query else 'indexed_text_functional_control',
                            'question': question, 'independent_accuracy_benchmark': False}
                    if not question:
                        smoke.append({**case, 'status': 'skipped_no_indexed_originals', 'passed': None, 'required': False}); continue
                    chosen.append((backend,source,question))
                    q = es.query(backend,source,question,root,original_only=True)
                    target = scratch/(str(i)+'.json'); core.save(target,q)
                    replay = es.resolve(target,q['candidates'][0]['candidate_id'],root) if q['candidates'] else None
                    smoke.append({**case,'status': 'executed','required': True,'passed': bool(replay and replay['replayed']),
                                  'candidate_count': len(q['candidates']),'answer_generated': q['answer_generated']})
                phase='saved_query_replay'
                for path,record in queries:
                    for c in record['candidates']: es.resolve(path,c['candidate_id'],root)
                    smoke.append({'case': 'saved_query_replay','source': path.relative_to(root).as_posix(),
                                  'candidate_count': len(record['candidates']),'status': 'executed','required': True,'passed': True})
                unknown = scratch/'unknown.json'
                core.save(unknown,{'schema':'research-claim-review/1.0','reviewer':{'id':'receiver','method':'missing evidence invariant'},
                    'claim':'No new local measurement was supplied','parts':[{'id':'new-site','text':'A new site has a measured benefit',
                    'decision':'unknown','rationale':'No new site observations in this handoff'}]})
                decision = ed.audit(unknown,root)
                smoke.append({'case':'unknown_measurement_is_not_an_assertion','required': True,'status':'executed',
                              'passed':not decision['can_export_assertion'] and decision['answer'] is None})
                if semantic:
                    semantic_result = _semantic(folder,root,chosen,optional,scratch)
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,sqlite3.Error) as exc:
        problems.append({'file':manifest.name,'reason':'acceptance_error','stage':phase,
                         'error_type':type(exc).__name__,'error':str(exc)})
    passed=(not problems and dependency_report is not None and dependency_report['passed']
            and (state_report is None or state_report['passed']) and bool(smoke)
            and all(t['passed'] for t in smoke if t.get('required')))
    return {'schema':'research-receiver-report/1.0','runtime':runtime,'package_kind':kind,'manifest_problems':problems,
        'state_check':state_report,'dependency_check':dependency_report,'optional_dependencies':optional,'smoke':smoke,
        'core_passed':bool(passed),'evidence_replay_passed':any(t.get('passed') and t.get('candidate_count',0)>0 for t in smoke),
        'semantic_ready':semantic_result['passed'],'semantic_check':semantic_result,'physical_other_computer_confirmed':False,
        'scientific_truth_certified':False,'scope':'Byte integrity, known dependency closure and functional replay only. Skipped original checks are explicit; same-host relocation is not a physical cross-host test.'}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--folder',default='.')
    p.add_argument('--output'); p.add_argument('--query',help='Optional explicit smoke question')
    p.add_argument('--library',help='Manifest-bound registry relative to project/')
    p.add_argument('--semantic',action='store_true',help='Explicitly run optional pinned local models if present')
    a=p.parse_args(); folder=Path(a.folder).resolve(); out=check(folder,a.query,a.library,a.semantic)
    target=Path(a.output) if a.output else folder/'接收机验收结果.json'; archive=target.with_suffix('.zip')
    try:
        if target.exists() or archive.exists(): raise FileExistsError('Preserve existing acceptance outputs; choose a new --output')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2); f.write('\n')
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z: z.write(target,target.name)
    except (OSError,ValueError) as exc:
        print(json.dumps({'core_passed':False,'error':str(exc)},ensure_ascii=False)); return 1
    print(json.dumps({'core_passed':out['core_passed'],'semantic_ready':out['semantic_ready'],
        'evidence_replay_passed':out['evidence_replay_passed'],'report':str(target),'return_zip':str(archive)},ensure_ascii=False))
    return 0 if out['core_passed'] else 1


if __name__=='__main__': raise SystemExit(main())
