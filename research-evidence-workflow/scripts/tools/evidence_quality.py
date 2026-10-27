"""Scoped reading audits and conditional graph additions. No automatic claim judgement."""
from pathlib import Path
import argparse
import copy
import json
import core
import evidence_search

READING_FIELDS = {'problem', 'mechanism', 'conditions', 'validation', 'results', 'limitations', 'transfer'}


def checked_reviews(file, root):
    root = Path(root).resolve()
    file = core.inside(root, file)
    data = core.load(file)
    if data.get('schema') != 'research-scoped-reading/1.0' or not data.get('reviewer') or not data.get('reviews'):
        raise ValueError('Explicit reviewer and scoped reading records required')
    checked = []
    identifiers = set()
    for review in data['reviews']:
        if not review.get('id') or review['id'] in identifiers:
            raise ValueError('Unique review IDs required')
        identifiers.add(review['id'])
        if review.get('decision') not in {'supports', 'challenges', 'insufficient'}:
            raise ValueError('Evidence decision must be explicit')
        if (not isinstance(review.get('conditions'), list) or not review['conditions']
                or not all(isinstance(c, str) and c.strip() for c in review['conditions'])
                or not all(str(review.get(k, '')).strip() for k in ('claim', 'reading_scope', 'rationale'))):
            raise ValueError('Claim, reading scope, rationale and conditions required')
        fields = review.get('reading_fields', {})
        if set(fields) != READING_FIELDS:
            raise ValueError('Reading fields must describe mechanism, validation and limits; unknown remains explicit')
        for value in fields.values():
            if value.get('status') not in {'recorded', 'unknown', 'not_applicable'} or not str(value.get('text', '')).strip():
                raise ValueError('Each reading field needs a status and content or reason')
        query = core.inside(root, review['query']['path'])
        if core.sha(query) != review['query']['sha256']:
            raise ValueError('Query record changed')
        candidates = [evidence_search.resolve(query, identity, root)['candidate'] for identity in review['candidate_ids']]
        if not candidates or any(c['kind'] != 'pdf_page' for c in candidates):
            raise ValueError('Scoped source review requires original text candidates')
        if len({c['document_id'] for c in candidates}) != 1:
            raise ValueError('Use separate scoped reviews for different documents')
        inspection_file = core.inside(root, review['inspection']['path'])
        if core.sha(inspection_file) != review['inspection']['sha256']:
            raise ValueError('Page inspection changed')
        inspection = core.load(inspection_file)
        pages = {p['page']: p for p in inspection['pages']}
        originals = {c['native'].get('source', c['native'].get('source_binding'))['sha256'] for c in candidates}
        if originals != {inspection['source_sha256']}:
            raise ValueError('Inspection and original do not agree')
        if any(c['page'] not in pages or pages[c['page']]['quality']['gate'] == 'hold_text_for_review' for c in candidates):
            raise ValueError('Review page is absent or held for quality review')
        previews = review.get('visual_checks', [])
        if not previews:
            raise ValueError('Selected original pages need recorded visual checks')
        for preview in previews:
            path = core.inside(root, preview['path'])
            if (preview.get('page') not in pages or preview.get('status') != 'visually_checked'
                    or preview.get('source_sha256') != inspection['source_sha256']
                    or core.sha(path) != preview['sha256']):
                raise ValueError('Invalid visual-check binding')
        if not {c['page'] for c in candidates} <= {p['page'] for p in previews}:
            raise ValueError('Each cited page needs its visual check')
        checked.append({'review': review, 'candidates': candidates})
    return {'passed': True, 'reviews': checked, 'reviewer': data['reviewer'],
            'scope': 'Checks declared reading records and source bindings; not an independent scientific adjudication.'}


