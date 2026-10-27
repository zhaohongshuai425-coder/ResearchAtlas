"""Thesis v2 registry adapter; generic corpus tools do not know its field names."""
from pathlib import Path
from collections import Counter
import argparse
import json
import os
import gzip
from importlib import metadata
import core
import corpus_tools
import pdf_quality

def adapt(root,kb,output,refresh_text=False,reuse_cache=None,text_backend='pdfplumber'):
    root=Path(root).resolve();kb=core.inside(root,kb);output=Path(output).resolve()
    if output.exists():raise FileExistsError('Create a new corpus version')
    if not output.is_relative_to(root):raise ValueError('Derived version must be inside the declared workspace')
    names=['02_文献与精读/文献主记录.json','02_文献与精读/原文与版本.json','02_文献与精读/原文证据登记.json',
           '03_场景与方法/方法登记.json','99_工具与维护/通用知识底座/instances/thesis-aml.graph.json']
    bindings=[{'path':p.relative_to(root).as_posix(),'sha256':core.sha(p)} for p in [kb/n for n in names]]
    papers=core.load(kb/names[0]);reports=core.load(kb/names[1]);graph=core.load(kb/names[-1])
    output.mkdir(parents=True)
    documents=[];file_checks=[];source_paths={}
    for position,paper in enumerate(papers):
        identity=paper['canonical_id'];alias=paper['alias']
        if identity!=paper['paper_id']:raise ValueError('Canonical and paper IDs require manual reconciliation')
        language=paper.get('legacy_matrix_fields',{}).get('语言','')
        doc={'id':identity,'alias':alias,'batch':paper['batch'],'title':paper['title'],
             'reading':{'status':paper['read_status'],'basis':paper.get('read_status_basis'),'scope':paper.get('read_scope'),
                        'origin':'inherited_registration_not_new_reading'},
             'identity_status':paper.get('identity_status'),'metadata_binding':{**bindings[0],'locator':f'JSON row {position+1}; canonical_id={identity}'},
             'search_metadata':{k:paper.get(k) for k in ['title','short_name','alias','legacy_summary','method_description','module_description','limits','source_url','doi','venue']},
             'expected_script':'cjk' if language=='中文' else 'unknown','reports':[],'review_sources':[],
             'historical_execution_metadata':{'status':paper.get('project_training_status'),'scope':'Historical literature registration only; current model archive must be checked separately.'}}
        for original in [r for r in reports if r['paper_id']==identity]:
            report={'id':original['report_id'],'kind':original['kind'],'path':original.get('path'),'sha256':original.get('sha256'),
                    'identity_scope':original.get('identity_scope'),'availability':'unbound_original'}
            if report['path']:
                path=core.inside(root,report['path'])
                if path.is_file():
                    actual=core.sha(path);report['availability']='verified_present' if actual==report['sha256'] else 'hash_mismatch'
                    report['current_sha256']=actual
                    cache=kb/'99_工具与维护/提取缓存'/(str(report['sha256'])+'.json.gz')
                    if report['availability']=='verified_present' and cache.is_file():
                        cache_binding={'path':cache.relative_to(root).as_posix(),'sha256':core.sha(cache)}
                        cached=corpus_tools.read_cache(root,cache_binding,report['sha256'])
                        report['cache']=cache_binding;report['cached_pages']=len(cached['pages'])
                    if report['availability']=='verified_present' and refresh_text:
                        old_pages={p['pdf_page']:p['text'] for p in cached['pages']} if report.get('cache') else {}
                        reuse=core.inside(root,reuse_cache)/(actual+'.json.gz') if reuse_cache else None
                        if reuse and reuse.is_file():
                            completed=corpus_tools.read_cache(root,{'path':reuse.relative_to(root).as_posix(),'sha256':core.sha(reuse)},actual)
                            inspection={'total_pages':len(completed['pages']),'engine':completed['engine'],
                                'pages':[{'page':p['pdf_page'],'text':p['text'],'quality':pdf_quality.text_quality(p['text'],doc['expected_script']),
                                          'layout_review':p.get('layout_review',[]),'coverage_fallback_used':False} for p in completed['pages']]}
                            report['reused_completed_machine_cache']={'path':reuse.relative_to(root).as_posix(),'sha256':core.sha(reuse)}
                        elif text_backend=='pdfium':inspection=pdf_quality.extract_fast(path,doc['expected_script'])
                        else:inspection=pdf_quality.inspect_pdf(path,expected_script=doc['expected_script'])
                        current={'sha256':actual,'alias':alias,'engine':inspection.get('engine',{'backend':'pdfplumber','version':metadata.version('pdfplumber'),'all_texts':True}),
                                 'scope':'Machine extraction and quality triage only; no new full-paper reading.',
                                 'pages':[{'pdf_page':p['page'],'text':p['text'],'quality':p['quality'],'layout_review':p['layout_review']} for p in inspection['pages']]}
                        fresh=output/'text_caches'/(actual+'.json.gz');fresh.parent.mkdir(exist_ok=True)
                        with fresh.open('xb') as stream:stream.write(gzip.compress(json.dumps(current,ensure_ascii=False).encode('utf-8'),mtime=0))
                        report['previous_cache']=report.get('cache')
                        report['cache']={'path':fresh.relative_to(root).as_posix(),'sha256':core.sha(fresh)}
                        report['cached_pages']=len(current['pages'])
                        report['extraction']={'policy':'byte_bound_machine_text_quality_triage','engine':current['engine'],'old_cached_pages':len(old_pages),
                            'new_physical_pages':inspection['total_pages'],
                            'held_pages':[p['page'] for p in inspection['pages'] if p['quality']['gate']=='hold_text_for_review'],
                            'recovered_old_flagged_pages':[p['page'] for p in inspection['pages'] if p['page'] in old_pages
                                and pdf_quality.text_quality(old_pages[p['page']],doc['expected_script'])['gate']=='hold_text_for_review'
                                and p['quality']['gate']!='hold_text_for_review'],
                            'layout_review_pages':[p['page'] for p in inspection['pages'] if p['layout_review']],
                            'coverage_fallback_pages':[p['page'] for p in inspection['pages'] if p['coverage_fallback_used']]}
                        print(json.dumps({'progress_alias':alias,'report':report['id'],'pages':report['cached_pages'],
                                          'held':len(report['extraction']['held_pages'])},ensure_ascii=False),flush=True)
                    source_paths[report['path']]=actual
                else:report['availability']='missing_now'
            doc['reports'].append(report);file_checks.append({'alias':alias,**report})
        safe_sources=[]
        if paper.get('deep_card_source'):safe_sources.append({'path':paper['deep_card_source'],'role':'inherited_deep_reading_card'})
        for ref in paper.get('source_refs',[]):
            if ref.get('path','').endswith('.md') and ref.get('line'):
                safe_sources.append({'path':ref['path'],'line':ref['line'],'role':'inherited_audit_row'})
        for binding in safe_sources:
            path=core.inside(root,binding['path'])
            if path.is_file():doc['review_sources'].append({**binding,'sha256':core.sha(path)})
        documents.append(doc)
    if len({d['id'] for d in documents})!=len(documents) or len({d['alias'] for d in documents})!=len(documents):
        raise ValueError('Duplicate registered identity/alias; do not silently merge')
    graph_docs={n['local_id']:n for n in graph['nodes'] if n['type']=='document'}
    alignment=[]
    for doc in documents:
        original=next(x for x in papers if x['paper_id']==doc['id']);node=graph_docs.get(doc['id'])
        alignment.append({'id':doc['id'],'alias':doc['alias'],'present_in_graph':bool(node),
                          'reading_status_preserved':node is not None and node['state']['reading']==original['read_status'],
                          'title_preserved':node is not None and node['label']==doc['title']})
    catalog={'schema':'research-corpus-catalog/1.0','documents':documents,'scope':'Entire formal registry, including missing originals. No identity/read/training promotion.'}
    cat=output/'catalog.json';core.save(cat,catalog)
    manifest={'schema':corpus_tools.SCHEMA,'instance_id':graph['instance_id'],'workspace_root':os.path.relpath(root,output).replace('\\','/'),
              'derived_root':output.relative_to(root).as_posix(),'catalog':{'path':cat.relative_to(root).as_posix(),'sha256':core.sha(cat)},
              'metadata_bindings':bindings,'scope':'All formal historical, added and future registered documents; source originals are referenced without duplication.'}
    core.save(output/'manifest.json',manifest)
    index=corpus_tools.build_search(output/'manifest.json',output/'corpus.sqlite')
    manifest['search_index']={'path':(output/'corpus.sqlite').relative_to(root).as_posix(),'sha256':index['sha256']}
    core.save(output/'manifest.json',manifest)
    passed=all(x['present_in_graph'] and x['reading_status_preserved'] and x['title_preserved'] for x in alignment)
    passed=passed and not any(x['availability'] in {'hash_mismatch','missing_now'} for x in file_checks)
    report={'passed':passed,'registered_documents':len(documents),'batches':dict(Counter(d['batch'] for d in documents)),
            'original_report_bindings':len(file_checks),'original_availability':dict(Counter(x['availability'] for x in file_checks)),
            'documents_with_originals':sum(any(r['availability']=='verified_present' for r in d['reports']) for d in documents),
            'documents_without_originals':[d['alias'] for d in documents if not any(r['availability']=='verified_present' for r in d['reports'])],
            'graph_alignment':alignment,'source_file_checks':file_checks,'index':index,'originals_copied':0,
            'scope':'Whole-registry identity/state and source-byte checks. Cached text reuse is not renewed full-text reading.'}
    core.save(output/'全库接入核验.json',report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--kb',required=True);p.add_argument('--output',required=True)
    p.add_argument('--refresh-text',action='store_true');p.add_argument('--reuse-cache');p.add_argument('--text-backend',choices=['pdfplumber','pdfium'],default='pdfplumber')
    a=p.parse_args();r=adapt(a.root,a.kb,a.output,a.refresh_text,a.reuse_cache,a.text_backend)
    print(json.dumps({k:v for k,v in r.items() if k not in ['graph_alignment','source_file_checks','documents_without_originals']},ensure_ascii=False,indent=2))
    raise SystemExit(0 if r['passed'] else 1)
