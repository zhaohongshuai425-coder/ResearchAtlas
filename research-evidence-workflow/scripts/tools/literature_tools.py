"""Public literature capture, immutable PDF anchors and question evidence retrieval.

No credentials, paid services or language model are required. PDF parsing uses
optional pdfplumber/pypdf. Retrieval scores do not certify support or truth.
"""
from pathlib import Path
from collections import Counter, defaultdict
from html.parser import HTMLParser
from importlib import metadata
from urllib.parse import urlsplit, urlencode, urljoin
from urllib.request import Request, urlopen
import argparse
import copy
import hashlib
import json
import math
import re
import sys

import core
import research_flow as flow

SCHEMA = 'research-literature/1.0'
STOP = set('a an the of to in on for with by is are was were be and or from that this how what why which does do can we our'.split())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_library(path,source_snapshot=None):
    value = core.load(path) if source_snapshot is None else copy.deepcopy(source_snapshot)
    if value.get('schema') != SCHEMA:
        raise ValueError('Unsupported literature schema')
    if not isinstance(value.get('documents'),list):raise ValueError('Literature documents must be a list')
    for position,doc in enumerate(value['documents']):
        if not isinstance(doc,dict) or not isinstance(doc.get('metadata'),dict):raise ValueError('Invalid document at position '+str(position))
        title=doc['metadata'].get('title')
        if not isinstance(title,str) or not title.strip():raise ValueError('Invalid title in document '+str(doc.get('id',position))+'; repair from original metadata')
    return value


def validate_metadata(record,local=False):
    if not isinstance(record,dict):raise ValueError('Metadata must be an object')
    if 'title' in record and (not isinstance(record['title'],str) or not record['title'].strip()):raise ValueError('Reported title must be nonempty text')
    if local:
        if set(record)-{'document_id','title','doi','authors','venue','version_note'}:raise ValueError('Unknown local metadata field')
        for key in ('document_id','doi','venue','version_note'):
            if key in record and not isinstance(record[key],str):raise ValueError('Metadata '+key+' must be text')
        if 'document_id' in record and not record['document_id'].strip():raise ValueError('Explicit document_id must be nonempty')
        if 'authors' in record and not isinstance(record['authors'],(str,list)):raise ValueError('Authors must be text or a list')
        if isinstance(record.get('authors'),list) and any(not isinstance(a,(str,dict)) for a in record['authors']):raise ValueError('Invalid author entry')
    return record


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(flow.dump(value))


def failed_attempt(library, operation, error, **details):
    with flow.locked(library):
        data = load_library(library)
        data['attempts'].append({'operation': operation, 'success': False, 'attempted_at': flow.now(),
                                 'error': str(error), **details})
        flow.commit(library, data)
    return {'passed': False, 'operation': operation, 'error': str(error), **details}


def initialize(path, topic, instance):
    path = Path(path).resolve()
    core.qualified(instance, 'check')
    if not topic.strip():
        raise ValueError('Research direction is empty')
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'schema': SCHEMA, 'topic': topic, 'instance_id': instance, 'revision': 0,
             'created_at': flow.now(), 'updated_at': flow.now(),
             'documents': [], 'searches': [], 'attempts': [], 'title_collision_review': []}
    with path.open('x', encoding='utf-8') as stream:
        stream.write(flow.dump(value))
    return {'created': str(path), 'documents': 0}


def blob(root, folder, content, suffix):
    sha = digest(content)
    path = core.inside(root, folder + '/' + sha + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if core.sha(path) != sha:
            raise ValueError('Immutable content object differs: ' + str(path))
    else:
        try:
            with path.open('xb') as stream:
                stream.write(content)
        except FileExistsError:
            if core.sha(path) != sha:
                raise ValueError('Concurrent immutable content mismatch')
    return {'path': path.relative_to(Path(root).resolve()).as_posix(), 'sha256': sha}


def http_get(url, max_bytes=4 * 1024 * 1024):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use an explicit public HTTP(S) URL without credentials')
    request = Request(url, headers={'User-Agent': 'ResearchKnowledgeTools/0.1', 'Accept': '*/*'})
    with urlopen(request, timeout=35) as response:
        final = response.geturl()
        if urlsplit(final).scheme not in {'http', 'https'}:
            raise ValueError('Unsupported redirect scheme')
        pieces, total = [], 0
        while True:
            piece = response.read(min(1024 * 1024, max_bytes - total + 1))
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                raise ValueError('Response exceeds declared byte budget')
            pieces.append(piece)
        return b''.join(pieces), {'requested_url': url, 'final_url': final,
                                  'content_type': response.headers.get('Content-Type', ''),
                                  'retrieved_at': flow.now()}


def doi_key(value):
    value = str(value or '').strip()
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value, flags=re.I)
    if value and not re.fullmatch(r'10\.\d{4,9}/\S+', value, flags=re.I):
        raise ValueError('Unrecognized DOI syntax; preserve raw metadata for manual correction')
    return value.casefold()


