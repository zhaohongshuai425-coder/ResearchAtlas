"""Synthetic evidence only; no actual PDF reading, models or measurements."""
from pathlib import Path
import copy,gzip,json
import core,corpus_tools as ct,research_flow as flow

GOOD='Battery capacity degradation is measured under declared cycling conditions, with source limitations preserved for synthetic review.'
HELD='unreadablemappingtoken '+('क्षज'*50)

def binding(root,path):return {'path':path.resolve().relative_to(root.resolve()).as_posix(),'sha256':core.sha(path)}

def graph(root):
    flow.initialize(root,'Synthetic graph handoff','round7-graph')
    source=root/'source.md';source.write_text('Synthetic battery source. Not a scientific claim.',encoding='utf-8')
    path=root/'graph.json'
    core.save(path,{'schema':'research-kb/1.0','instance_id':'round7-graph',
        'sources':[{'id':'round7-graph::S','local_path':source.name,'sha256':core.sha(source)}],
        'nodes':[{'id':'round7-graph::M','local_id':'M','type':'method','label':'Synthetic battery method',
            'state':{k:'unknown' for k in sorted(core.AXES)},'source_refs':[{'source_id':'round7-graph::S','locator':'Synthetic text'}]}],
        'edges':[]})
    assert core.validate(core.load(path),root,True)['passed']
    state=root/'research_state.json'
    for i,p in enumerate((path,source)):flow.register_artifact(state,p,'graph' if p==path else 'other','G'+str(i),'Synthetic graph fixture')
    return path,state

def corpus(root,pages=None,expected_script='unknown',bad_index=None):
    flow.initialize(root,'Synthetic indexed evidence','round7-corpus')
    metadata=root/'metadata.json';core.save(metadata,{'title':'Synthetic corpus metadata'})
    pdf=root/'paper.pdf';pdf.write_bytes(b'%PDF-1.4\nSynthetic binding bytes only')
    cache=root/'cache.json.gz';cache.write_bytes(gzip.compress(json.dumps({'sha256':core.sha(pdf),
        'pages':[{'pdf_page':i+1,'text':t} for i,t in enumerate([GOOD] if pages is None else pages)]},ensure_ascii=False).encode()))
    catalog=root/'catalog.json'
    core.save(catalog,{'documents':[{'id':'D1','alias':'A1','batch':'synthetic','title':'Synthetic corpus',
        'metadata_binding':binding(root,metadata),'search_metadata':{'title':'Synthetic corpus'},'reading':'not_reviewed',
        'expected_script':expected_script,'reports':[{'id':'v1','availability':'verified_present',**binding(root,pdf),'cache':binding(root,cache)}]}]})
    manifest=root/'manifest.json';value={'schema':ct.SCHEMA,'instance_id':'round7-corpus','workspace_root':'.',
        'catalog':binding(root,catalog),'metadata_bindings':[binding(root,metadata)],'derived_root':'derived','scope':'Synthetic only'}
    core.save(manifest,value);index=root/'derived/index.sqlite';ct.build_search(manifest,index)
    if bad_index is not None:index.write_bytes(bad_index)
    value['search_index']=binding(root,index);core.save(manifest,value)
    return manifest,root/'research_state.json'

def register_dependencies(state,extra=()):
    # Explicitly register fixture files; never crawl the filesystem.
    root=state.parent
    for path in [root/'manifest.json',*extra]:
        if not any(a['path']==path.resolve().relative_to(root.resolve()).as_posix() for a in flow.read_state(state)['artifacts']):
            flow.register_artifact(state,path,'other','ROOT-'+str(len(flow.read_state(state)['artifacts'])),'Explicit synthetic root')
    from handoff_dependencies import inspect
    report=inspect(root,flow.read_state(state)['artifacts'])
    for path in report['unregistered_dependencies']:
        flow.register_artifact(state,path,'other','DEP-'+str(len(flow.read_state(state)['artifacts'])),'Explicit synthetic dependency')
    return flow.check_state(state)

def update_corpus(manifest):
    root=manifest.parent;value=core.load(manifest);catalog=root/'catalog.json';data=core.load(catalog)
    doc=copy.deepcopy(data['documents'][0]);doc.update(id='D2',alias='A2',title='Additional metadata only',reports=[])
    doc['search_metadata']={'title':'Additional metadata only'};data['documents'].append(doc);core.save(catalog,data)
    value['catalog']=binding(root,catalog);core.save(manifest,value)
    new_index=root/'derived/new_index.sqlite';ct.build_search(manifest,new_index)
    value['search_index']=binding(root,new_index);core.save(manifest,value)
    return new_index