def add_to_graph(graph_file, review_file, root, output, return_graph=False):
    root = Path(root).resolve()
    output = Path(output)
    if output.exists() and not return_graph:
        raise FileExistsError('Preserve graph versions')
    graph = copy.deepcopy(core.load(graph_file))
    base = core.validate(graph, root, True)
    if not base['passed']:
        raise ValueError(base['errors'])
    audited = checked_reviews(review_file, root)
    instance = graph['instance_id']
    q = lambda value: core.qualified(instance, value)
    node_ids = {n['id'] for n in graph['nodes']}
    edge_ids = {e['id'] for e in graph['edges']}
    def source(path, digest):
        identity = q('SRC-' + digest[:20])
        if identity not in {s['id'] for s in graph['sources']}:
            graph['sources'].append({'id': identity, 'local_path': path, 'sha256': digest})
        return identity
    review_file = core.inside(root, review_file)
    review_source = source(review_file.relative_to(root).as_posix(), core.sha(review_file))
    for item in audited['reviews']:
        record = item['review']
        document = q(item['candidates'][0]['document_id'])
        if document not in node_ids:
            raise ValueError('Reviewed document is not in the base graph')
        refs = [{'source_id': review_source, 'locator': {'review_id': record['id'], 'scope': record['reading_scope']}}]
        for candidate in item['candidates']:
            original = candidate['native'].get('source', candidate['native'].get('source_binding'))
            relative = (Path(candidate['source_root']) / original['path']).as_posix()
            refs.append({'source_id': source(relative, original['sha256']),
                         'locator': {'physical_pdf_page': candidate['page'], 'anchor': candidate['anchor']}})
        for binding in [record['inspection'], record['query']] + record['visual_checks']:
            refs.append({'source_id': source(binding['path'], binding['sha256']),
                         'locator': {'review_id': record['id'], 'role': 'recorded_page_or_query_check'}})
        local = {name: q(record['id'] + '-' + name) for name in ('question', 'method', 'context', 'evidence', 'claim', 'decision')}
        labels = {'question': record['reading_fields']['problem']['text'],
                  'method': record['reading_fields']['mechanism']['text'],
                  'context': '；'.join(record['conditions']), 'evidence': record['reading_scope'],
                  'claim': record['claim'], 'decision': record['rationale']}
        has_method = record['reading_fields']['mechanism']['status'] == 'recorded'
        for kind, identity in local.items():
            if kind == 'method' and not has_method:
                continue
            if identity in node_ids:
                raise ValueError('New review IDs must not overwrite existing nodes')
            node_ids.add(identity)
            state = {axis: 'unknown' for axis in core.AXES}
            state.update(availability='available', reading='selected_passages_reviewed')
            if kind == 'claim':
                state['adoption'] = 'not_decided'
            graph['nodes'].append({'id': identity, 'local_id': identity.split('::')[-1], 'type': kind,
                'review_record_id': record['id'],
                'label': labels[kind], 'state': state, 'source_refs': copy.deepcopy(refs),
                'scope': 'Single-reader scoped interpretation; no whole-paper or project-result promotion.'})
        edges = [(document, local['question'], 'related_to'),
                 (local['context'], local['method'] if has_method else document, 'context_for'), (document, local['evidence'], 'has_evidence'),
                 (local['decision'], local['claim'], 'decision_about')]
        if has_method:
            edges.append((document, local['method'], 'describes'))
        if record['decision'] != 'insufficient':
            edges.append((local['evidence'], local['claim'], record['decision']))
        for number, (start, end, relation) in enumerate(edges):
            identity = q(record['id'] + '-edge-' + str(number))
            if identity in edge_ids:
                raise ValueError('Duplicate review edge')
            edge_ids.add(identity)
            graph['edges'].append({'id': identity, 'from': start, 'to': end, 'relation': relation,
                'review_record_id': record['id'],
                'conditions': record['conditions'], 'review_status': 'single_reader_selected_pages_checked',
                'source_refs': copy.deepcopy(refs)})
    report = core.validate(graph, root, True)
    if not report['passed']:
        raise ValueError(report['errors'])
    result={**report,'review_count':len(audited['reviews']),'whole_paper_reading_promoted':False,
            'scope':'Conditional scoped graph records. Neither review text nor graph connectivity proves scientific correctness.'}
    if return_graph:return {**result,'graph':graph}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(graph, stream, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--graph')
    parser.add_argument('--output')
    args = parser.parse_args()
    if bool(args.graph) != bool(args.output):
        raise ValueError('Graph and new output must be given together')
    report = add_to_graph(args.graph, args.reviews, args.root, args.output) if args.graph else checked_reviews(args.reviews, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