def document_id(record):
    doi = doi_key(record.get('doi'))
    key = 'doi:' + doi if doi else 'url:' + str(record.get('url') or record.get('provider_id') or '')
    if not key[4:].strip():
        raise ValueError('A DOI, source URL or explicit provider ID is required')
    return 'DOC-' + digest(key.encode('utf-8'))[:16]


def add_records(library, records, raw_binding, search=None):
    if not isinstance(records,list):raise ValueError('Records must be a list of bibliographic objects')
    prepared=[]
    for position,record in enumerate(records):
        try:
            validate_metadata(record)
            if 'title' not in record:raise ValueError('Reported title is required')
            prepared.append((document_id(record),{**record,'doi':doi_key(record.get('doi'))}))
        except ValueError as exc:
            raise ValueError(f'record[{position}]: {exc}') from exc
    with flow.locked(library):
        data = load_library(library)
        indexed = {d['id']: d for d in data['documents']}
        added, linked = [], []
        for position, (identity, record) in enumerate(prepared):
            # Local PDF identities remain stable when a DOI is supplied later.
            # Only primary DOIs participate; unreviewed variants are not aliases.
            matches = [d for d in indexed.values() if record['doi'] and
                       doi_key(d['metadata'].get('doi')) == record['doi']]
            if len(matches) > 1:
                raise ValueError('Ambiguous registered primary DOI; needs manual review')
            if matches:
                if identity in indexed and indexed[identity] is not matches[0]:
                    raise ValueError('DOI and source identity disagree; needs manual review')
                identity = matches[0]['id']
            elif identity in indexed and record['doi']:
                primary = doi_key(indexed[identity]['metadata'].get('doi'))
                if primary and primary != record['doi']:
                    raise ValueError('DOI conflicts with registered source identity')
            binding = {**raw_binding, 'locator': f'record[{position}] or publication citation metadata'}
            if identity in indexed:
                document = indexed[identity]
                document['metadata_variants'].append({'record': record, 'source': binding})
                primary=document['metadata']
                placeholder=(primary.get('title_status')=='filename_placeholder_unverified'
                             and document.get('identity_status')!='verified')
                if placeholder:
                    primary.update(title=record['title'],title_status='source_reported_not_verified',title_source=binding)
                else:
                    document['metadata_conflict'] = document['metadata_conflict'] or record['title'].casefold() != primary['title'].casefold()
                linked.append(identity)
            else:
                document = {'id': identity, 'metadata': record,
                            'metadata_variants': [{'record': record, 'source': binding}],
                            'metadata_conflict': False, 'identity_status': 'source_reported_not_scientifically_verified',
                            'availability': 'not_retrieved', 'reading_status': 'not_reviewed',
                            'selection_status': 'candidate', 'selection_history': [],
                            'fulltexts': [], 'indexes': [], 'active_index': None}
                data['documents'].append(document)
                indexed[identity] = document
                added.append(identity)
        titles = defaultdict(list)
        for d in data['documents']:
            titles[re.sub(r'\W+', '', d['metadata']['title'].casefold())].append(d['id'])
        data['title_collision_review'] = [ids for ids in titles.values() if len(ids) > 1]
        if search:
            data['searches'].append(search)
        flow.commit(library, data)
    return {'added': added, 'linked_metadata_reports': linked, 'documents': len(data['documents']),
            'scope': 'Only identical DOI/source identity linked; title similarities remain review leads.'}


def import_records(library, file):
    source = Path(file)
    content = source.read_bytes()
    data = json.loads(content.decode('utf-8-sig'))
    if isinstance(data,list):records=data
    elif isinstance(data,dict) and 'records' in data:records=data['records']
    else:raise ValueError('Bibliographic import must be a record list or an object containing records')
    binding = blob(Path(library).resolve().parent, 'sources/raw', content, '.json')
    binding['origin'] = 'explicit_record_import'
    return add_records(library, records, binding)


def crossref_record(item):
    dates = {key: item[key] for key in ('published', 'published-print', 'published-online', 'issued') if key in item}
    return {'title': (item.get('title') or [''])[0], 'doi': item.get('DOI', ''),
            'authors': [' '.join(filter(None, (a.get('given'), a.get('family')))) for a in item.get('author', [])],
            'venue': (item.get('container-title') or [''])[0], 'date_fields': dates,
            'url': item.get('URL', ''), 'provider': 'Crossref', 'provider_type': item.get('type'),
            'fulltext_candidates': [link['URL'] for link in item.get('link', []) if link.get('content-type') == 'application/pdf'],
            'reported_license': item.get('license', []), 'related_links': item.get('relation', {})}


