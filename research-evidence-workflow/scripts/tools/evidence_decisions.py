"""Evidence coverage and claim gates. Candidate ranking is never an answer.

The gate checks review completeness and original bindings, not scientific truth.
Entailment, negation, units and external validity require explicit scoped review.
"""
from pathlib import Path
import argparse
import json
import re
import struct
import zlib
import core
import evidence_search as es


def normalized(value):
    return re.sub(r'\s+', ' ', value).strip()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def checked_png(path):
    """Validate bounded PNG scanlines; decodability is not proof of reading."""
    path=Path(path)
    if path.stat().st_size>40*1024*1024:raise ValueError('Unexpected page preview size')
    data=path.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Original page preview must be a real PNG')
    offset=8;seen=[];compressed=[];idat_closed=False;palette=None
    while offset<len(data):
        if offset+12>len(data):raise ValueError('Truncated PNG')
        n=struct.unpack('>I',data[offset:offset+4])[0]
        kind=data[offset+4:offset+8];end=offset+12+n
        if len(kind)!=4 or any(not (65<=c<=90 or 97<=c<=122) for c in kind):raise ValueError('Invalid PNG chunk type')
        if end>len(data):raise ValueError('Invalid PNG chunk bounds')
        content=data[offset+8:offset+8+n]
        crc=struct.unpack('>I',data[offset+8+n:end])[0]
        if zlib.crc32(kind+content)&0xffffffff!=crc:raise ValueError('Invalid PNG chunk checksum')
        if not seen:
            if kind!=b'IHDR' or n!=13:raise ValueError('PNG header is missing')
            width,height=struct.unpack('>II',content[:8])
            if not 100<=width<=10000 or not 100<=height<=10000:raise ValueError('Not a usable page preview')
            depth,color,compression,filter_method,interlace=content[8:]
            allowed={0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
            if depth not in allowed.get(color,set()) or compression!=0 or filter_method!=0 or interlace not in (0,1):
                raise ValueError('Unsupported or invalid PNG header methods')
        elif kind==b'IHDR':raise ValueError('Duplicate PNG header')
        if kind==b'PLTE':
            if palette is not None or b'IDAT' in seen or color in (0,4) or not n or n%3 or n>768:
                raise ValueError('Invalid PNG palette')
            palette=n//3
            if color==3 and palette>2**depth:raise ValueError('PNG palette exceeds bit depth')
        if kind==b'IDAT':
            if idat_closed:raise ValueError('PNG IDAT chunks must be consecutive')
            if color==3 and palette is None:raise ValueError('Indexed PNG requires a palette')
            compressed.append(content)
        elif b'IDAT' in seen:idat_closed=True
        if kind==b'IEND' and n:raise ValueError('Invalid PNG end chunk')
        if kind not in (b'IHDR',b'PLTE',b'IDAT',b'IEND') and not kind[0]&32:
            raise ValueError('Unsupported PNG critical chunk')
        seen.append(kind);offset=end
        if kind==b'IEND':break
    if not seen or seen[-1]!=b'IEND' or b'IDAT' not in seen or offset!=len(data):
        raise ValueError('Incomplete PNG image')
    channels={0:1,2:3,3:1,4:2,6:4}[color]
    passes=[(0,0,1,1)] if interlace==0 else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    layouts=[]
    for x,y,dx,dy in passes:
        w=max(0,(width-x+dx-1)//dx);h=max(0,(height-y+dy-1)//dy)
        if w and h:layouts.append(((w*channels*depth+7)//8,h))
    expected=sum((row_bytes+1)*h for row_bytes,h in layouts)
    if expected>64*1024*1024:raise ValueError('Decoded PNG exceeds the 64 MiB preview budget')
    decoder=zlib.decompressobj()
    try:raw=decoder.decompress(b''.join(compressed),expected+1)
    except zlib.error as exc:raise ValueError('PNG image data cannot be decoded') from exc
    if len(raw)!=expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise ValueError('PNG decoded size or stream termination is invalid')
    cursor=0
    for row_bytes,h in layouts:
        for _ in range(h):
            if raw[cursor]>4:raise ValueError('Invalid PNG scanline filter')
            cursor+=row_bytes+1
    return {'format':'PNG','width':width,'height':height,'scanlines_verified':True,
            'rendered_original_content_certified':False}


def plan(file, root, output_dir=None):
    """Execute explicit subquestions independently; preserve missing document coverage."""
    root = Path(root).resolve()
    spec = core.load(file)
    if spec.get('schema') != 'research-evidence-plan/1.0' or not spec.get('requirements'):
        raise ValueError('Explicit question/document requirements required')
    result = []
    if output_dir:
        output_dir = core.inside(root, output_dir)
        if output_dir.exists():raise FileExistsError('Keep previous subquery records')
        output_dir.mkdir(parents=True)
    seen = set()
    for item in spec['requirements']:
        if not item.get('id') or item['id'] in seen or not item.get('question'):
            raise ValueError('Unique requirement IDs and nonempty subquestions required')
        seen.add(item['id'])
        options = {k: item[k] for k in ('terms', 'concepts', 'document_ids', 'alias') if k in item}
        options.update({k: spec[k] for k in ('semantic_index', 'model_dir') if spec.get(k)})
        for k in ('semantic_index', 'model_dir'):
            if k in options:
                options[k] = core.inside(root, options[k])
        query = es.query(spec['backend'], core.inside(root, spec['source']), item['question'], root,
                         original_only=True, limit=item.get('limit', 10), per_document=10, **options)
        present = {c['document_id'] for c in query['candidates']}
        missing = set(item.get('document_ids', [])) - present
        present_pages={(c['document_id'],c['page']) for c in query['candidates']}
        required_pages={(p['document_id'],p['page']) for p in item.get('required_pages',[])}
        missing_pages=[{'document_id':d,'page':p} for d,p in sorted(required_pages-present_pages)]
        binding = None
        if output_dir:
            target=output_dir/f'query_{len(result)+1}.json';core.save(target,query)
            binding={'path':target.relative_to(root).as_posix(),'sha256':core.sha(target)}
        result.append({'id': item['id'], 'question': item['question'], 'query': query,'query_binding':binding,
                       'missing_documents': sorted(missing),'missing_pages':missing_pages,'support_status': 'not_reviewed'})
    return {'schema': 'research-evidence-plan-result/1.0', 'requirements': result,
        'candidate_coverage_gaps': [i['id'] for i in result if i['missing_documents'] or i['missing_pages'] or not i['query']['candidates']],
        'answer_status': 'insufficient_reviewed_evidence', 'answer': None,
        'scope': 'Coverage is explicit but candidate presence is not support; inspect relevant neighbouring pages and methods/limits.'}


def audit(file, root, require_independent=False, independent_report=None):
    root = Path(root).resolve()
    spec = core.load(file)
    if (spec.get('schema') != 'research-claim-review/1.0' or not text(spec.get('claim'))
            or not isinstance(spec.get('parts'),list) or not spec['parts']):
        raise ValueError('A claim split into auditable parts is required')
    reviewer = spec.get('reviewer', {})
    if not text(reviewer.get('id')) or not text(reviewer.get('method')):
        raise ValueError('An explicit reviewer and review method are required')
    checked, gaps, ids = [], [], set()
    for part in spec['parts']:
        if not part.get('id') or part['id'] in ids:
            raise ValueError('Unique claim-part identities required')
        ids.add(part['id'])
        if part.get('decision') not in {'supported', 'challenged', 'unknown'}:
            raise ValueError('Unknown part decision')
        if not all(text(part.get(k)) for k in ('text', 'rationale')):
            raise ValueError('Each part needs its exact assertion and a substantive rationale')
        if part['decision'] == 'unknown':
            gaps.append({'part': part['id'], 'reason': part['rationale']})
            checked.append({'id': part['id'], 'decision': 'unknown'})
            continue
        if (not isinstance(part.get('conditions'),list) or not part['conditions']
                or not all(text(c) for c in part['conditions'])
                or not isinstance(part.get('citations'),list) or not part['citations']):
            raise ValueError('A positive/negative decision requires scope conditions and original citations')
        materials = part.get('material_types')
        if not isinstance(materials,list) or not materials or not set(materials) <= {'text', 'math', 'table', 'figure'}:
            raise ValueError('Declare the types of original material actually interpreted')
        citations = []
        for cite in part['citations']:
            query = core.inside(root, cite['query']['path'])
            if core.sha(query) != cite['query']['sha256']:
                raise ValueError('Query record changed')
            candidate = es.resolve(query, cite['candidate_id'], root)['candidate']
            if candidate['kind'] != 'pdf_page':
                raise ValueError('Metadata/graph candidates cannot support a source claim')
            quote = normalized(cite.get('excerpt', ''))
            if len(quote) < 12 or quote not in normalized(candidate['text']):
                raise ValueError('Evidence excerpt is absent from the original candidate')
            preview = cite.get('visual_check', {})
            original = candidate['native'].get('source', candidate['native'].get('source_binding'))
            if (preview.get('status') != 'visually_checked' or preview.get('page') != candidate['page']
                    or preview.get('source_sha256') != original['sha256']
                    or core.sha(core.inside(root, preview['path'])) != preview['sha256']):
                raise ValueError('Every interpreted original page requires an intact declared visual check')
            checked_png(core.inside(root,preview['path']))
            citations.append({'document_id': candidate['document_id'], 'page': candidate['page'],
                              'anchor': candidate['anchor'], 'source_sha256': original['sha256']})
        if not all(text(part.get(k)) for k in ('units_and_population', 'negation_and_alternatives', 'external_validity')):
            raise ValueError('Review units/population, negation/alternatives and transfer limits explicitly')
        if set(materials) & {'math', 'table', 'figure'}:
            check = part.get('structured_visual_review', {})
            if check.get('status') != 'verified_selected_elements' or not all(text(check.get(k))
                    for k in ('elements', 'transcription_or_values', 'definitions_and_units', 'assumptions', 'remaining_limits')):
                raise ValueError('Formula/table/figure interpretation requires selected-element visual transcription and conditions')
        checked.append({'id': part['id'], 'decision': part['decision'], 'citations': citations,
                        'conditions': part['conditions']})
    independent = spec.get('independent_review')
    if independent_report:
        if independent:raise ValueError('Use either an embedded or separate independent report')
        report_path=core.inside(root,independent_report)
        independent={'report':{'path':report_path.relative_to(root).as_posix(),'sha256':core.sha(report_path)}}
    independent_status = 'not_performed'
    if independent:
        binding = independent['report']
        report_path = core.inside(root, binding['path'])
        if core.sha(report_path) != binding['sha256']:
            raise ValueError('Independent review changed')
        review = core.load(report_path)
        if (review.get('schema') != 'research-independent-review/1.0'
                or not text(review.get('reviewer_id')) or review['reviewer_id']==reviewer['id']):
            raise ValueError('A separate identifiable reviewer is required')
        if review.get('reviewed_claim_sha256') != core.sha(file):
            # Independent report targets the immutable primary review excluding its later report link.
            primary = {k:v for k,v in spec.items() if k != 'independent_review'}
            import hashlib
            primary_sha = hashlib.sha256(json.dumps(primary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if review.get('reviewed_claim_sha256') != primary_sha:
                raise ValueError('Independent report targets another claim version')
        decisions = review.get('parts', [])
        if {d['id'] for d in decisions} != ids:
            raise ValueError('Independent review must cover every claim part')
        independent_status = 'agreed' if all(d.get('decision') == 'agree' and text(d.get('rationale')) for d in decisions) else 'disputed'
    if independent_status=='disputed':
        gaps.append({'part':'independent_review','reason':'Recorded disagreement must be resolved before exporting assertions'})
    if require_independent and independent_status != 'agreed':
        gaps.append({'part': 'independent_review', 'reason': independent_status})
    actual_pages={(c['document_id'],c['page']) for p in checked for c in p.get('citations',[])}
    for required in spec.get('required_evidence',[]):
        if (required['document_id'],required['page']) not in actual_pages:
            gaps.append({'part':'evidence_coverage','reason':'A declared required original page was not cited',
                         'document_id':required['document_id'],'page':required['page']})
    if not gaps and not text(spec.get('qualified_answer')):
        gaps.append({'part':'qualified_answer','reason':'A nonempty reviewed answer string is required for assertion export'})
    status = 'insufficient_reviewed_evidence' if gaps else ('reviewed_challenge' if any(p['decision']=='challenged' for p in checked) else 'reviewed_conditional_answer')
    return {'schema': 'research-claim-audit/1.0', 'claim': spec['claim'], 'status': status,
        'parts': checked, 'gaps': gaps, 'independent_status': independent_status,
        'answer': spec.get('qualified_answer') if not gaps else None,
        'can_export_assertion': not gaps and text(spec.get('qualified_answer')),
        'scientific_truth_certified': False,
        'scope': 'Checks the completeness/provenance of explicit judgments, not automatic entailment or expert certification.'}


def update_graph(graph_file, scoped_file, decisions, root, output, require_independent=True):
    """Add a scoped graph only after corresponding claim audits; keep the old graph."""
    import evidence_quality as eq
    import hashlib,os
    from file_commit import paths,commit_pair
    root=Path(root).resolve();output=core.inside(root,output)
    _,receipt,journal,_=paths(output)
    if ((output.exists() or receipt.exists()) and not journal.exists()
            and not (output.is_file() and receipt.is_file())):
        raise FileExistsError('Keep previous graph versions')
    output.parent.mkdir(parents=True,exist_ok=True)
    versions=output.parent/('_gsrc_'+hashlib.sha256(output.name.encode()).hexdigest()[:12])
    versions.mkdir(exist_ok=True)
    frozen_bindings={}
    def freeze(source,role,expected_sha=None):
        source=core.inside(root,source);data=source.read_bytes();sha=hashlib.sha256(data).hexdigest()
        if expected_sha is not None and sha!=expected_sha:raise ValueError('Review bytes differ from their declared version')
        target=versions/(role+'_'+sha[:20]+'.json')
        if target.exists():
            if core.sha(target)!=sha:raise ValueError('Preserved input version changed')
        else:
            with target.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        frozen_bindings[role]={'path':target.relative_to(root).as_posix(),'sha256':sha,
                               'original_path':source.relative_to(root).as_posix()}
        return target
    graph_file=freeze(graph_file,'base');scoped_file=freeze(scoped_file,'scope')
    scoped=eq.checked_reviews(scoped_file,root)
    if set(decisions)!={r['review']['id'] for r in scoped['reviews']}:
        raise ValueError('Every scoped graph interpretation requires its matching claim review')
    reports=[];frozen_decisions={}
    for row in scoped['reviews']:
        rid=row['review']['id'];item=decisions[rid]
        role_id=hashlib.sha256(rid.encode()).hexdigest()[:16]
        file=freeze(item['path'],'primary_'+role_id,item['sha256'])
        if core.load(file).get('independent_review'):
            raise ValueError('Graph publication requires an explicit separate independent_report binding; standalone audit still supports embedded reviews')
        frozen_item=dict(frozen_bindings['primary_'+role_id])
        report=None
        if item.get('independent_report'):
            b=item['independent_report'];report=freeze(b['path'],'independent_'+role_id,b['sha256'])
            frozen_item['independent_report']=dict(frozen_bindings['independent_'+role_id])
        frozen_decisions[rid]=frozen_item
        decision=audit(file,root,require_independent,report)
        if not decision['can_export_assertion'] or decision['claim']!=row['review']['claim']:
            raise ValueError('Unresolved or mismatched claim cannot create support/challenge edges')
        expected_decision='challenges' if decision['status']=='reviewed_challenge' else 'supports'
        if row['review']['decision']!=expected_decision:
            raise ValueError('Graph relation direction differs from the audited claim decision')
        conditions={c for part in decision['parts'] for c in part.get('conditions',[])}
        if not conditions<=set(row['review']['conditions']):
            raise ValueError('Graph interpretation dropped audited scope conditions')
        anchors={c['anchor'] for p in decision['parts'] for c in p.get('citations',[])}
        expected={c['anchor'] for c in row['candidates']}
        if not anchors or anchors!=expected:raise ValueError('Claim and graph must cite the exact same reviewed evidence set')
        reports.append(decision)
    # Build in memory and let the shared transaction own staging, publication,
    # completion receipt and retryable cleanup. No second cleanup lifecycle.
    for b in frozen_bindings.values():
        if core.sha(core.inside(root,b['path']))!=b['sha256']:raise ValueError('Frozen graph/scoped input changed')
    built=eq.add_to_graph(graph_file,scoped_file,root,output,return_graph=True)
    for b in frozen_bindings.values():
        if core.sha(core.inside(root,b['path']))!=b['sha256']:raise ValueError('Frozen graph/scoped input changed during insertion')
    graph=built['graph'];instance=graph['instance_id']
    oldgraph=core.load(graph_file);oldids={i['id'] for i in oldgraph['nodes']+oldgraph['edges']}
    for row in scoped['reviews']:
        rid=row['review']['id'];bindings=[frozen_decisions[rid]]
        if frozen_decisions[rid].get('independent_report'):bindings.append(frozen_decisions[rid]['independent_report'])
        refs=[]
        for b in bindings:
            sid=core.qualified(instance,'CLAIM-SRC-'+b['sha256'][:20])
            if sid not in {s['id'] for s in graph['sources']}:
                graph['sources'].append({'id':sid,'local_path':b['path'],'sha256':b['sha256']})
            refs.append({'source_id':sid,'locator':{'review_id':rid,'role':'scoped_claim_audit_not_truth_certificate'}})
        for item in graph['nodes']+graph['edges']:
            if item['id'] not in oldids and item.get('review_record_id')==rid:
                item['source_refs'].extend(refs)
                item['review_status']='scoped_claim_binding_checked_other_fields_not_independently_adjudicated'
    graph['review_input_versions']=frozen_bindings
    final=core.validate(graph,root,True)
    if not final['passed']:raise ValueError(final['errors'])
    payload=(json.dumps(graph,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    if json.loads(payload.decode('utf-8'))!=graph:raise ValueError('Serialized graph differs from validated graph')
    for b in frozen_bindings.values():
        if core.sha(core.inside(root,b['path']))!=b['sha256']:raise ValueError('Frozen input changed before publication')
    completion={'schema':'research-graph-commit/1.0',
        'graph':{'path':output.relative_to(root).as_posix(),'sha256':hashlib.sha256(payload).hexdigest()},
        'review_input_versions':frozen_bindings,'independent_required':require_independent,
        'scope':'Complete byte publication and scoped review checks; not scientific truth certification.'}
    receipt_bytes=(json.dumps(completion,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    publication=commit_pair(output,payload,receipt_bytes,allow_identical=True)
    return {**final,**publication,
            'receipt':{'path':receipt.relative_to(root).as_posix(),'sha256':hashlib.sha256(receipt_bytes).hexdigest()},
            'claim_audits':reports,'independent_required':require_independent,
            'scope':'Corresponding scoped judgments checked before graph insertion; not universal scientific validity or method adoption.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['plan','audit']);p.add_argument('--file',required=True)
    p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    p.add_argument('--require-independent',action='store_true');p.add_argument('--independent-report');a=p.parse_args()
    out=plan(a.file,a.root,Path(a.output).with_suffix('')) if a.command=='plan' else audit(a.file,a.root,a.require_independent,a.independent_report)
    target=Path(a.output)
    if target.exists():raise FileExistsError('Preserve earlier review versions')
    core.save(target,out)
    print(json.dumps({'output':str(target.resolve()),'status':out.get('status',out.get('answer_status'))},ensure_ascii=False))


if __name__=='__main__':main()
