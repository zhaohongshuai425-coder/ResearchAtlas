"""Evidence review queue and explicit, scoped graph curation. No auto-adoption."""
from pathlib import Path
import argparse
import copy
import json
import core

def queue(graph_path,root):
    graph=core.load(graph_path);report=core.validate(graph,root,True)
    if not report['passed']:raise ValueError(report['errors'])
    by_id={n['id']:n for n in graph['nodes']};records=[]
    for method in [n for n in graph['nodes'] if n['type']=='method']:
        raw=method.get('payload',{}).get('record',{})
        docs=[by_id[e['from']] for e in graph['edges'] if e['relation']=='describes' and e['to']==method['id'] and by_id[e['from']]['type']=='document']
        evidence=[by_id[e['to']] for e in graph['edges'] if e['relation']=='has_evidence' and e['from'] in {d['id'] for d in docs}]
        records.append({'method_id':method['id'],'name':method['label'],'documents':[d['id'] for d in docs],
                        'inherited_mechanism':raw.get('mechanism'),'inherited_position':raw.get('replacement_position'),
                        'inherited_fit':raw.get('fit'),'inherited_limits':raw.get('limits'),
                        'evidence_ids':[e['id'] for e in evidence],
                        'questions_to_check':['What problem does the mechanism address?','What inputs, labels and permissions are required?',
                            'Which protocol and metrics support the claim?','Which limitations or counterevidence change the decision?'],
                        'decision':'not_reassessed_this_queue','review_status':'inherited_records_pending_scoped_recheck',
                        'source_refs':method.get('source_refs',[]),
                        'execution_scope':'Historical method metadata is not a current training result; consult saved experiment archive.'})
    return {'schema':'research-method-queue/1.0','base_graph_sha256':core.sha(graph_path),'methods':records,
            'scope':'Organizes all existing methods, including unknown fields. Does not re-read, invent support relations, select winners or refresh experiment statuses.'}