def discover(library, query, limit):
    if not 1 <= limit <= 100 or not query.strip():
        raise ValueError('Use a nonempty query and a bounded 1..100 result count')
    url = 'https://api.crossref.org/works?' + urlencode({'query.bibliographic': query, 'rows': limit})
    load_library(library)
    try:
        content, retrieval = http_get(url, 12 * 1024 * 1024)
        response = json.loads(content)
    except Exception as exc:
        return failed_attempt(library, 'crossref_discovery', exc, query=query, url=url, requested_limit=limit)
    records = [crossref_record(i) for i in response['message']['items']]
    binding = {**blob(Path(library).resolve().parent, 'sources/raw', content, '.json'), **retrieval, 'origin': 'crossref_search'}
    search = {**retrieval, 'provider': 'Crossref', 'query': query, 'requested_limit': limit,
              'returned_records': len(records), 'raw_source': binding,
              'coverage': 'Ranked bounded discovery, not exhaustive systematic search'}
    return add_records(library, records, binding, search)


class PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = defaultdict(list)
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            name = (attrs.get('name') or attrs.get('property') or '').casefold()
            if name.startswith('citation_') and attrs.get('content'):
                self.fields[name].append(attrs['content'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])


def publication_record(content, url):
    parser = PublicationMetadata()
    parser.feed(content.decode('utf-8', errors='replace'))
    fields = parser.fields
    first = lambda name: (fields.get(name) or [''])[0]
    if not first('citation_title'):
        raise ValueError('No citation_title metadata; use a reviewed explicit record import')
    dates = {key: value for key, value in fields.items() if key in {'citation_date', 'citation_publication_date', 'citation_online_date'}}
    return {'title': first('citation_title'), 'authors': fields.get('citation_author', []),
            'doi': first('citation_doi'), 'venue': first('citation_journal_title') or first('citation_conference_title'),
            'date_fields': dates, 'url': url, 'provider': 'publication_page',
            'fulltext_candidates': [urljoin(url, p) for p in fields.get('citation_pdf_url', [])],
            'page_links_candidates_not_verified_resources': sorted(set(urljoin(url, p) for p in parser.links if not p.startswith('#')))}


def capture_publication(library, url):
    load_library(library)
    try:
        content, retrieval = http_get(url)
        record = publication_record(content, retrieval['final_url'])
    except Exception as exc:
        return failed_attempt(library, 'publication_capture', exc, url=url)
    binding = {**blob(Path(library).resolve().parent, 'sources/raw', content, '.html'), **retrieval, 'origin': 'publication_page'}
    return add_records(library, [record], binding)


def document(data, identity):
    for item in data['documents']:
        if item['id'] == identity:
            return item
    raise ValueError('Unknown document ID: ' + identity)


def select_document(library, identity, status, reason):
    if status not in {'included', 'excluded', 'pending', 'candidate'} or not reason.strip():
        raise ValueError('Selection needs an explicit status and substantive reason')
    with flow.locked(library):
        data = load_library(library)
        item = document(data, identity)
        item['selection_status'] = status
        item['selection_history'].append({'status': status, 'reason': reason, 'at': flow.now()})
        flow.commit(library, data)
    return {'document_id': identity, 'selection_status': status, 'reason': reason}


def fetch_pdf(library, identity, url=None, max_mb=30):
    data = load_library(library)
    doc = document(data, identity)
    candidates = list(doc['metadata'].get('fulltext_candidates', []))
    for variant in reversed(doc['metadata_variants']):
        candidates.extend(url for url in variant['record'].get('fulltext_candidates', []) if url not in candidates)
    url = url or (candidates[0] if candidates else None)
    if not url:
        return failed_attempt(library, 'pdf_fetch', 'No public PDF candidate', document_id=identity)
    if not 1 <= max_mb <= 300:
        raise ValueError('Explicit PDF byte budget must be within 1..300 MiB')
    error = None
    try:
        content, retrieval = http_get(url, max_mb * 1024 * 1024)
        if not content.lstrip().startswith(b'%PDF-'):
            raise ValueError('Response is not a PDF; no login, paywall or HTML substitution attempted')
        binding = {**blob(Path(library).resolve().parent, 'sources/pdf', content, '.pdf'), **retrieval}
    except Exception as exc:
        error = str(exc)
        binding = None
    with flow.locked(library):
        data = load_library(library)
        doc = document(data, identity)
        attempt = {'document_id': identity, 'url': url, 'attempted_at': flow.now(), 'success': binding is not None}
        if binding:
            if not any(x['sha256'] == binding['sha256'] for x in doc['fulltexts']):
                doc['fulltexts'].append(binding)
            doc['availability'] = 'available'
        else:
            attempt['error'] = error
            if not doc['fulltexts']:
                doc['availability'] = 'not_retrieved'
        data['attempts'].append(attempt)
        flow.commit(library, data)
    return {'passed': binding is not None, 'document_id': identity, 'fulltext': binding, 'error': error,
            'reading_status': doc['reading_status']}


