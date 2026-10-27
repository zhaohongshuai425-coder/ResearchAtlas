"""Bounded multi-provider discovery and exact-DOI checks, without silent merging.

Raw provider responses and failures are retained. A bibliographic match is not
full-text review; an OA URL is a candidate until its downloaded original is checked.
"""
from pathlib import Path
from urllib.parse import quote, urlencode
from difflib import SequenceMatcher
import argparse
import json
import re
import xml.etree.ElementTree as ET
import core
import literature_tools as lt

PROVIDERS = ('crossref', 'openalex', 'semantic_scholar', 'arxiv')


def doi(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/|^doi:\s*', '', value.strip(), flags=re.I).casefold()


def normalized_title(value):
    return ' '.join(re.findall(r'\w+', value.casefold()))


def parse(provider, body, exact=False):
    if provider == 'arxiv':
        ns = {'a': 'http://www.w3.org/2005/Atom', 'ar': 'http://arxiv.org/schemas/atom'}
        rows = []
        for item in ET.fromstring(body).findall('a:entry', ns):
            rows.append({'title': ' '.join(item.findtext('a:title', '', ns).split()),
                'doi': item.findtext('ar:doi', '', ns), 'url': item.findtext('a:id', '', ns),
                'authors': [a.findtext('a:name', '', ns) for a in item.findall('a:author', ns)],
                'date_fields': {k: item.findtext('a:'+k, '', ns) for k in ('published', 'updated')},
                'fulltext_candidates': [l.attrib['href'] for l in item.findall('a:link', ns)
                                         if l.attrib.get('title') == 'pdf'],
                'version': 'arxiv_preprint_not_publisher_version', 'provider': provider})
        return rows
    data = json.loads(body)
    if provider == 'crossref':
        items = [data['message']] if exact else data['message']['items']
        return [{**lt.crossref_record(i), 'version_relations': i.get('relation', {}),
                 'update_to': i.get('update-to', []), 'clinical_trial': i.get('clinical-trial-number', [])} for i in items]
    if provider == 'openalex':
        items = [data] if exact else data['results']
        return [{'title': i.get('title', ''), 'doi': doi(i.get('doi') or ''), 'url': i.get('id'),
            'authors': [a['author']['display_name'] for a in i.get('authorships', [])],
            'date_fields': {'publication_date': i.get('publication_date')},
            'fulltext_candidates': list(dict.fromkeys(l['pdf_url'] for l in i.get('locations', []) if l.get('pdf_url'))),
            'locations': i.get('locations', []), 'is_retracted': i.get('is_retracted'),
            'provider': provider} for i in items]
    items = [data] if exact else data.get('data', [])
    return [{'title': i.get('title') or '', 'doi': (i.get('externalIds') or {}).get('DOI', ''),
        'url': i.get('url'), 'authors': [a['name'] for a in i.get('authors', [])],
        'date_fields': {'publicationDate': i.get('publicationDate'), 'year': i.get('year')},
        'fulltext_candidates': [i['openAccessPdf']['url']] if (i.get('openAccessPdf') or {}).get('url') else [],
        'provider': provider} for i in items]


def request_url(provider, question=None, identity=None, limit=10):
    fields = 'title,externalIds,url,authors,year,publicationDate,openAccessPdf'
    if provider == 'crossref':
        return 'https://api.crossref.org/works/' + quote(doi(identity), safe='') if identity else (
            'https://api.crossref.org/works?' + urlencode({'query.bibliographic': question, 'rows': limit}))
    if provider == 'openalex':
        return 'https://api.openalex.org/works/https://doi.org/' + quote(doi(identity), safe='') if identity else (
            'https://api.openalex.org/works?' + urlencode({'search': question, 'per-page': limit}))
    if provider == 'semantic_scholar':
        return 'https://api.semanticscholar.org/graph/v1/paper/DOI:' + quote(doi(identity), safe='') + '?' + urlencode({'fields': fields}) if identity else (
            'https://api.semanticscholar.org/graph/v1/paper/search?' + urlencode({'query': question, 'limit': limit, 'fields': fields}))
    return 'https://export.arxiv.org/api/query?' + urlencode({
        'search_query': ('doi:"'+doi(identity)+'"') if identity else 'all:'+question,
        'start': 0, 'max_results': limit})


def run(output, question=None, identity=None, expected_title=None, providers=PROVIDERS, limit=10):
    if (bool(question) == bool(identity) or not 1 <= limit <= 50 or not providers
            or len(providers)!=len(set(providers)) or not set(providers) <= set(PROVIDERS)):
        raise ValueError('Choose one bounded query or exact DOI and known providers')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Keep provider checks as separate immutable attempts')
    output.mkdir(parents=True)
    attempts, records = [], []
    for provider in providers:
        url = request_url(provider, question, identity, limit)
        try:
            body, retrieval = lt.http_get(url, 16*1024*1024)
            raw = output / (provider + ('.xml' if provider == 'arxiv' else '.json'))
            raw.write_bytes(body)
            rows = parse(provider, body, exact=bool(identity) and provider != 'arxiv')
            attempts.append({'provider': provider, 'status': 'received', **retrieval,
                             'raw': {'path': raw.name, 'sha256': core.sha(raw)}, 'records': len(rows)})
            for row in rows:
                matched_doi = doi(row.get('doi', '')) == doi(identity) if identity else None
                similarity = SequenceMatcher(None, normalized_title(expected_title), normalized_title(row['title'])).ratio() if expected_title else None
                records.append({**row, 'provider': provider, 'exact_doi_match': matched_doi,
                    'expected_title_similarity': similarity,
                    'identity_review': 'needs_review' if identity and (not matched_doi or similarity is not None and similarity < .92) else 'provider_reported_match'})
        except Exception as exc:
            attempts.append({'provider': provider, 'url': url, 'status': 'failed',
                             'error': str(exc), 'missing_is_not_negative_evidence': True})
    titles = {normalized_title(r['title']) for r in records if r.get('exact_doi_match') is not False}
    result = {'schema': 'research-provider-check/1.0', 'query': question, 'requested_doi': identity,
        'expected_title': expected_title, 'attempts': attempts, 'records': records,
        'conflicts': {'title_variants': len(titles) > 1,
                     'identity_requires_review': any(r['identity_review'] == 'needs_review' for r in records),
                     'date_fields_preserved_separately': True},
        'automatic_merge': False, 'fulltext_reviewed': False,
        'scope': 'Bounded provider snapshots, including failures. No exhaustive coverage, definitive retraction clearance or full-text support.'}
    core.save(output / 'report.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--query'); g.add_argument('--doi')
    p.add_argument('--expected-title'); p.add_argument('--provider', action='append', choices=PROVIDERS)
    p.add_argument('--limit', type=int, default=10); p.add_argument('--output', required=True)
    a = p.parse_args()
    out = run(a.output, a.query, a.doi, a.expected_title, a.provider or PROVIDERS, a.limit)
    print(json.dumps({'attempts': out['attempts'], 'records': len(out['records']), 'conflicts': out['conflicts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
