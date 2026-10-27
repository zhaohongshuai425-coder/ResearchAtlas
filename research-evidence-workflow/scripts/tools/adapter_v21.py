"""Project adapter only: map a legacy graph to a portable, namespaced registry.

This is where legacy field names and the thesis evaluation protocol belong.
It reads existing JSON; it never imports the legacy experiment/maintenance code.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json, sys
from core import AXES, SCHEMA_VERSION, load, save, sha, qualified, inside, root_from_manifest, validate

TYPE_MAP={'papers':'document','reports':'document_version','evidence':'evidence','methods':'method',
 'scenes':'context','scene_versions':'context','modules':'component','datasets':'dataset',
 'experiments':'evaluation','runs':'run','results':'observation','claims':'claim',
 'implementations':'implementation','directions':'topic','research_plans':'plan','status':'status'}
REL_MAP={'DESCRIBES_CANDIDATE':'describes','HAS_REPORT':'has_version','HAS_SUPPLEMENT_REPORT':'has_version',
 'HAS_ORIGINAL_EVIDENCE':'has_evidence','HAS_SCENE_VERSION':'has_version','EVALUATED_BY':'evaluated_by',
 'HAS_RECORDED_RUN':'has_run','HAS_SAVED_RESULT':'has_result','HAS_OBSERVED_RESULT':'has_result',
 'SUPPORTS_SCOPED_CLAIM':'supports','CHALLENGES_GENERALIZATION':'challenges'}

def adapt(manifest_path,root_override=None):
    manifest=load(manifest_path);root=root_from_manifest(manifest_path,root_override)
    instance=manifest['instance_id'];legacy=load(inside(root,manifest['legacy_graph']))
    source_map={};sources=[]
    def source_binding(ref):
        path=ref['path'];key=(path,ref.get('sha256'))
        if key not in source_map:
            sid=qualified(instance,'src-'+hashlib.sha256((path+'|'+str(ref.get('sha256'))).encode()).hexdigest()[:18])
            source_map[key]=sid
            sources.append({'id':sid,'local_path':path,'sha256':ref.get('sha256'),
                'source_date':ref.get('source_date'),'role':ref.get('source_role',ref.get('role')),
                'access':'recorded_local_bytes','source_version':'sha256_bytes_not_signature'})
        return {'source_id':source_map[key],'locator':ref.get('locator') or 'registered source record',
                'scope':ref.get('scope') or 'Original scope and dates retained; no new reading or execution.'}
    # Include registered files even if they have no direct current graph relation.
    for s in legacy['sources']:source_binding(s)
    nodes=[];edges=[]
    for n in legacy['nodes']:
        if n['type'] not in TYPE_MAP:raise ValueError('Unmapped legacy type '+n['type'])
        state={axis:'unknown' for axis in AXES};d=n['details']
        if n['type']=='papers':
            state['identity']=d.get('identity_status','unknown')
            state['availability']='missing' if d.get('read_status')=='MISSING_ORIGINAL_DO_NOT_USE' else d.get('current_asset_status','recorded_not_rechecked')
            state['reading']=d.get('read_status','unknown')
            state['implementation']='historical_metadata_only_see_project_implementation'
        elif n['type']=='results':state['validation']=n['status']
        # Completion in one axis never fills another axis.
        item={'id':qualified(instance,n['id']),'local_id':n['id'],'instance_id':instance,
          'type':TYPE_MAP[n['type']],'label':n['label'],'state':state,
          'source_refs':[source_binding(s) for s in n['sources']],
          'scope':d.get('scope') or d.get('boundary') or 'Retain source-specific conditions and historical scope.',
          'payload':{'legacy_type':n['type'],'legacy_status':n['status'],'record':d}}
        if 'comparison_contract' in d:
            c=d['comparison_contract']
            item['comparison_context']={
                'task_unit':c.get('prediction_unit'),'label_semantics':c.get('label_policy'),
                'data_identity':c.get('dataset_source','')+'/'+c.get('dataset',''),
                'data_version':c.get('time_protocol'),'evaluation_partition':c.get('split'),
                'population_slice':c.get('stratum'),'information_permission':c.get('bank_visibility'),
                'decision_budget_rule':c.get('budget_rule'),'decision_budget':c.get('review_budget'),
                'eligible_population':c.get('eligible_account_days'),'positive_population':c.get('positive_account_days'),
                'replicate_ids':c.get('seeds'),'metric_definition':'positive_account_day_hits_at_declared_review_budget',
                'feature_information':c.get('information_scope'),'supervision':c.get('supervision'),
                'search_exposure':c.get('search_exposure'),'uncertainty_unit':c.get('uncertainty')}
        nodes.append(item)
    for e in legacy['edges']:
        edges.append({'id':qualified(instance,e['id']),'from':qualified(instance,e['from']),
          'to':qualified(instance,e['to']),'relation':REL_MAP.get(e['relation'],'context_for'),
          'source_refs':[source_binding(s) for s in e['source_refs']],
          'conditions':e['conditions'] or ['Context/navigation only; not scientific support.'],
          'review_status':'inherited_source_scoped_record_not_new_review',
          'payload':{'legacy_relation':e['relation'],'interpretation':e['interpretation'],'checked_at':e['checked_at']}})
    graph={'schema':SCHEMA_VERSION,'instance_id':instance,'title':manifest['title'],
        'domain_profile':manifest['domain_profile'],'generated_at':datetime.now(timezone.utc).isoformat(),
        'nodes':nodes,'edges':edges,'sources':sources,
        'adaptation':{'source_graph':manifest['legacy_graph'],'source_graph_sha256':sha(inside(root,manifest['legacy_graph'])),
          'legacy_node_count':len(legacy['nodes']),'legacy_edge_count':len(legacy['edges']),
          'source_facts_or_statuses_promoted':False,'scientific_execution':False,
          'scope':'Compatibility projection, not a new semantic review or proof of cross-domain scientific generalization.'}}
    result=validate(graph,root,True)
    if not result['passed']:raise ValueError(result['errors'])
    return graph

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',required=True);p.add_argument('--root');p.add_argument('--output',required=True)
    a=p.parse_args();manifest=load(a.manifest);root=root_from_manifest(a.manifest,a.root)
    output=inside(root,a.output)
    source=inside(root,manifest['legacy_graph'])
    if output!=inside(root,manifest['derived_output']):raise ValueError('Output must be the derived view declared by this instance manifest')
    if output==source:raise ValueError('Do not overwrite source graph')
    g=adapt(a.manifest,a.root);save(output,g)
    print(json.dumps({'output':str(output),'entities':len(g['nodes']),'relations':len(g['edges']),'sources':len(g['sources']),'status':'PORTABLE_PROJECTION_VALIDATED'},ensure_ascii=False))

if __name__=='__main__':main()
