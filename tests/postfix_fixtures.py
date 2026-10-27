"""Synthetic corpus/encoder fixtures; never real papers or model inference."""
from pathlib import Path
import gzip,json
import core,corpus_tools as ct

def binding(root,path):return {'path':path.resolve().relative_to(root.resolve()).as_posix(),'sha256':core.sha(path)}

def corpus(root,body):
    root.mkdir(parents=True);metadata=root/'metadata.json';core.save(metadata,{'title':'Synthetic evidence fixture'})
    pdf=root/'paper.pdf';pdf.write_bytes(b'%PDF-1.4\nSynthetic binding bytes; no actual parsing')
    cache=root/'cache.json.gz';cache.write_bytes(gzip.compress(json.dumps({'sha256':core.sha(pdf),'pages':[{'pdf_page':1,'text':body}]},ensure_ascii=False).encode()))
    catalog=root/'catalog.json';core.save(catalog,{'documents':[{'id':'D1','alias':'A1','batch':'synthetic','title':'Synthetic evidence fixture',
        'search_metadata':{'title':'Synthetic evidence fixture'},'metadata_binding':binding(root,metadata),
        'reports':[{'id':'v1','availability':'verified_present',**binding(root,pdf),'cache':binding(root,cache)}],
        'reading':'not_reviewed','review_sources':[]}]})
    manifest=root/'manifest.json';value={'schema':ct.SCHEMA,'workspace_root':'.','instance_id':'synthetic','derived_root':'derived',
        'catalog':binding(root,catalog),'metadata_bindings':[binding(root,metadata)],'scope':'Synthetic only'}
    core.save(manifest,value);result=ct.build_search(manifest,root/'derived/index.sqlite');value['search_index']=binding(root,Path(result['path']));core.save(manifest,value)
    return manifest

class FakeTokenizer:
    def encode(self,text,add_special_tokens=False):return list(map(ord,text))
    def decode(self,tokens,skip_special_tokens=True):return ''.join(map(chr,tokens))

class FakeEncoder:
    def __init__(self,*args,**kwargs):self.tokenizer=FakeTokenizer();self.lock_sha='0'*64;self.lock={'repository':'synthetic-no-model','revision':'fixture'}
    def encode(self,texts,role,batch=8):return [[1.0,1.0] for _ in texts]

class FakeNumpy:
    @staticmethod
    def concatenate(values):return [row for batch in values for row in batch]
    @staticmethod
    def savez_compressed(path,**values):Path(path).write_text(json.dumps(values),encoding='utf-8')