def apply_reviews(graph_path,review_path,root,output):
    output=Path(output)
    if output.exists():raise FileExistsError('Write a new graph version')
    root=Path(root).resolve();graph=core.load(graph_path);review=core.load(review_path)
    if review.get('schema')!='research-method-review/1.0':raise ValueError('Unsupported review schema')
    if review.get('base_graph_sha256')!=core.sha(graph_path):raise ValueError('Review/base graph version mismatch')
    checked=core.validate(graph,root,True)
    if not checked['passed']:raise ValueError(checked['errors'])
    graph=copy.deepcopy(graph);instance=graph['instance_id'];q=lambda x:core.qualified(instance,x)
    nodes={n['id']:n for n in graph['nodes']};local_review=Path(review_path).resolve()
    if not local_review.is_relative_to(root):raise ValueError('Review must be inside the declared source root')
    source_id=q('SRC-review-'+core.sha(local_review)[:16])
    graph['sources'].append({'id':source_id,'local_path':local_review.relative_to(root).as_posix(),'sha256':core.sha(local_review)})
    new_nodes=[];new_edges=[];seen=set(nodes);edge_ids={e['id'] for e in graph['edges']}
    def add_node(identity,kind,label,refs):
        if identity in seen:raise ValueError('New review IDs must not overwrite existing entities')
        if not str(label).strip():raise ValueError('Review text must be nonempty')
        seen.add(identity)
        new_nodes.append({'id':identity,'local_id':identity.split('::')[-1],'instance_id':instance,'type':kind,'label':label,
             'state':{**{a:'unknown' for a in core.AXES},'availability':'available','reading':'selected_passages_reviewed'},'source_refs':copy.deepcopy(refs)})
    def edge(identity,start,end,relation,refs,conditions):
        if identity in edge_ids:raise ValueError('Duplicate review relation ID')
        edge_ids.add(identity)
        new_edges.append({'id':identity,'from':start,'to':end,'relation':relation,'source_refs':copy.deepcopy(refs),
                          'conditions':conditions,'review_status':'new_selected_passage_review_not_whole_paper'})
    for record in review['reviews']:
        method_id=record['method_id']
        if method_id not in nodes or nodes[method_id]['type']!='method':raise ValueError('Review must bind a real existing method')
        prefix=record['id'];conditions=record['conditions']
        if not isinstance(conditions,list) or not conditions or not all(isinstance(c,str) and c.strip() for c in conditions):raise ValueError('Explicit conditions required')
        if record.get('decision_kind')!='evidence_scope_only':raise ValueError('This tool cannot adopt/replace a scientific model')
        relation=record.get('relation','supports')
        if relation not in {'supports','challenges'}:raise ValueError('Use an explicit support/challenge relation')
        if not record.get('reading_scope'):raise ValueError('Selected reading scope is required')
        refs=[{'source_id':source_id,'locator':{'review_id':prefix,'reading_scope':record['reading_scope']}}]
        docs={e['from'] for e in graph['edges'] if e['relation']=='describes' and e['to']==method_id}
        if record['document_id'] not in docs:raise ValueError('Evidence document is not the described source of this method')
        for evidence in record['evidence']:
            source=core.inside(root,evidence['path'])
            if not source.is_file() or core.sha(source)!=evidence['sha256']:raise ValueError('Review original missing/changed')
            inspection_path=core.inside(root,evidence['inspection']['path'])
            if core.sha(inspection_path)!=evidence['inspection']['sha256']:raise ValueError('Review inspection changed')
            inspection=core.load(inspection_path)
            if inspection['source_sha256']!=evidence['sha256'] or inspection.get('document_id')!=nodes[record['document_id']]['local_id']:
                raise ValueError('Inspection/original/document binding mismatch')
            pages={p['page']:p for p in inspection['pages']}
            n=evidence['page']
            if n not in pages or pages[n]['quality']['gate']=='hold_text_for_review':raise ValueError('Selected page lacks reliable candidate text; inspect or repair first')
            if not str(evidence.get('section','')).strip():raise ValueError('Section/figure locator is required')
            pdf_id=q('SRC-pdf-'+evidence['sha256'][:16])
            if not any(s['id']==pdf_id for s in graph['sources']):graph['sources'].append({'id':pdf_id,'local_path':evidence['path'],'sha256':evidence['sha256']})
            inspection_id=q('SRC-inspect-'+evidence['inspection']['sha256'][:16])
            if not any(s['id']==inspection_id for s in graph['sources']):graph['sources'].append({'id':inspection_id, 'local_path':evidence['inspection']['path'],'sha256':evidence['inspection']['sha256']})
            refs.extend([{'source_id':pdf_id,'locator':{'physical_pdf_page':n,'section':evidence['section']}},
                         {'source_id':inspection_id,'locator':{'page':n,'scope':'Machine text plus separately recorded visual review'}}])
        if len(refs)==1:raise ValueError('Source evidence is required')
        ids={k:q(prefix+'-'+k) for k in ['Q','CTX','EV','C','D']}
        add_node(ids['Q'],'question',record['question'],refs);add_node(ids['CTX'],'context','；'.join(conditions),refs)
        add_node(ids['EV'],'evidence',record['reading_scope'],refs);add_node(ids['C'],'claim',record['claim'],refs)
        add_node(ids['D'],'decision',record['decision'],refs)
        edge(q(prefix+'-question'),method_id,ids['Q'],'related_to',refs,conditions)
        edge(q(prefix+'-context'),ids['CTX'],method_id,'context_for',refs,conditions)
        edge(q(prefix+'-evidence'),record['document_id'],ids['EV'],'has_evidence',refs,conditions)
        if relation=='challenges':
            new_nodes[-2]['state']['adoption']='not_adopted'
            new_nodes[-2]['scope']='An explicitly challenged inference; not a supported project conclusion.'
        edge(q(prefix+'-support'),ids['EV'],ids['C'],relation,refs,conditions)
        edge(q(prefix+'-decision'),ids['D'],method_id,'decision_about',refs,conditions)
        edge(q(prefix+'-basis'),ids['D'],ids['C'],'derived_from',refs,conditions)
    graph['nodes'].extend(new_nodes);graph['edges'].extend(new_edges)
    graph['review_extension']={'base_graph_sha256':core.sha(graph_path),'review_sha256':core.sha(review_path),
                             'reading_promoted':False,'scope':'Selected-source evidence chains; original method and experiment states preserved.'}
    report=core.validate(graph,root,True)
    if not report['passed']:raise ValueError(report['errors'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(graph,f,ensure_ascii=False,indent=2)
    return {**report,'added_nodes':len(new_nodes),'added_relations':len(new_edges),'output':str(output.resolve())}

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    for name in ['queue','apply']:
        a=s.add_parser(name);a.add_argument('--graph',required=True);a.add_argument('--root',required=True);a.add_argument('--output',required=True)
        if name=='apply':a.add_argument('--reviews',required=True)
    a=p.parse_args()
    if a.command=='queue':
        if Path(a.output).exists():raise FileExistsError('Preserve old queue')
        value=queue(a.graph,a.root);Path(a.output).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');out={'methods':len(value['methods']),'output':a.output,'scope':value['scope']}
    else:out=apply_reviews(a.graph,a.reviews,a.root,a.output)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
