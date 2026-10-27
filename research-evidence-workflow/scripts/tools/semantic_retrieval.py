"""Local multilingual candidate retrieval. Scores never establish scientific support.

Optional torch/transformers/numpy dependency; lexical tools remain standard-library.
All model files, sources and derived records are pinned. No repository code runs.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3
import core

SCHEMA = 'research-semantic-index/1.0'


def signature(backend, source, root):
    source = core.inside(root, source)
    if backend == 'library':
        import literature_tools as lt
        data = lt.load_library(source)
        return [{'id': d['id'], 'index': d.get('active_index'), 'selection': d.get('selection_status')}
                for d in data['documents']]
    if backend == 'corpus':
        import corpus_tools as ct
        manifest, _, _ = ct.read_manifest(source, root)
        for binding in (manifest['catalog'], manifest['search_index']):
            if core.sha(core.inside(root, binding['path'])) != binding['sha256']:
                raise ValueError('Corpus binding changed')
        return {k: manifest[k] for k in ('catalog', 'search_index')}
    raise ValueError('Semantic retrieval accepts original library or corpus sources only')


def records(backend, source, root, source_snapshot=None):
    """Read intact original passages, excluding held text and non-original material."""
    import pdf_quality
    source = core.inside(root, source)
    result, withheld = [], []
    if backend == 'library':
        import literature_tools as lt
        binding_root = source.parent
        data = lt.load_library(source)
        if source_snapshot is not None and data != source_snapshot:
            raise ValueError('Source changed while reading semantic records')
        for doc in data['documents']:
            if doc.get('selection_status') != 'included':
                continue
            active = next((b for b in doc['indexes'] if b['sha256'] == doc.get('active_index')), None)
            if not active:
                withheld.append({'document_id': doc['id'], 'reason': 'no_original_index'})
                continue
            index = lt.read_index(binding_root, active)
            for chunk in index['chunks']:
                if pdf_quality.text_quality(chunk['text'])['gate'] == 'hold_text_for_review':
                    withheld.append({'anchor': chunk['anchor'], 'reason': 'text_quality'})
                    continue
                native = {**chunk, 'title': doc['metadata']['title'], 'source': index['source'],
                          'index_binding': active, 'score': None}
                result.append({'anchor': chunk['anchor'], 'document_id': doc['id'], 'alias': None,
                    'title': doc['metadata']['title'], 'kind': 'pdf_page', 'text': chunk['text'],
                    'page': chunk['page'], 'bbox': chunk['bbox'], 'source_bindings': [index['source'], active],
                    'source_root': binding_root.relative_to(root).as_posix(),
                    'reading_state': 'not_inferred_from_index', 'native': native})
    elif backend == 'corpus':
        import corpus_tools as ct
        manifest, _, catalog = ct.read_manifest(source, root)
        if source_snapshot is not None and manifest != source_snapshot:
            raise ValueError('Source changed while reading semantic records')
        binding = manifest['search_index']
        db = core.inside(root, binding['path'])
        if core.sha(db) != binding['sha256']:
            raise ValueError('Corpus index changed')
        docs = {d['id']: d for d in catalog['documents']}
        con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        verified = set()
        try:
            for row in con.execute("SELECT * FROM item WHERE kind='pdf_page'"):
                if json.loads(row['quality'])['gate'] == 'hold_text_for_review':
                    withheld.append({'row': row['id'], 'reason': 'text_quality'})
                    continue
                original = json.loads(row['binding'])
                for b in [original] + ([original['cache']] if original.get('cache') else []):
                    key = (b['path'], b['sha256'])
                    if key not in verified:
                        if core.sha(core.inside(root, b['path'])) != b['sha256']:
                            raise ValueError('Original/cache changed: ' + b['path'])
                        verified.add(key)
                doc = docs[row['document_id']]
                anchor = ct.row_anchor(manifest,row,binding)
                native = {'anchor': anchor, 'document_id': row['document_id'], 'alias': row['alias'],
                          'title': doc['title'], 'kind': 'pdf_page', 'page': row['page'],
                          'excerpt': row['body'], 'source_binding': original, 'score': None}
                result.append({'anchor': anchor, 'document_id': row['document_id'], 'alias': row['alias'],
                    'title': doc['title'], 'kind': 'pdf_page', 'text': row['body'], 'page': row['page'],
                    'bbox': None, 'source_bindings': [original], 'source_root': '.',
                    'reading_state': doc['reading'], 'native': native})
        finally:
            con.close()
    else:
        raise ValueError('Unknown original-source backend')
    return result, withheld


class Encoder:
    def __init__(self, model_dir, threads=2):
        import torch
        from transformers import AutoTokenizer, AutoModel
        self.directory = Path(model_dir).resolve()
        lock = core.load(self.directory / 'model_lock.json')
        if (lock.get('schema') != 'research-local-model/1.0' or lock.get('trust_remote_code') is not False
                or lock.get('weights_format') != 'safetensors'):
            raise ValueError('A pinned, safetensors-only local model lock is required')
        for binding in lock['files']:
            if core.sha(core.inside(self.directory, binding['path'])) != binding['sha256']:
                raise ValueError('Model file differs from lock')
        torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(self.directory, local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(self.directory, local_files_only=True,
                                              trust_remote_code=False, use_safetensors=True).eval()
        self.lock_sha = core.sha(self.directory / 'model_lock.json')
        self.lock = lock

    def encode(self, texts, role, batch=8):
        import numpy as np
        result = []
        for start in range(0, len(texts), batch):
            inputs = self.tokenizer([role + ': ' + t for t in texts[start:start+batch]],
                                    padding=True, truncation=False, return_tensors='pt')
            if inputs['input_ids'].shape[1] > 512:
                raise ValueError('Unsplit passage/query exceeds the pinned model context')
            with self.torch.inference_mode():
                hidden = self.model(**inputs).last_hidden_state
                mask = inputs['attention_mask'][..., None].bool()
                pooled = hidden.masked_fill(~mask, 0).sum(1) / mask.sum(1)
                pooled = self.torch.nn.functional.normalize(pooled, p=2, dim=1)
                result.append(pooled.cpu().numpy().astype('float32'))
        return np.concatenate(result) if result else np.empty((0, self.model.config.hidden_size), dtype='float32')


def build(backend, source, root, model_dir, output, batch=8):
    import numpy as np
    root = Path(root).resolve()
    source = core.inside(root,source)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Keep existing semantic index versions')
    source_sha = core.sha(source)
    source_snapshot = core.load(source)
    source_signature = signature(backend, source, root)
    def require_same_source():
        if core.sha(source) != source_sha or signature(backend, source, root) != source_signature:
            raise ValueError('Source changed during semantic build; build a new index from a stable snapshot')
    require_same_source()
    rows, withheld = records(backend, source, root, source_snapshot=source_snapshot)
    require_same_source()
    encoder = Encoder(model_dir)
    windows, mapping = [], []
    for i, row in enumerate(rows):
        tokens = encoder.tokenizer.encode(row['text'], add_special_tokens=False)
        # 400 source tokens, overlap 80; safe margin for prefix/re-tokenization.
        for start in range(0, len(tokens), 320):
            text = encoder.tokenizer.decode(tokens[start:start+400], skip_special_tokens=True)
            windows.append(text)
            mapping.append({'record': i, 'token_start': start, 'token_end': min(start+400, len(tokens))})
            if start+400 >= len(tokens):
                break
    output.mkdir(parents=True)
    try:
        # Small batches and progress do not require an agent monitor.
        matrix = []
        for start in range(0, len(windows), 64):
            matrix.append(encoder.encode(windows[start:start+64], 'passage', batch))
            print(f'embedded {min(start+64,len(windows))}/{len(windows)}', flush=True)
        vectors = np.concatenate(matrix) if matrix else encoder.encode([], 'passage')
        np.savez_compressed(output / 'vectors.npz', vectors=vectors)
        core.save(output / 'records.json', rows)
        core.save(output / 'windows.json', mapping)
        bindings = {name: {'path': name, 'sha256': core.sha(output / name)}
                    for name in ('vectors.npz', 'records.json', 'windows.json')}
        require_same_source()
        manifest = {'schema': SCHEMA, 'backend': backend,
            'source': {'path': core.inside(root, source).relative_to(root).as_posix(),
                       'signature': source_signature, 'sha256': source_sha},
            'source_snapshot': source_snapshot if backend == 'corpus' else None,
            'model': {'lock_sha256': encoder.lock_sha, 'repository': encoder.lock['repository'],
                      'revision': encoder.lock['revision']},
            'bindings': bindings, 'records': len(rows), 'windows': len(windows), 'withheld': withheld,
            'pooling': 'masked_mean_L2', 'prefixes': ['query: ', 'passage: '],
            'window': {'tokens': 400, 'stride': 320},
            'scope': 'Local multilingual similarity only; not translation, entailment, full reading or completeness.'}
        core.save(output / 'manifest.json', manifest)
        require_same_source()
        return manifest
    except BaseException:
        core.save(output / 'incomplete.json', {'status': 'incomplete_do_not_use'})
        raise


def search(index, question, root, model_dir=None, limit=100, encoder=None, alias=None, document_ids=None):
    import numpy as np
    index = Path(index).resolve()
    if (index.parent / 'incomplete.json').exists():
        raise ValueError('Incomplete semantic build; do not use this cache')
    root = Path(root).resolve()
    manifest = core.load(index)
    if manifest.get('schema') != SCHEMA or not 1 <= limit <= 2000:
        raise ValueError('Invalid semantic index or bounded limit')
    if signature(manifest['backend'], core.inside(root, manifest['source']['path']), root) != manifest['source']['signature']:
        raise ValueError('Active source scope/index changed; build a new derived semantic index')
    if manifest['source'].get('sha256') and core.sha(core.inside(root,manifest['source']['path'])) != manifest['source']['sha256']:
        raise ValueError('Source snapshot changed; build a new derived semantic index')
    for binding in manifest['bindings'].values():
        if core.sha(core.inside(index.parent, binding['path'])) != binding['sha256']:
            raise ValueError('Semantic cache changed')
    encoder = encoder or Encoder(model_dir)
    if encoder.lock_sha != manifest['model']['lock_sha256']:
        raise ValueError('Model/index mismatch')
    vectors = np.load(index.parent / 'vectors.npz', allow_pickle=False)['vectors']
    rows = core.load(index.parent / 'records.json')
    windows = core.load(index.parent / 'windows.json')
    if len(vectors) != len(windows) or len(rows) != manifest['records']:
        raise ValueError('Semantic cache dimensions differ')
    scores = vectors @ encoder.encode([question], 'query')[0]
    best = {}
    for i, score in enumerate(scores.tolist()):
        row = rows[windows[i]['record']]
        if alias and row.get('alias') != alias:
            continue
        if document_ids and row['document_id'] not in document_ids:
            continue
        key = windows[i]['record']
        if key not in best or score > best[key][0]:
            best[key] = (score, windows[i])
    ranked = sorted(best, key=lambda i: (-best[i][0], rows[i]['anchor']))
    hits = [{**rows[i], 'score': best[i][0], 'semantic_window': best[i][1]} for i in ranked[:limit]]
    verified = set()
    for hit in hits:
        binding_root = core.inside(root, hit['source_root'])
        bindings = list(hit['source_bindings'])
        for b in list(bindings):
            if b.get('cache'):
                bindings.append(b['cache'])
        for b in bindings:
            key = (b['path'], b['sha256'])
            if key not in verified:
                if core.sha(core.inside(binding_root, b['path'])) != b['sha256']:
                    raise ValueError('Returned original/index/cache changed')
                verified.add(key)
    return {'hits': hits, 'matched_records': len(ranked), 'pool_may_be_truncated': len(ranked) > limit,
            'source_snapshot': manifest.get('source_snapshot'), 'model': manifest['model'],
            'cache_binding': {'path': index.relative_to(root).as_posix(), 'sha256': core.sha(index)},
            'support_verified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['build', 'search'])
    p.add_argument('--backend', choices=['library', 'corpus'])
    p.add_argument('--source'); p.add_argument('--root', required=True)
    p.add_argument('--model', required=True); p.add_argument('--output')
    p.add_argument('--index'); p.add_argument('--question'); p.add_argument('--limit', type=int, default=10)
    a = p.parse_args()
    if a.command == 'build':
        out = build(a.backend, a.source, a.root, a.model, a.output)
        print(json.dumps({'records': out['records'], 'windows': out['windows']}))
    else:
        print(json.dumps(search(a.index, a.question, a.root, a.model, a.limit), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
