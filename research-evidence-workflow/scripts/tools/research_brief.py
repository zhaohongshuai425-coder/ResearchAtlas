"""Bounded, read-only research summaries. Full originals remain the evidence source."""
from pathlib import Path
from collections import Counter
import argparse,json,sys
import core,research_flow

def clip(value,limit=180):
    value=str(value)
    return value if len(value)<=limit else value[:limit]+'… [text omitted]'

def tally(values,limit=20):
    counts=Counter(values)
    return {'counts':dict(counts.most_common(limit)),'distinct_total':len(counts),
            'omitted_categories':max(0,len(counts)-limit)}

def status(path,limit=10):
    result=research_flow.check_state(path)
    problems=[{'id':ident,**row} for ident,row in result['artifacts'].items() if row['status']!='intact']
    return {'schema':'research-brief/1.0','kind':'status','read_only':True,
        'source':{'path':str(Path(path).resolve()),'sha256':core.sha(path)},
        'passed':result['passed'],'project_id':result['project_id'],'topic':clip(result['topic'],240),
        'handoff_ready':result['handoff_ready'],
        'dependency_issue_count':len(result['dependencies']['issues']),
        'dependency_issues':result['dependencies']['issues'][:limit],
        'dependency_issues_omitted':max(0,len(result['dependencies']['issues'])-limit),
        'dependency_warning_count':len(result['dependencies'].get('context_warnings',[])),
        'dependency_warnings':result['dependencies'].get('context_warnings',[])[:limit],
        'dependency_warnings_omitted':max(0,len(result['dependencies'].get('context_warnings',[]))-limit),
        'revision':result['revision'],'stage_records_complete':result['stage_records_complete'],
        'stage_records_total':result['stage_records_total'],'next_unfinished_stage':result['next_unfinished_stage'],
        'stages':{key:{'label':row['label'],'recorded_status':row['status'],
            'effective_status':row['effective_status'],'reason':clip(row.get('reason','')),
            'reason_truncated':len(str(row.get('reason','')))>180,
            'evidence_count':len(row.get('evidence_ids',[]))} for key,row in result['stages'].items()},
        'artifact_counts':tally(row['status'] for row in result['artifacts'].values()),
        'artifact_problem_count':len(problems),'artifact_problems':problems[:limit],
        'artifact_problems_omitted':max(0,len(problems)-limit),
        'issues_count':len(result['issues']),'issues':result['issues'][:limit],
        'issues_omitted':max(0,len(result['issues'])-limit),
        'note_count':len(result['notes']),'notes_not_expanded':True,
        'scope':result['scope'],'scientific_validity_certified':False}

