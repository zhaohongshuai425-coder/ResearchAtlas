"""Portable evidence-aware knowledge registry. Uses Python standard library only.

The core does not know research domains, datasets, review budgets or local IDs.
Domain semantics are declared by profiles; local conventions stay in adapters.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import argparse, hashlib, json, re, sys

CORE_VERSION='0.1.0'
SCHEMA_VERSION='research-kb/1.0'
TYPES={'source','document','document_version','topic','question','context','method',
       'component','implementation','dataset','protocol','evaluation','run','observation',
       'evidence','claim','decision','plan','review','status'}
AXES={'identity','availability','reading','implementation','execution','validation','adoption'}
RELATIONS={
 'describes':({'document','document_version'},{'method','component','claim','context'}),
 'has_version':({'document','context','dataset','implementation','protocol'},{'document_version','context','dataset','implementation','protocol'}),
 'has_evidence':({'document','document_version','evaluation','observation','run'},{'evidence'}),
 'evaluated_by':({'context','method','implementation','component'},{'evaluation'}),
 'has_result':({'evaluation','run','dataset','implementation'},{'observation'}),
 'has_run':({'evaluation','protocol'},{'run'}),
 'supports':({'evidence','observation'},{'claim'}),
 'challenges':({'evidence','observation','claim'},{'claim'}),
 'context_for':(TYPES,TYPES),
 'derived_from':(TYPES,TYPES),
 'decision_about':({'decision'},TYPES),
 'needs_review':(TYPES,{'review'}),
 'related_to':(TYPES,TYPES),
}

def load(path):
    with Path(path).open(encoding='utf-8-sig') as f:return json.load(f)
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.new')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def qualified(instance,local):
    if not instance or '::' in instance:raise ValueError('instance_id must be nonempty and exclude ::')
    return instance+'::'+local
def inside(root,value):
    root=Path(root).resolve();path=(root/value).resolve()
    if not path.is_relative_to(root):raise ValueError('Local source escapes configured project root: '+str(value))
    return path
def root_from_manifest(manifest_path,override=None):
    m=load(manifest_path)
    return Path(override).resolve() if override else (Path(manifest_path).resolve().parent/m['workspace_root']).resolve()

def validate(graph,root=None,verify_sources=False):
    errors=[];warnings=[]
    if graph.get('schema')!=SCHEMA_VERSION:errors.append('Unsupported graph schema')
    instance=graph.get('instance_id','')
    try:qualified(instance,'check')
    except ValueError as e:errors.append(str(e))
    nodes={};sources={}
    for n in graph.get('nodes',[]):
        identity=n.get('id','')
        if identity in nodes:errors.append('Duplicate entity '+identity)
        nodes[identity]=n
        if not identity.startswith(instance+'::'):errors.append('Entity namespace mismatch '+identity)
        if n.get('type') not in TYPES:errors.append('Unknown entity type '+str(n.get('type')))
        if not str(n.get('label','')).strip():errors.append('Empty entity label '+identity)
        statuses=n.get('state',{})
        if set(statuses)!=AXES:errors.append('Independent state axes missing/unknown '+identity)
        for ref in n.get('source_refs',[]):
            if not ref.get('source_id'):errors.append('Entity source binding lacks source_id '+identity)
    for s in graph.get('sources',[]):
        if s['id'] in sources:errors.append('Duplicate source '+s['id'])
        sources[s['id']]=s
        if not s['id'].startswith(instance+'::'):errors.append('Source namespace mismatch '+s['id'])
        if not s.get('local_path') and not s.get('uri'):errors.append('Source has no locator '+s['id'])
        if verify_sources and s.get('local_path'):
            if root is None:errors.append('Source verification requires an explicit project root');continue
            try:
                path=inside(root,s['local_path'])
                if not path.is_file():errors.append('Missing source '+s['id'])
                elif not s.get('sha256') or sha(path)!=s['sha256']:errors.append('Source hash mismatch '+s['id'])
            except ValueError as e:errors.append(str(e))
        if s.get('uri') and not s.get('local_path'):warnings.append('Remote source access/content not verified: '+s['id'])
    for n in nodes.values():
        for ref in n.get('source_refs',[]):
            if ref.get('source_id') not in sources:errors.append('Dangling entity source '+n['id'])
    edgeids=set()
    for e in graph.get('edges',[]):
        if e['id'] in edgeids:errors.append('Duplicate relation '+e['id'])
        edgeids.add(e['id'])
        if e.get('from') not in nodes or e.get('to') not in nodes:errors.append('Orphan relation '+e['id']);continue
        rel=e.get('relation');pair=RELATIONS.get(rel)
        if not pair:errors.append('Unknown relation '+str(rel));continue
        if nodes[e['from']]['type'] not in pair[0] or nodes[e['to']]['type'] not in pair[1]:errors.append('Relation type mismatch '+e['id'])
        if not e.get('source_refs'):errors.append('Unsourced relation '+e['id'])
        for ref in e.get('source_refs',[]):
            if ref.get('source_id') not in sources:errors.append('Dangling relation source '+e['id'])
        if rel in ('supports','challenges'):
            if not e.get('conditions'):errors.append('Evidence relation lacks scope '+e['id'])
            if any(not ref.get('locator') for ref in e.get('source_refs',[])):errors.append('Evidence relation lacks evidence locator '+e['id'])
            if not e.get('review_status'):errors.append('Evidence relation lacks review state '+e['id'])
            state=nodes[e['from']]['state']
            if state.get('availability')=='missing':errors.append('Missing original promoted to evidence '+e['id'])
    return {'passed':not errors,'errors':errors,'warnings':warnings,'entities':len(nodes),'sources':len(sources),
            'relations':len(edgeids),'scope':'Structural/provenance validation does not certify scientific or domain correctness.'}

def compare(left,right,profile):
    """Explicit evaluation context; equality alone never proves causal attribution."""
    a=left.get('comparison_context',{});b=right.get('comparison_context',{})
    required=profile.get('comparison',{}).get('required_equal',[])
    optional=profile.get('comparison',{}).get('report_differences',[])
    if not required:return {'status':'NO_QUANTITATIVE_COMPARISON_PROFILE','comparable':False,
                            'reason':'This profile manages documentary evidence; no numeric evaluation contract is declared.'}
    unknown=lambda value:value is None or value=='' or value=='unknown' or value==[]
    missing=[key for key in required if unknown(a.get(key)) or unknown(b.get(key))]
    mismatch={key:[a.get(key),b.get(key)] for key in required if key not in missing and a.get(key)!=b.get(key)}
    differences={key:[a.get(key),b.get(key)] for key in optional if a.get(key)!=b.get(key)}
    if not left.get('instance_id') or not right.get('instance_id'):
        missing.append('instance_id')
    elif left.get('instance_id')!=right.get('instance_id'):
        mismatch['instance_id']=[left.get('instance_id'),right.get('instance_id')]
    return {'status':'MISSING_CONTEXT' if missing else 'CONTEXT_MISMATCH' if mismatch else 'DECLARED_CONTEXT_ALIGNED',
       'profile':profile.get('name'),'checked_dimensions':required,
       'left_id':left.get('id'),'right_id':right.get('id'),
       'comparable':not missing and not mismatch,'missing_fields':missing,'mismatched_fields':mismatch,
       'reported_differences':differences,'causal_attribution_certified':False,
       'scope':'Aligned declared context permits scoped description; substantive domain review remains necessary.'}

def search(graph,query,profile=None,kind=None,limit=10):
    """Domain-independent lexical lookup; graph/source references accompany every hit."""
    terms=[x.casefold() for x in query.split() if x]
    synonyms=(profile or {}).get('query_synonyms',{})
    groups=[[t]+[s.casefold() for s in synonyms.get(t,[])] for t in terms]
    found=[];sources={s['id']:s for s in graph['sources']}
    for n in graph['nodes']:
        if kind and n['type']!=kind:continue
        title=(n['label']+' '+n['local_id']).casefold()
        body=json.dumps(n.get('payload',{}),ensure_ascii=False).casefold()
        context=json.dumps(n.get('comparison_context',{}),ensure_ascii=False).casefold()
        texts=title+' '+body+' '+context
        if not groups or not all(any(t in texts for t in group) for group in groups):continue
        score=sum(4*sum(title.count(t) for t in group)+sum(body.count(t) for t in group) for group in groups)
        found.append({'id':n['id'],'type':n['type'],'label':n['label'],'score':score,
                      'state':n['state'],'source_refs':n['source_refs'],
                      'source_bindings':[{**sources[r['source_id']],**r} for r in n['source_refs'] if r['source_id'] in sources],
                      'scope':n.get('scope')})
    found.sort(key=lambda x:(-x['score'],x['id']))
    return {'hits':found[:limit],'matched_entities':len(found),'rank_policy':'declared_terms_and_optional_profile_synonyms_no_collection_or_project_preference',
            'scope':'Entity lookup, not semantic entailment or a generated evidence-backed answer.'}

def impact(graph,root):
    changed=[]
    for s in graph['sources']:
        if not s.get('local_path'):continue
        try:
            p=inside(root,s['local_path'])
            if not p.is_file() or not s.get('sha256') or sha(p)!=s['sha256']:changed.append(s['id'])
        except ValueError:changed.append(s['id'])
    affected={n['id'] for n in graph['nodes'] if any(r['source_id'] in changed for r in n['source_refs'])}
    affected.update(z for e in graph['edges'] if any(r['source_id'] in changed for r in e['source_refs']) for z in (e['from'],e['to']))
    direct=sorted(affected)
    for _ in range(2):
        before=set(affected);affected.update(e['to'] for e in graph['edges'] if e['from'] in before)
    return {'changed_sources':changed,'direct_entities':direct,'review_queue':sorted(affected),
            'action':'review_required' if changed else 'no_registered_byte_change','automatic_claim_rewrite':False}

def initialize(folder,instance_id,title):
    folder=Path(folder).resolve()
    if folder.exists():raise FileExistsError('Preserve existing instance: '+str(folder))
    qualified(instance_id,'test');folder.mkdir(parents=True)
    graph={'schema':SCHEMA_VERSION,'instance_id':instance_id,'title':title,'nodes':[],'edges':[],'sources':[]}
    save(folder/'registry.json',graph)
    save(folder/'instance.json',{'instance_id':instance_id,'title':title,'workspace_root':'.','registry':'registry.json',
        'domain_profile':None,'permissions':{'raw_sources':'read_only','views':'explicit_build','external_actions':'separate_authorization'},
        'scope':'New empty instance; no example facts, sources, reading or experiments fabricated.'})
    return {'created':str(folder),'instance_id':instance_id,'entities':0}

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('init');p.add_argument('folder');p.add_argument('--instance-id',required=True);p.add_argument('--title',required=True)
    for name in ('validate','search','compare','impact'):
        p=sub.add_parser(name);p.add_argument('--graph',required=True);p.add_argument('--manifest');p.add_argument('--root')
        if name=='validate':p.add_argument('--verify-sources',action='store_true')
        if name=='search':p.add_argument('query');p.add_argument('--profile');p.add_argument('--type');p.add_argument('--limit',type=int,default=10)
        if name=='compare':p.add_argument('left');p.add_argument('right');p.add_argument('--profile',required=True)
    a=ap.parse_args()
    if a.command=='init':out=initialize(a.folder,a.instance_id,a.title)
    else:
        g=load(a.graph)
        if a.command in ('validate','impact'):
            root=root_from_manifest(a.manifest,a.root) if a.manifest else Path(a.root).resolve() if a.root else None
            if a.command=='impact' and root is None:raise ValueError('impact requires --manifest or --root')
            out=validate(g,root,a.verify_sources) if a.command=='validate' else impact(g,root)
        elif a.command=='search':out=search(g,a.query,load(a.profile) if a.profile else None,a.type,a.limit)
        elif a.command=='compare':
            ns={n['id']:n for n in g['nodes']};out=compare(ns[a.left],ns[a.right],load(a.profile))
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 1 if out.get('passed') is False else 0

if __name__=='__main__':raise SystemExit(main())
