"""Configurable offline prototype acceptance; no agent, model API or training.

Uses supplied original indices. Functional replay is not answer accuracy, full
reading, physical-host confirmation, or a test of formal Skill discovery.
"""
from pathlib import Path
import argparse,hashlib,json,platform,sys,uuid
from datetime import datetime,timezone

def run(folder):
    folder=Path(folder).resolve()
    def inside(value):
        p=(folder/value).resolve()
        if not p.is_relative_to(folder):raise ValueError('Package path escapes root')
        return p
    def sha(p):
        value=hashlib.sha256()
        with Path(p).open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
        return value.hexdigest()
    spec=json.loads((folder/'prototype_manifest.json').read_text(encoding='utf-8'))
    if spec.get('schema')!='research-prototype-package/1.0':raise ValueError('Unsupported package')
    problems=[]
    for name,digest in spec['files'].items():
        p=inside(name)
        if not p.is_file() or sha(p)!=digest:problems.append(name)
    report={'schema':'research-prototype-acceptance/1.0','utc':datetime.now(timezone.utc).isoformat(),
      'runtime':{'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'prefix':sys.prefix,'base_prefix':sys.base_prefix},
      'integrity_problems':problems,'cases':[],'physical_other_machine_confirmed':False,
      'skill_installed_or_triggered':False,'semantic_accuracy_tested':False}
    if not problems:
        sys.path.insert(0,str(inside('tools')))
        import core,evidence_search as es,evidence_followup as ef,evidence_decisions as ed,research_flow as flow
        out=inside('_runs/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]);out.mkdir(parents=True)
        for index,case in enumerate(spec['projects']):
            library=inside(case['library']);state=inside(case['state'])
            query=es.query('library',library,case['question'],folder,terms=case.get('terms',[]),limit=5,per_document=3,original_only=True)
            qp=out/f'query_{index}.json';core.save(qp,query)
            replay=[es.resolve(qp,c['candidate_id'],folder)['replayed'] for c in query['candidates']]
            follow=ef.save_and_replay(qp,folder,out/f'followup_{index}.json',radius=1,max_pages=6)
            state_check=flow.check_state(state)
            row={'id':case['id'],'candidates':len(replay),'original_replays_passed':bool(replay) and all(replay),
              'followup_passed':follow['all_original_bindings_replayed'],'followup_pages':follow['pages'],
              'flow_state_passed':state_check['passed'],'answer_generated':False}
            row['passed']=row['original_replays_passed'] and row['followup_passed'] and row['flow_state_passed']
            report['cases'].append(row)
        missing=out/'missing.json';core.save(missing,{'schema':'research-claim-review/1.0','reviewer':{'id':'prototype-check','method':'missing-evidence behavior probe'},
          'claim':'An unperformed experiment achieved a benefit','parts':[{'id':'experiment','text':'A new measurement improved performance','decision':'unknown','rationale':'No new measurement supplied'}]})
        gate=ed.audit(missing,folder);report['unknown_claim_blocked']=not gate['can_export_assertion'] and gate['answer'] is None
        report['run_folder']=out.relative_to(folder).as_posix()
    report['passed']=not problems and bool(report['cases']) and all(c['passed'] for c in report['cases']) and report.get('unknown_claim_blocked',False)
    report['scope']='Offline standard-library functional acceptance of included topics; not a comprehensive scientific, semantic, OS or Skill test'
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    report=run(a.folder);out=Path(a.output)
    if out.exists():raise FileExistsError('Keep previous acceptance reports')
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    import zipfile
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as z:z.write(out,out.name)
    print(json.dumps({'passed':report['passed'],'report':str(out.resolve()),'return_zip':str(out.with_suffix('.zip').resolve())},ensure_ascii=False))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