def graph(path,root=None,node=None,offset=0,limit=5,include_payload=False,verify_sources=False):
    if include_payload and not node:raise ValueError('Choose one exact --node before requesting payload')
    data=core.load(path)
    checked=core.validate(data,root,verify_sources)
    nodes=data.get('nodes',[]);edges=data.get('edges',[])
    selected=[n for n in nodes if n.get('id')==node] if node else nodes
    if node and not selected:raise ValueError('Requested exact node does not exist')
    page=selected[offset:offset+limit]
    brief=[]
    for n in page:
        refs=n.get('source_refs',[])
        row={k:clip(n.get(k,''),240) for k in ('id','type','label')}
        row['state']={k:clip(n.get('state',{}).get(k,'unknown'),100) for k in sorted(core.AXES)}
        row['source_ref_count']=len(refs)
        row['source_refs']=[{'source_id':r.get('source_id'),'locator_preview':clip(json.dumps(r.get('locator',{}),ensure_ascii=False),300)} for r in refs[:2]]
        row['source_refs_omitted']=max(0,len(refs)-2)
        row['payload_keys']=list(n.get('payload',{}))[:12] if isinstance(n.get('payload',{}),dict) else []
        row['payload_expanded']=include_payload
        if include_payload:
            payload=json.dumps(n.get('payload'),ensure_ascii=False)
            row['payload_preview']=clip(payload,1200);row['payload_preview_truncated']=len(payload)>1200
        brief.append(row)
    local_edges=[e for e in edges if not node or node in (e.get('from'),e.get('to'))]
    return {'schema':'research-brief/1.0','kind':'graph','read_only':True,
        'source':{'path':str(Path(path).resolve()),'sha256':core.sha(path)},
        'passed':checked['passed'],'source_verification':'local_file_hashes_checked' if verify_sources else 'not_requested',
        'verification_scope':checked['scope'],'errors_count':len(checked['errors']),
        'errors':[clip(x,240) for x in checked['errors'][:10]],'errors_omitted':max(0,len(checked['errors'])-10),
        'warnings_count':len(checked['warnings']),'warnings':[clip(x,240) for x in checked['warnings'][:10]],
        'node_total':len(nodes),'edge_total':len(edges),'source_total':len(data.get('sources',[])),
        'node_type_counts':tally(n.get('type','unknown') for n in nodes),
        'relation_counts':tally(e.get('relation','unknown') for e in edges),
        'selected_node_total':len(selected),'offset':offset,'shown_nodes':len(brief),
        'next_offset':offset+len(page) if offset+len(page)<len(selected) else None,'nodes':brief,
        'selected_edge_total':len(local_edges),
        'edge_preview':[{k:clip(e.get(k,''),240) for k in ('id','from','to','relation')} for e in local_edges[:limit]],
        'edge_preview_omitted':max(0,len(local_edges)-limit),'full_graph_exported':False,
        'scope':'Whole graph counted and structurally checked; displayed subset is paginated. A preview is not full reading, evidence approval or method adoption.'}

def library(path,offset=0,limit=10):
    import literature_tools
    data=literature_tools.load_library(path);docs=data['documents']
    rows=[]
    for d in docs[offset:offset+limit]:
        rows.append({'id':d['id'],'title':clip(d.get('metadata',{}).get('title',''),240),
            **{key:d.get(key,'unknown') for key in ('reading_status','selection_status','availability')},
            'fulltext_versions':len(d.get('fulltexts',[])),'index_versions':len(d.get('indexes',[]))})
    return {'schema':'research-brief/1.0','kind':'library','read_only':True,
        'source':{'path':str(Path(path).resolve()),'sha256':core.sha(path)},
        'registered_documents_total':len(docs),'offset':offset,'shown_documents':len(rows),
        'next_offset':offset+len(rows) if offset+len(rows)<len(docs) else None,
        'reading_state_counts':tally(str(d.get('reading_status','unknown')) for d in docs),
        'documents':rows,'passed':True,'original_hashes_replayed':False,
        'scope':'Registry metadata summary; all records counted, displayed page is not the complete consideration set. No new reading status inferred.'}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-chars',type=int,default=16000)
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('status');p.add_argument('--state',required=True);p.add_argument('--limit',type=int,default=10)
    p=commands.add_parser('graph');p.add_argument('--graph',required=True);p.add_argument('--root');p.add_argument('--node')
    p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=5)
    p.add_argument('--include-payload',action='store_true');p.add_argument('--verify-sources',action='store_true')
    p=commands.add_parser('library');p.add_argument('--library',required=True);p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=10)
    args=parser.parse_args(argv)
    if not 1<=args.limit<=50 or getattr(args,'offset',0)<0:raise ValueError('Use limit 1..50 and nonnegative offset')
    if not 1000<=args.max_chars<=64000:raise ValueError('Use max-chars 1000..64000')
    if args.command=='status':out=status(args.state,args.limit)
    elif args.command=='library':out=library(args.library,args.offset,args.limit)
    else:out=graph(args.graph,args.root,args.node,args.offset,args.limit,args.include_payload,args.verify_sources)
    text=json.dumps(out,ensure_ascii=False,separators=(',',':'))
    if len(text)>args.max_chars:raise ValueError('Summary exceeds output budget; use a smaller --limit or one exact --node. No full graph was emitted.')
    print(text)
    return 0 if out['passed'] else 1
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({'passed':False,'error':clip(exc,500)},ensure_ascii=False));raise SystemExit(1)