def pdf_blocks(path, backend='auto'):
    if backend in {'auto', 'pdfplumber'}:
        try:
            import pdfplumber
        except ImportError:
            if backend == 'pdfplumber':
                raise ValueError('pdfplumber is unavailable; use the bundled runtime or install the documented optional parser')
        else:
            pages = []
            with pdfplumber.open(path, laparams={'all_texts': True}) as pdf:
                for number, page in enumerate(pdf.pages, 1):
                    boxes = page.objects.get('textboxhorizontal', [])
                    blocks = [{'text': b.get('text', ''), 'bbox': [float(b[k]) for k in ('x0', 'top', 'x1', 'bottom')]}
                              for b in sorted(boxes, key=lambda b: (round(b['top'], 1), b['x0'])) if b.get('text', '').strip()]
                    source_chars = sum(len(str(c.get('text', '')).strip()) for c in page.chars)
                    extracted_chars = sum(len(re.sub(r'\s+', '', b['text'])) for b in blocks)
                    coverage_fallback = source_chars > 0 and extracted_chars < 0.85 * source_chars
                    if not blocks or coverage_fallback:
                        text = page.extract_text() or ''
                        if text.strip():
                            blocks = [{'text': text, 'bbox': None}]
                    pages.append({'page': number, 'width': float(page.width), 'height': float(page.height), 'blocks': blocks,
                                  'source_character_objects': source_chars,
                                  'extracted_nonspace_characters': sum(len(re.sub(r'\s+', '', b['text'])) for b in blocks),
                                  'coverage_fallback_used': coverage_fallback})
                    page.close()
            return pages, {'backend': 'pdfplumber', 'version': metadata.version('pdfplumber'),
                           'layout_policy': 'nested_all_texts_with_character_coverage_fallback_v2'}
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError('PDF indexing needs optional pdfplumber or pypdf; originals remain preserved') from exc
    reader = PdfReader(str(path))
    pages = [{'page': i, 'width': float(p.mediabox.width), 'height': float(p.mediabox.height),
              'blocks': [{'text': p.extract_text() or '', 'bbox': None}]} for i, p in enumerate(reader.pages, 1)]
    return pages, {'backend': 'pypdf', 'version': metadata.version('pypdf')}


