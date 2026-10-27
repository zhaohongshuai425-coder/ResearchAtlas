"""Unified, read-only candidate retrieval across registered corpus, library and graph."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import core
from source_snapshot import read_json

SCHEMA = 'research-evidence-query/1.0'


def query(backend, source, question, root, terms=None, concepts=None, limit=10,
          alias=None, original_only=False, require_all_concepts=False,
          semantic_index=None, model_dir=None, pool_limit=500, per_document=3, document_ids=None,
          encoder=None, translation_model=None, translator=None):
    root = Path(root).resolve()
    source = core.inside(root, source)
    source_document,source_sha=read_json(source)
    if backend not in {'corpus', 'library', 'graph'}:
        raise ValueError('Choose corpus, library or graph')
    if not question.strip() or not 1 <= limit <= 100:
        raise ValueError('A question and limit 1..100 are required')
    if not 100 <= pool_limit <= 2000 or not 1 <= per_document <= 100:
        raise ValueError('Pool must be 100..2000 and per-document cap 1..100')
    terms = list(terms or [])
    concepts = list(concepts or [])
    if not all(isinstance(t, str) and t.strip() for t in terms):
        raise ValueError('Terms must be nonempty strings')
    if not all(isinstance(g, list) and g and all(isinstance(t, str) and t.strip() for t in g) for g in concepts):
        raise ValueError('Concepts must be nonempty groups of explicit alternatives')
    if require_all_concepts and not concepts:
        raise ValueError('Concept constraints require explicit concept groups')
    if alias and backend != 'corpus':
        raise ValueError('Alias filter is supported by corpus only; use an explicit library or graph scope')
    expanded = terms + [t for group in concepts for t in group]
    translation = None
    if translation_model or translator:
        from query_translation import Translator
        translator = translator or Translator(translation_model)
        translation = translator.translate(question)
        if translation.get('translated'):
            expanded.append(translation['translated'])
    records = []
    raw_pool_count = 0
    quality_filtered_count = 0
    source_snapshot = None
    binding_root = '.'
    warnings = []
    if backend == 'corpus':
        import corpus_tools
        native = corpus_tools.search(source, question, expanded, pool_limit, alias, False, root, True, pool_limit,
                                     source_snapshot=source_document)
        raw_pool_count = len(native['hits'])
        source_snapshot = source_document
        for hit in native['hits']:
            records.append({'anchor': hit['anchor'], 'document_id': hit['document_id'], 'alias': hit['alias'],
                'title': hit['title'], 'kind': hit['kind'], 'text': hit['excerpt'], 'page': hit['page'],
                'bbox': None, 'source_bindings': [hit['source_binding']], 'score': hit['score'],
                'reading_state': hit['reading'], 'native': hit})
        warnings.extend(native.get('stale_matches_withheld', []))
        trace = {k: native.get(k) for k in ('retrieval_terms', 'query_expansion', 'registered_documents',
                                           'registered_batches', 'held_flagged_matches', 'index_sha256')}
    elif backend == 'library':
        import literature_tools
        import pdf_quality
        binding_root = source.parent.relative_to(root).as_posix()
        native = literature_tools.retrieve(source, question, expanded, concepts, pool_limit, pool_limit, True,
                                           source_snapshot=source_document)
        raw_pool_count = len(native['hits'])
        for hit in native['hits']:
            quality = pdf_quality.text_quality(hit['text'])
            if quality['gate'] == 'hold_text_for_review':
                quality_filtered_count += 1
                warnings.append({'anchor': hit['anchor'], 'quality': quality, 'action': 'held_for_page_review'})
                continue
            records.append({'anchor': hit['anchor'], 'document_id': hit['document_id'], 'alias': None,
                'title': hit['title'], 'kind': 'pdf_page', 'text': hit['text'], 'page': hit['page'],
                'bbox': hit['bbox'], 'source_bindings': [hit['source'], hit['index_binding']],
                'score': hit['score'], 'reading_state': 'not_inferred_from_index', 'native': hit})
        warnings.extend(native['warnings'])
        trace = {'retrieval_terms': expanded, 'query_expansion': 'explicit_terms_and_concepts',
                 'metadata_only_documents': native['metadata_only_documents'], 'policy': native['retrieval_policy']}
    else:
        graph = source_document
        check = core.validate(graph, root, True)
        if not check['passed']:
            raise ValueError(check['errors'])
        # Graph lookup stays entity lookup, never silently becomes PDF evidence.
        # Alternatives are OR terms here; explicit concept constraints follow below.
        import literature_tools
        words = set(literature_tools.tokens(' '.join(expanded) if expanded else question))
        sources = {s['id']: s for s in graph['sources']}
        graph_hits = []
        for node in graph['nodes']:
            text = node['label'] + ' ' + json.dumps(node.get('payload', {}), ensure_ascii=False)
            matched = words & set(literature_tools.tokens(text))
            if not matched:
                continue
            graph_hits.append({'id': node['id'], 'label': node['label'], 'text': text,
                'score': len(matched), 'state': node['state'],
                'source_bindings': [{**sources[r['source_id']], **r} for r in node['source_refs']]})
        graph_hits.sort(key=lambda h: (-h['score'], h['id']))
        raw_pool_count = min(len(graph_hits), pool_limit)
        for hit in graph_hits[:pool_limit]:
            records.append({'anchor': hit['id'], 'document_id': hit['id'], 'alias': None,
                'title': hit['label'], 'kind': 'graph_entity', 'text': hit['text'], 'page': None,
                'bbox': None, 'source_bindings': hit['source_bindings'], 'score': hit['score'],
                'reading_state': hit['state'], 'native': hit})
        warnings.extend(check['warnings'])
        trace = {'retrieval_terms': expanded or [question], 'query_expansion': 'explicit_only',
                 'matched_entities': len(graph_hits), 'policy': 'OR lexical terms; explicit concepts can constrain returned entities'}
    truncated = raw_pool_count >= pool_limit or native.get('pool_may_be_truncated', False) if backend != 'graph' else len(graph_hits) > pool_limit
    if semantic_index:
        if backend == 'graph':
            raise ValueError('Graph entities cannot masquerade as dense original passages')
        import semantic_retrieval as sr
        sem = sr.search(semantic_index, question, root, model_dir, pool_limit, encoder, alias, document_ids)
        translated_sem = sr.search(semantic_index, translation['translated'], root, model_dir, pool_limit,
                                   encoder, alias, document_ids) if translation and translation.get('translated') else None
        sm = core.load(semantic_index)
        if sm['backend'] != backend or sm['source']['path'] != source.relative_to(root).as_posix():
            raise ValueError('Semantic index is bound to another backend or source')
        if (core.sha(source)!=source_sha or
                sm['source'].get('sha256',source_sha)!=source_sha or
                (backend=='corpus' and sem.get('source_snapshot')!=source_document)):
            raise ValueError('Lexical and semantic sources changed or differ; retry with one source snapshot')
        # Rank fusion never compares incomparable lexical/dense raw scores.
        fused = {}
        channels = [('lexical', records), ('semantic', sem['hits'])]
        if translated_sem:
            channels.append(('semantic_translated', translated_sem['hits']))
        for channel, rows in channels:
            for rank, row in enumerate(rows, 1):
                item = fused.setdefault(row['anchor'], {**row, 'fusion_score': 0., 'retrieval_channels': []})
                item['fusion_score'] += 1. / (60 + rank)
                item['retrieval_channels'].append(channel)
        records = sorted(fused.values(), key=lambda row: (-row['fusion_score'], row['anchor']))
        for row in records:
            row['score'] = row['fusion_score']
        truncated = truncated or sem['pool_may_be_truncated']
        trace['semantic'] = {k: sem[k] for k in ('model', 'cache_binding', 'matched_records')}
        trace['rank_fusion'] = 'reciprocal_rank_k60; similarity is not support'
    if translation:
        trace['query_translation'] = translation
    candidates = []
    counts = {}
    page_counts = {}
    excluded = {'not_original_text': 0, 'concept_constraint': 0, 'document_scope': 0, 'document_cap': 0, 'same_page': 0}
    for record in records:
        if document_ids and record['document_id'] not in document_ids:
            excluded['document_scope'] += 1
            continue
        if counts.get(record['document_id'], 0) >= per_document:
            excluded['document_cap'] += 1
            continue
        original_binding = record['native'].get('source',record['native'].get('source_binding',{}))
        page_key = (record['document_id'],original_binding.get('sha256'),record['page'])
        if record['kind'] == 'pdf_page' and page_counts.get(page_key, 0):
            excluded['same_page'] += 1
            continue
        if original_only and record['kind'] != 'pdf_page':
            excluded['not_original_text'] += 1
            continue
        # Constraint is lexical and scoped to the returned passage, not entailment.
        normalized = record['text'].casefold()
        matched = [any(term.casefold() in normalized for term in group) for group in concepts]
        if require_all_concepts and not all(matched):
            excluded['concept_constraint'] += 1
            continue
        record['matched_concepts'] = matched
        record['source_root'] = binding_root
        record['candidate_id'] = hashlib.sha256((backend + '\0' + record['anchor']).encode()).hexdigest()[:24]
        record['support_status'] = 'candidate_requires_source_review'
        record['scientific_confidence'] = None
        candidates.append(record)
        counts[record['document_id']] = counts.get(record['document_id'], 0) + 1
        page_counts[page_key] = page_counts.get(page_key, 0) + 1
        if len(candidates) >= limit:
            break
    return {'schema': SCHEMA, 'backend': backend,
        'source': {'path': source.relative_to(root).as_posix(), 'sha256': source_sha},
        'query': {'question': question, 'terms': terms, 'concepts': concepts, 'limit': limit, 'alias': alias,
                  'original_only': original_only, 'require_all_concepts': require_all_concepts,
                  'pool_limit': pool_limit, 'per_document': per_document, 'document_ids': document_ids},
        'candidates': candidates, 'warnings': warnings, 'excluded': excluded, 'trace': trace,
        'source_snapshot': source_snapshot,
        'pool_limit': pool_limit, 'raw_pool_returned': raw_pool_count,
        'quality_filtered_count': quality_filtered_count, 'pool_returned': len(records),
        'candidate_pool_may_be_truncated': truncated,
        'answer_generated': False,
        'status': 'candidates_for_review' if candidates else 'no_eligible_candidate_in_bounded_pool',
        'scope': 'Common candidate fields only. Backend scores are not comparable across backends or questions. '
                 'Optional local multilingual similarity is not translation or semantic support. '
                 'No complete coverage or reading promotion.'}


def resolve(record_file, candidate_id, root):
    root = Path(root).resolve()
    saved = core.load(record_file)
    if saved.get('schema') != SCHEMA:
        raise ValueError('Unsupported query record')
    source = core.inside(root, saved['source']['path'])
    registry_changed = not source.is_file() or core.sha(source) != saved['source']['sha256']
    original = next((c for c in saved['candidates'] if c['candidate_id'] == candidate_id), None)
    if original is None:
        raise ValueError('Candidate is not in this query record')
    if not isinstance(original.get('text'),str) or not original['text'].strip():
        raise ValueError('Empty evidence text cannot be replayed as an original passage')
    expected_id = hashlib.sha256((saved['backend'] + '\0' + original['anchor']).encode()).hexdigest()[:24]
    if expected_id != candidate_id:
        raise ValueError('Candidate identity does not match its anchor')
    native = original['native']
    if saved['backend'] == 'library':
        import literature_tools
        binding_root = core.inside(root, original['source_root'])
        index = literature_tools.read_index(binding_root, native['index_binding'])
        chunk = next((c for c in index['chunks'] if c['anchor'] == original['anchor']), None)
        if (chunk is None or index['source'] != native['source']
                or any(chunk.get(k) != original.get(k) for k in ('document_id', 'text', 'page', 'bbox'))
                or any(native.get(k) != original.get(k) for k in ('document_id', 'text', 'page', 'bbox'))):
            raise ValueError('Candidate original/index content changed or anchor does not identify this passage')
    elif saved['backend'] == 'corpus':
        snapshot = saved.get('source_snapshot')
        if not snapshot:
            if registry_changed:
                raise ValueError('Legacy corpus query lacks a preserved manifest; use its original manifest version')
            snapshot = core.load(source)
        binding = snapshot['search_index']
        db = core.inside(root, binding['path'])
        if core.sha(db) != binding['sha256']:
            raise ValueError('Preserved index changed')
        anchor = re.search(r'::row(\d+)@([0-9a-f]{16})$', original['anchor'])
        if not anchor or anchor[2] != binding['sha256'][:16]:
            raise ValueError('Anchor/index version mismatch')
        con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute('SELECT * FROM item WHERE id=?', (int(anchor[1]),)).fetchone()
        finally:
            con.close()
        import corpus_tools as ct
        if row is not None and original['anchor'] != ct.row_anchor(snapshot,row,binding):
            raise ValueError('Corpus anchor does not match its preserved document/version/page/row')
        if (row is None or any(row[k] != original[k] for k in ('document_id', 'kind', 'page'))
                or json.loads(row['binding']) != native['source_binding']
                or original['text'] != native['excerpt'] or original['text'] not in row['body']):
            raise ValueError('Corpus candidate differs from its preserved row')
        for item in [native['source_binding']] + ([native['source_binding']['cache']] if native['source_binding'].get('cache') else []):
            path = core.inside(root, item['path'])
            if not path.is_file() or core.sha(path) != item['sha256']:
                raise ValueError('Candidate original or extraction cache changed')
    elif saved['backend'] == 'graph':
        if registry_changed:
            raise ValueError('Graph version changed; preserve the original graph version')
        fresh = query(saved['backend'], source, root=root, **saved['query'])
        found = next((c for c in fresh['candidates'] if c['candidate_id'] == candidate_id), None)
        if found != original:
            raise ValueError('Graph candidate changed')
    else:
        raise ValueError('Unsupported candidate backend')
    return {'candidate': original, 'replayed': True, 'support_verified': False,
            'source_registry_changed': registry_changed, 'ranking_replayed': False,
            'scope': 'Replays the exact preserved original/index passage, not a new ranking or scientific support. '
                     'Unrelated registry additions do not invalidate unchanged source evidence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    ask = commands.add_parser('query')
    ask.add_argument('--backend', choices=['corpus', 'library', 'graph'], required=True)
    ask.add_argument('--source', required=True)
    ask.add_argument('--question', required=True)
    ask.add_argument('--root', required=True)
    ask.add_argument('--terms', action='append', default=[])
    ask.add_argument('--concept', action='append', default=[], help='Explicit alternatives separated by |')
    ask.add_argument('--limit', type=int, default=10)
    ask.add_argument('--alias')
    ask.add_argument('--original-only', action='store_true')
    ask.add_argument('--require-all-concepts', action='store_true')
    ask.add_argument('--semantic-index')
    ask.add_argument('--model-dir')
    ask.add_argument('--translation-model')
    ask.add_argument('--pool-limit', type=int, default=500)
    ask.add_argument('--per-document', type=int, default=3)
    ask.add_argument('--document-id', action='append')
    ask.add_argument('--output', required=True)
    locate = commands.add_parser('resolve')
    locate.add_argument('--record', required=True)
    locate.add_argument('--candidate', required=True)
    locate.add_argument('--root', required=True)
    args = parser.parse_args()
    if args.command == 'query':
        result = query(args.backend, args.source, args.question, args.root, args.terms,
                       [group.split('|') for group in args.concept], args.limit, args.alias,
                       args.original_only, args.require_all_concepts,
                       args.semantic_index, args.model_dir, args.pool_limit, args.per_document, args.document_id,
                       translation_model=args.translation_model)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'output': str(output.resolve()), 'candidates': len(result['candidates']), 'answer_generated': False}))
    else:
        print(json.dumps(resolve(args.record, args.candidate, args.root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
