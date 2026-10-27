"""Bounded original-page follow-up. Expansion is not initial recall or reading.

All passages retain replayable original/index bindings. No missing-page list or
gold answer is needed to expand neighbours, and no assertion is auto-approved.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import sqlite3
import core
import evidence_search as es
import corpus_tools as ct


def expand(record_file, root, radius=1, max_pages=24, candidate_ids=None):
    root=Path(root).resolve();record_file=core.inside(root,Path(record_file).resolve().relative_to(root))
    if type(radius) is not int or not 0<=radius<=3:raise ValueError('Neighbour radius must be an integer in 0..3')
    if type(max_pages) is not int or not 1<=max_pages<=100:raise ValueError('Page budget must be in 1..100')
    parent_bytes=record_file.read_bytes();parent_sha=hashlib.sha256(parent_bytes).hexdigest()
    saved=json.loads(parent_bytes.decode('utf-8-sig'))
    if saved.get('schema')!=es.SCHEMA or saved.get('backend') not in ('library','corpus'):
        raise ValueError('A preserved original-passage query is required')
    if candidate_ids is not None and (not candidate_ids or len(set(candidate_ids))!=len(candidate_ids)):
        raise ValueError('Explicit candidate IDs must be nonempty and unique')
    available={c['candidate_id']:c for c in saved['candidates']}
    wanted=list(available) if candidate_ids is None else candidate_ids
    if set(wanted)-set(available):raise ValueError('Unknown parent candidate')
    seeds=[]
    for cid in wanted:
        replay=es.resolve(record_file,cid,root)
        c=replay['candidate']
        if c['kind']!='pdf_page' or type(c['page']) is not int:raise ValueError('Only original PDF pages can be expanded')
        seeds.append(c)
    # Rank-independent breadth first: all seed pages before radius-1 neighbours.
    requests=[];seen=set()
    for distance in range(radius+1):
        for c in seeds:
            native=c['native'];binding=native.get('source',native.get('source_binding',{}))
            for page in ([c['page']] if distance==0 else [c['page']-distance,c['page']+distance]):
                key=(c['document_id'],binding.get('sha256'),page)
                if page<1 or key in seen:continue
                seen.add(key);requests.append((c,page,key))
    out=copy.deepcopy(saved);out['candidates']=[];pages=[];missing=[];blocked=[];anchors=set();truncated=False
    for seed,page,key in requests:
        if len(pages)>=max_pages:
            truncated=True;break
        rows=[]
        if saved['backend']=='library':
            import literature_tools as lt
            native=seed['native'];folder=core.inside(root,seed['source_root'])
            idx=lt.read_index(folder,native['index_binding'])
            for chunk in idx['chunks']:
                if chunk['document_id']==seed['document_id'] and chunk['page']==page:
                    row=copy.deepcopy(seed)
                    for k in ('anchor','document_id','text','page','bbox'):row[k]=chunk[k]
                    row['native']={**copy.deepcopy(native),**{k:chunk[k] for k in ('anchor','document_id','text','page','bbox')}}
                    rows.append(row)
        else:
            snap=saved.get('source_snapshot')
            if not snap:raise ValueError('Corpus expansion requires its preserved source snapshot')
            binding=snap['search_index'];db=core.inside(root,binding['path'])
            if core.sha(db)!=binding['sha256']:raise ValueError('Preserved index changed')
            con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
            try:
                found=con.execute("SELECT * FROM item WHERE document_id=? AND kind='pdf_page' AND page=? ORDER BY id",(seed['document_id'],page)).fetchall()
            finally:con.close()
            for item in found:
                source_binding=json.loads(item['binding'])
                if source_binding.get('sha256')!=key[1]:continue
                indexed_quality=json.loads(item['quality'])
                if not isinstance(indexed_quality,dict) or indexed_quality.get('gate') not in {'candidate_text_only','hold_text_for_review'}:
                    raise ValueError('Preserved page quality gate is invalid')
                anchor=ct.row_anchor(snap,item,binding)
                row=copy.deepcopy(seed);row.update(anchor=anchor,page=page,text=item['body'])
                row['native']={**copy.deepcopy(seed['native']),'anchor':anchor,'page':page,'excerpt':item['body'],
                               'source_binding':source_binding,'quality':indexed_quality}
                row['source_bindings']=[source_binding];rows.append(row)
        if not rows:
            missing.append({'document_id':key[0],'source_sha256':key[1],'page':page,'reason':'no_indexed_page; may be boundary/held/absent'})
            continue
        pages.append({'document_id':key[0],'source_sha256':key[1],'page':page,'chunks':len(rows)})
        for row in rows:
            if row['anchor'] in anchors:continue
            anchors.add(row['anchor']);row['candidate_id']=hashlib.sha256((saved['backend']+'\0'+row['anchor']).encode()).hexdigest()[:24]
            row['score']=None;row.pop('fusion_score',None);row.pop('retrieval_channels',None)
            row['reading_state']='followup_page_not_reviewed';row['support_status']='candidate_requires_source_review'
            row['scientific_confidence']=None;row['followup_from']=seed['candidate_id']
            import pdf_quality
            indexed=row['native'].get('quality') if saved['backend']=='corpus' else None
            quality=(copy.deepcopy(indexed) if indexed and indexed['gate']=='hold_text_for_review' else
                     pdf_quality.text_quality(row['text'],indexed.get('expected_script','unknown') if indexed else 'unknown'))
            row['followup_text_quality']=quality
            if quality['gate']=='hold_text_for_review':
                blocked.append({'anchor':row['anchor'],'page':page,'reason':'held_text_requires_original_page_view',
                                'quality':quality})
                continue
            out['candidates'].append(row)
    out['query']={**saved['query'],'question':saved['query']['question'],'followup_radius':radius,'followup_max_pages':max_pages}
    if core.sha(record_file)!=parent_sha:raise ValueError('Parent query changed during follow-up')
    out['trace']={'method':'original-bound-neighbour-expansion','parent_record':{'path':record_file.relative_to(root).as_posix(),'sha256':parent_sha},
       'seed_candidate_ids':wanted,'radius':radius,'page_budget':max_pages,'pages':pages,'missing_index_pages':missing,
       'held_passages':blocked,'page_budget_truncated':truncated,'initial_ranking_changed':False}
    out['status']='followup_candidates_require_review';out['answer_generated']=False
    out['scope']='Additional original-page candidates only; excluded from initial ranking metrics. Expansion is not full reading, proof of complete coverage, or scientific support.'
    return out


def save_and_replay(record_file,root,output,**options):
    root=Path(root).resolve();output=core.inside(root,Path(output).resolve().relative_to(root))
    if output.exists():raise FileExistsError('Keep previous follow-up records')
    output.parent.mkdir(parents=True,exist_ok=True)
    parent=core.inside(root,Path(record_file).resolve().relative_to(root));raw=parent.read_bytes()
    digest=hashlib.sha256(raw).hexdigest();versions=output.parent/'_followup_inputs';versions.mkdir(exist_ok=True)
    snapshot=versions/(digest+'.json')
    try:
        with snapshot.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        if core.sha(snapshot)!=digest:raise ValueError('Preserved parent-query version changed')
    result=expand(snapshot,root,**options)
    result['trace']['parent_original_location']={'path':parent.relative_to(root).as_posix(),'sha256':digest,
       'role':'original location only; immutable parent_record is authoritative'}
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    checks=[es.resolve(output,c['candidate_id'],root)['replayed'] for c in result['candidates']]
    return {'record':output.relative_to(root).as_posix(),'sha256':core.sha(output),'pages':len(result['trace']['pages']),
       'passages':len(checks),'all_original_bindings_replayed':all(checks),'answer_generated':False,
       'page_budget_truncated':result['trace']['page_budget_truncated']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--record',required=True);p.add_argument('--root',required=True)
    p.add_argument('--output',required=True);p.add_argument('--radius',type=int,default=1);p.add_argument('--max-pages',type=int,default=24)
    a=p.parse_args();print(json.dumps(save_and_replay(a.record,a.root,a.output,radius=a.radius,max_pages=a.max_pages),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