def split_text(text, length=1800):
    start = 0
    while start < len(text):
        end = min(start + length, len(text))
        if end < len(text):
            boundary = text.rfind('\n', start + length // 2, end)
            if boundary > start:
                end = boundary + 1
        yield start, end, text[start:end]
        start = end


def index_pdf(library, identity, backend='auto'):
    data = load_library(library)
    doc = document(data, identity)
    if doc.get('metadata_conflict'):raise ValueError('Resolve document identity/metadata conflicts before changing its active index')
    if not doc['fulltexts']:
        raise ValueError('No retrieved original; metadata cannot substitute for PDF full text')
    source = doc['fulltexts'][-1]
    root = Path(library).resolve().parent
    path = core.inside(root, source['path'])
    if core.sha(path) != source['sha256']:
        raise ValueError('Original PDF hash changed')
    pages, engine = pdf_blocks(path, backend)
    blueprint = {'source_sha256': source['sha256'], 'engine': engine, 'pages': pages, 'chunk_chars': 1800}
    tag = digest(flow.dump(blueprint).encode('utf-8'))[:16]
    chunks = []
    sparse = []
    for page in pages:
        if sum(len(b['text'].strip()) for b in page['blocks']) < 40:
            sparse.append(page['page'])
        for b, block in enumerate(page['blocks'], 1):
            for c, (start, end, text) in enumerate(split_text(block['text']), 1):
                if not text.strip():
                    continue
                anchor = f"{identity}@{tag}:p{page['page']:04d}:b{b:04d}:c{c:03d}"
                labels = re.findall(r'(?im)\b(?:figure|fig\.|table|equation)\s*\d+[a-z]?|[图表式]\s*\d+(?:[-.]\d+)?', text)
                chunks.append({'anchor': anchor, 'document_id': identity, 'page': page['page'],
                               'block': b, 'chunk': c, 'block_char_start': start, 'block_char_end': end,
                               'bbox': block['bbox'], 'text': text, 'label_candidates': labels,
                               'label_status': 'text_mentions_not_verified_figure_bounds_or_formula_semantics'})
    index = {'schema': 'research-text-index/1.0', 'document_id': identity, 'source': source,
             'engine': engine, 'index_tag': tag, 'page_count': len(pages), 'chunks': chunks,
             'sparse_pages_needing_ocr_review': sparse, 'printed_page_numbers_verified': False,
             'page_extraction_checks': [{k: p.get(k) for k in ('page', 'source_character_objects', 'extracted_nonspace_characters', 'coverage_fallback_used')} for p in pages],
             'scope': 'Physical PDF pages and extracted text boxes; extraction is not full-text reading or semantic verification.'}
    binding = blob(root, 'indexes/' + identity, flow.dump(index).encode('utf-8'), '.json')
    binding.update(index_tag=tag, source_sha256=source['sha256'], page_count=len(pages), chunks=len(chunks), engine=engine)
    with flow.locked(library):
        data = load_library(library)
        doc = document(data, identity)
        if doc.get('metadata_conflict') or not doc['fulltexts'] or doc['fulltexts'][-1]['sha256']!=source['sha256']:
            raise ValueError('Identity or current original changed during indexing; active index is preserved')
        if not any(i['sha256'] == binding['sha256'] for i in doc['indexes']):
            doc['indexes'].append(binding)
        doc['active_index'] = binding['sha256']
        flow.commit(library, data)
    return {'document_id': identity, 'index': binding, 'sparse_pages_needing_ocr_review': sparse,
            'reading_status': doc['reading_status']}


def read_index(root, binding):
    path = core.inside(root, binding['path'])
    if not path.is_file() or core.sha(path) != binding['sha256']:
        raise ValueError('Index missing or changed; old anchors are not silently regenerated')
    index = core.load(path)
    source = core.inside(root, index['source']['path'])
    if not source.is_file() or core.sha(source) != index['source']['sha256']:
        raise ValueError('Bound original is missing or changed')
    return index


def tokens(text):
    text = re.sub(r'([a-z])[-\u00ad]\s*\n\s*([a-z])', r'\1\2', text.casefold())
    english = [w for w in re.findall(r'[a-z][a-z0-9_]*(?:-[a-z0-9]+)*', text) if w not in STOP]
    chinese = []
    for phrase in re.findall(r'[\u3400-\u9fff]+', text):
        chinese.extend([phrase] if len(phrase) < 2 else [phrase[i:i+2] for i in range(len(phrase)-1)])
    return english + chinese


def retrieve(library, question, terms=None, concepts=None, limit=10, per_document=3, pool=False, source_snapshot=None):
    maximum = 2000 if pool else 100
    if not question.strip() or not 1 <= limit <= maximum or not 1 <= per_document <= maximum:
        raise ValueError('A question and bounded retrieval limits are required')
    data = load_library(library,source_snapshot=source_snapshot)
    root = Path(library).resolve().parent
    docs = {d['id']: d for d in data['documents']}
    chunks, warnings, metadata_only = [], [], []
    for doc in docs.values():
        if doc.get('selection_status') != 'included':
            continue
        active = next((i for i in doc['indexes'] if i['sha256'] == doc['active_index']), None)
        if not active:
            metadata_only.append(doc['id'])
            continue
        try:
            index = read_index(root, active)
            chunks.extend({**chunk, 'source': index['source'], 'index_binding': active} for chunk in index['chunks'])
        except ValueError as exc:
            warnings.append({'document_id': doc['id'], 'error': str(exc)})
    query_terms = set(tokens(' '.join([question] + (terms or []))))
    groups = [set(tokens(' '.join(group))) for group in (concepts or [])]
    for group in groups:
        query_terms.update(group)
    bags = [Counter(tokens(c['text'])) for c in chunks]
    frequencies = Counter(term for bag in bags for term in bag)
    average = sum(sum(b.values()) for b in bags) / max(len(bags), 1) or 1
    scored = []
    for chunk, bag in zip(chunks, bags):
        matched = query_terms & bag.keys()
        if not matched:
            continue
        score = 0.0
        length = sum(bag.values())
        for term in matched:
            inverse = math.log(1 + (len(bags) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            count = bag[term]
            score += inverse * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average))
        group_hits = sum(bool(group & bag.keys()) for group in groups)
        if groups:
            score *= 0.5 + group_hits / len(groups)
        scored.append((score, chunk, sorted(matched), group_hits))
    scored.sort(key=lambda item: (-item[0], item[1]['anchor']))
    hits, counts = [], Counter()
    for score, chunk, matched, group_hits in scored:
        if counts[chunk['document_id']] >= per_document:
            continue
        counts[chunk['document_id']] += 1
        meta = docs[chunk['document_id']]['metadata']
        hits.append({'anchor': chunk['anchor'], 'document_id': chunk['document_id'], 'title': meta['title'],
                     'source_url': meta.get('url'), 'doi': meta.get('doi'), 'page': chunk['page'],
                     'bbox': chunk['bbox'], 'text': chunk['text'], 'score': score,
                     'matched_terms': matched, 'matched_concept_groups': group_hits,
                     'source': chunk['source'], 'index_binding': chunk['index_binding'],
                     'role': 'retrieved_candidate_requires_source_review'})
        if len(hits) >= limit:
            break
    return {'question': question, 'declared_terms': terms or [], 'concepts': concepts or [],
            'retrieval_policy': 'BM25-like lexical ranking with declared bilingual concepts and per-document diversity',
            'hits': hits, 'warnings': warnings, 'metadata_only_documents': metadata_only,
            'selection_scope': 'Only explicitly included documents are retrieved; candidates/excluded/pending remain registered separately',
            'evidence_status': 'candidates_found_not_entailment_verified' if hits else 'no_matching_fulltext_evidence',
            'matched_chunks': len(scored), 'pool_may_be_truncated': len(scored) > len(hits),
            'answer_generated': False, 'scope': 'Review these original passages before writing an answer; lexical score is not correctness, support or novelty.'}


def locate(library, anchor):
    data = load_library(library)
    root = Path(library).resolve().parent
    for doc in data['documents']:
        if not anchor.startswith(doc['id'] + '@'):
            continue
        for binding in doc['indexes']:
            if binding['index_tag'] != anchor.partition('@')[2].split(':', 1)[0]:
                continue
            index = read_index(root, binding)
            for chunk in index['chunks']:
                if chunk['anchor'] == anchor:
                    return {**chunk, 'source': index['source'], 'index_binding': binding,
                            'is_current_index': doc['active_index'] == binding['sha256'],
                            'original_path': str(core.inside(root, index['source']['path'])),
                            'metadata': doc['metadata'], 'page_definition': 'physical PDF page, one-based'}
    raise ValueError('Anchor not found in its preserved original/index version')


def render_anchor(library, anchor, output):
    value = locate(library, anchor)
    output = Path(output)
    if output.exists():
        raise FileExistsError('Preserve existing page preview')
    try:
        import pdfplumber
    except ImportError as exc:
        raise ValueError('Read-only page rendering needs optional pdfplumber and its renderer') from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(value['original_path']) as pdf:
        page = pdf.pages[value['page'] - 1]
        page.to_image(resolution=110).save(str(output), format='PNG')
    return {'preview': str(output.resolve()), 'anchor': anchor, 'page': value['page'], 'bbox': value['bbox'],
            'scope': 'Rendered original page, not a generated replacement or verified formula transcription.'}


def graph_view(library, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Export to a new graph version; preserve existing curation')
    data = load_library(library)
    root = Path(library).resolve().parent
    instance = data['instance_id']
    graph = {'schema': core.SCHEMA_VERSION, 'instance_id': instance, 'title': data['topic'],
             'nodes': [], 'edges': [], 'sources': []}
    source_ids = {}
    def add_source(binding):
        path = core.inside(root, binding['path'])
        if not path.is_file() or core.sha(path) != binding['sha256']:
            raise ValueError('Cannot export changed/missing source binding')
        key = binding['path'] + ':' + binding['sha256']
        if key not in source_ids:
            identity = core.qualified(instance, 'SRC-' + digest(key.encode())[:16])
            source_ids[key] = identity
            graph['sources'].append({'id': identity, 'local_path': binding['path'], 'sha256': binding['sha256']})
        return source_ids[key]
    def state(available):
        return {**{a: 'unknown' for a in core.AXES}, 'identity': 'source_reported',
                'availability': available, 'reading': 'not_reviewed'}
    for doc in data['documents']:
        if doc.get('selection_status') != 'included':
            continue
        identity = core.qualified(instance, doc['id'])
        refs = [{'source_id': add_source(v['source']), 'locator': v['source']['locator']} for v in doc['metadata_variants']]
        graph['nodes'].append({'id': identity, 'local_id': doc['id'], 'type': 'document',
                               'label': doc['metadata']['title'], 'instance_id': instance,
                               'source_refs': refs, 'state': state(doc['availability']), 'payload': doc['metadata']})
        for number, original in enumerate(doc['fulltexts'], 1):
            source_id = add_source(original)
            version = core.qualified(instance, doc['id'] + '-V' + str(number))
            binding = {'source_id': source_id, 'locator': 'Original PDF bytes; publication version not inferred'}
            graph['nodes'].append({'id': version, 'local_id': doc['id'] + '-V' + str(number), 'type': 'document_version',
                                   'label': doc['metadata']['title'] + ' / retrieved PDF ' + str(number),
                                   'source_refs': [binding], 'state': state('available'), 'instance_id': instance})
            graph['edges'].append({'id': core.qualified(instance, 'EV-' + doc['id'] + '-' + str(number)),
                                   'from': identity, 'to': version, 'relation': 'has_version', 'source_refs': [binding]})
        for indexed in doc['indexes']:
            if indexed['sha256'] != doc['active_index']:
                continue
            index = read_index(root, indexed)
            source_id = add_source(index['source'])
            add_source(indexed)
            for chunk in index['chunks']:
                eid = core.qualified(instance, chunk['anchor'])
                binding = {'source_id': source_id, 'locator': {'anchor': chunk['anchor'], 'pdf_page': chunk['page'],
                           'bbox': chunk['bbox'], 'block': chunk['block'], 'chars': [chunk['block_char_start'], chunk['block_char_end']],
                           'index_path': indexed['path'], 'index_sha256': indexed['sha256']}}
                graph['nodes'].append({'id': eid, 'local_id': chunk['anchor'], 'type': 'evidence',
                                       'label': f"{doc['id']} PDF p{chunk['page']} block {chunk['block']}",
                                       'source_refs': [binding], 'state': state('available'), 'instance_id': instance,
                                       'scope': 'Extracted passage candidate; no claim entailment asserted', 'payload': {'text': chunk['text']}})
                graph['edges'].append({'id': core.qualified(instance, 'HE-' + chunk['anchor']), 'from': identity, 'to': eid,
                                       'relation': 'has_evidence', 'source_refs': [binding]})
    report = core.validate(graph, root, True)
    if not report['passed']:
        raise ValueError('Derived graph failed provenance checks: ' + str(report['errors']))
    write_new(output, graph)
    return {**report, 'output': str(output.resolve()), 'reading_promoted': False,
            'semantic_support_edges_created': 0, 'source_root': str(root)}


def library_dependencies(data):
    """Enumerate original bindings, including materials held for identity review."""
    bindings = {}
    def add(binding):
        path, digest = binding['path'], binding['sha256']
        previous = bindings.get(path)
        if previous and previous['sha256'] != digest:
            raise ValueError('Conflicting library dependency hashes: ' + path)
        bindings[path] = {'path': path, 'sha256': digest, 'kind': 'other'}
        if binding.get('cache'):
            add(binding['cache'])
    for doc in data['documents']:
        for binding in [v['source'] for v in doc['metadata_variants']] + doc['fulltexts'] + doc['indexes']:
            add(binding)
    for review in data.get('intake_reviews', []):
        for key in ('fulltext', 'metadata_source'):
            add(review[key])
    return list(bindings.values())


def bind_to_flow(library, state):
    root = Path(library).resolve().parent
    if Path(state).resolve().parent != root:
        raise ValueError('Use a literature registry and workflow state in the same explicit project root')
    data = load_library(library)
    content = flow.dump(data).encode('utf-8')
    snapshot = root / ('literature_snapshot_' + digest(content) + '.json')
    if snapshot.exists():
        if snapshot.read_bytes() != content:
            raise ValueError('Immutable literature snapshot differs')
    else:
        with snapshot.open('xb') as stream:
            stream.write(content)
    bindings = {snapshot.name: {'path': snapshot.name, 'sha256': digest(content), 'kind': 'literature_registry'}}
    for binding in library_dependencies(data):
        if binding['path'] in bindings and bindings[binding['path']]['sha256'] != binding['sha256']:
            raise ValueError('Library snapshot dependency conflict')
        bindings[binding['path']] = binding
    for binding in bindings.values():
        path = core.inside(root, binding['path'])
        if not path.is_file() or core.sha(path) != binding['sha256']:
            raise ValueError('Cannot bind missing/changed library dependencies')
    registered, reused = [], []
    for binding in bindings.values():
        current = flow.read_state(state)
        found = next((a for a in current['artifacts'] if all(a.get(k) == binding[k] for k in ('path', 'sha256', 'kind'))), None)
        if found:
            reused.append(found['id'])
            continue
        identity = 'LIT-' + digest((binding['kind'] + binding['path'] + binding['sha256']).encode())[:16]
        flow.register_artifact(state, binding['path'], binding['kind'], identity,
                               'Explicit literature snapshot or its immutable original/index dependency')
        registered.append(identity)
    return {'registered': registered, 'reused': reused, 'snapshot': str(snapshot),
            'scope': 'Registers immutable library snapshots and dependencies; does not mark reading or stages complete.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'import', 'discover', 'publication', 'select', 'fetch', 'index', 'ask', 'locate', 'render', 'graph', 'bind', 'list'):
        p = commands.add_parser(name)
        p.add_argument('--library', required=True)
        if name == 'init':
            p.add_argument('--topic', required=True); p.add_argument('--id', required=True)
        if name == 'import': p.add_argument('--file', required=True)
        if name == 'discover': p.add_argument('--query', required=True); p.add_argument('--limit', type=int, default=10)
        if name == 'publication': p.add_argument('--url', required=True)
        if name in {'fetch', 'index', 'select'}: p.add_argument('--document', required=True)
        if name == 'select':
            p.add_argument('--status', choices=['included', 'excluded', 'pending', 'candidate'], required=True)
            p.add_argument('--reason', required=True)
        if name == 'bind': p.add_argument('--state', required=True)
        if name == 'fetch': p.add_argument('--url'); p.add_argument('--max-mb', type=int, default=30)
        if name == 'index': p.add_argument('--backend', choices=['auto', 'pdfplumber', 'pypdf'], default='auto')
        if name == 'ask':
            p.add_argument('--question'); p.add_argument('--plan'); p.add_argument('--terms', action='append', default=[])
            p.add_argument('--limit', type=int, default=10); p.add_argument('--per-document', type=int, default=3)
            p.add_argument('--output')
        if name in {'locate', 'render'}: p.add_argument('--anchor', required=True)
        if name in {'graph', 'render'}: p.add_argument('--output', required=True)
    p = commands.add_parser('corpus-search')
    p.add_argument('--manifest',required=True);p.add_argument('--root');p.add_argument('--question',required=True)
    p.add_argument('--terms',action='append',default=[]);p.add_argument('--limit',type=int,default=10)
    p.add_argument('--alias');p.add_argument('--include-flagged',action='store_true');p.add_argument('--output')
    p = commands.add_parser('inspect-pdf')
    p.add_argument('--pdf',required=True);p.add_argument('--pages',nargs='+',type=int);p.add_argument('--expected-script',choices=['unknown','cjk'],default='unknown')
    p.add_argument('--tables',action='store_true');p.add_argument('--output',required=True)
    p = commands.add_parser('ocr-pdf')
    p.add_argument('--pdf',required=True);p.add_argument('--pages',nargs='+',type=int,required=True)
    p.add_argument('--language',default='zh-Hans-CN');p.add_argument('--output',required=True)
    args = parser.parse_args()
    if args.command == 'init': out = initialize(args.library, args.topic, args.id)
    elif args.command == 'import': out = import_records(args.library, args.file)
    elif args.command == 'discover': out = discover(args.library, args.query, args.limit)
    elif args.command == 'publication': out = capture_publication(args.library, args.url)
    elif args.command == 'select': out = select_document(args.library, args.document, args.status, args.reason)
    elif args.command == 'bind': out = bind_to_flow(args.library, args.state)
    elif args.command == 'fetch': out = fetch_pdf(args.library, args.document, args.url, args.max_mb)
    elif args.command == 'index': out = index_pdf(args.library, args.document, args.backend)
    elif args.command == 'ask':
        plan = core.load(args.plan) if args.plan else {}
        out = retrieve(args.library, args.question or plan.get('question', ''), args.terms + plan.get('terms', []),
                       plan.get('concepts'), args.limit, args.per_document)
        if args.output:
            output = Path(args.output)
            if output.exists(): raise FileExistsError('Preserve existing retrieval record')
            write_new(output, out)
    elif args.command == 'locate': out = locate(args.library, args.anchor)
    elif args.command == 'render': out = render_anchor(args.library, args.anchor, args.output)
    elif args.command == 'graph': out = graph_view(args.library, args.output)
    elif args.command == 'corpus-search':
        import corpus_tools
        out=corpus_tools.search(args.manifest,args.question,args.terms,args.limit,args.alias,args.include_flagged,args.root)
        if args.output:write_new(args.output,out)
    elif args.command == 'inspect-pdf':
        import pdf_quality
        if Path(args.output).exists():raise FileExistsError('Preserve existing inspection')
        out=pdf_quality.inspect_pdf(args.pdf,args.pages,args.expected_script,args.tables)
        write_new(args.output,out)
        out={'output':str(Path(args.output).resolve()),'selected_pages':out['selected_pages'],'scope':out['scope']}
    elif args.command == 'ocr-pdf':
        import pdf_ocr
        value=pdf_ocr.recognize(args.pdf,args.pages,args.output,args.language)
        out={'output':str(Path(args.output).resolve()),'pages':[{'page':p['page'],'status':p['status']} for p in value['pages']],'scope':value['scope']}
    else:
        data = load_library(args.library)
        out = {'topic': data['topic'], 'revision': data['revision'],
               'documents': [{k: d[k] for k in ('id', 'metadata', 'availability', 'reading_status', 'selection_status', 'active_index', 'metadata_conflict')} for d in data['documents']],
               'title_collision_review': data['title_collision_review']}
    print(flow.dump(out), end='')
    return 1 if out.get('passed') is False else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(flow.dump({'passed': False, 'error': str(exc)}), end='')
        raise SystemExit(1)
