"""Generic read-only corpus bridge. Source fields are supplied by adapters."""
from pathlib import Path
from collections import Counter
import argparse
import gzip
import json
import math
import os
import re
import sqlite3
import tempfile
import unicodedata
import core
import pdf_quality
from source_snapshot import read_json

SCHEMA='research-corpus/1.0'

def row_anchor(manifest,row,binding):
    """One canonical locator for search, semantic records and neighbour pages."""
    return (f"{manifest['instance_id']}::{row['document_id']}::{row['report_id']}::"
            f"p{row['page']}::row{row['id']}@{binding['sha256'][:16]}")

def normal(text):
    return unicodedata.normalize('NFKC',str(text)).casefold()

def source_excerpt(body,offset,words,width=1300):
    # Map normalized positions back near original source positions, then check
    # the literal excerpt. Chunk scanning handles composition boundaries.
    low,high=0,len(body)
    while low<high:
        middle=(low+high)//2
        if len(normal(body[:middle]))<=offset:low=middle+1
        else:high=middle
    start=max(0,low-1-240);excerpt=body[start:start+width]
    if excerpt and any(word in normal(excerpt) for word in words):return excerpt
    overlap=min(width-1,max([240,*map(len,words)]));step=max(1,width-overlap)
    for start in range(0,len(body),step):
        excerpt=body[start:start+width]
        if excerpt and any(word in normal(excerpt) for word in words):return excerpt
    raise ValueError('Matched text cannot fit a verified source excerpt; narrow the query')

def read_manifest(file,root_override=None,source_snapshot=None):
    file=Path(file).resolve();m=read_json(file)[0] if source_snapshot is None else source_snapshot
    if m.get('schema')!=SCHEMA:raise ValueError('Unsupported corpus schema')
    root=Path(root_override).resolve() if root_override else (file.parent/m['workspace_root']).resolve()
    catalog=core.inside(root,m['catalog']['path'])
    if not catalog.is_file():raise ValueError('Corpus catalog missing/changed')
    catalog_data,catalog_sha=read_json(catalog)
    if catalog_sha!=m['catalog']['sha256']:raise ValueError('Corpus catalog missing/changed')
    for binding in m['metadata_bindings']:
        path=core.inside(root,binding['path'])
        if not path.is_file() or core.sha(path)!=binding['sha256']:raise ValueError('Formal metadata source missing/changed; rebuild a new derived version')
    return m,root,catalog_data

def read_cache(root,binding,pdf_sha):
    path=core.inside(root,binding['path'])
    if core.sha(path)!=binding['sha256']:raise ValueError('Extraction cache bytes changed')
    value=json.loads(gzip.decompress(path.read_bytes()))
    if value.get('sha256')!=pdf_sha:raise ValueError('Cache is not bound to the verified original SHA')
    return value

def _build_search_file(manifest,output,root_override=None):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Preserve existing corpus index')
    m,root,data=read_manifest(manifest,root_override)
    derived=core.inside(root,m['derived_root'])
    if not output.is_relative_to(derived):raise ValueError('Index must stay in the declared derived directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(output)
    try:
        con.execute('CREATE TABLE item(id INTEGER PRIMARY KEY,document_id TEXT,alias TEXT,kind TEXT,report_id TEXT,page INTEGER,body TEXT,normalized TEXT,quality TEXT,binding TEXT)')
        con.execute('CREATE TABLE context(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('INSERT INTO context VALUES(?,?)',('catalog_sha256',m['catalog']['sha256']))
        count=Counter()
        for doc in data['documents']:
            def insert(kind,body,binding,report_id='',page=None,quality=None):
                con.execute('INSERT INTO item(document_id,alias,kind,report_id,page,body,normalized,quality,binding) VALUES(?,?,?,?,?,?,?,?,?)',
                            (doc['id'],doc['alias'],kind,report_id,page,body,normal(body),json.dumps(quality or {}),json.dumps(binding)))
                count[kind]+=1
            insert('metadata',json.dumps(doc['search_metadata'],ensure_ascii=False),doc['metadata_binding'])
            for original in doc['reports']:
                if original['availability']!='verified_present' or not original.get('cache'):continue
                path=core.inside(root,original['path'])
                if core.sha(path)!=original['sha256']:raise ValueError('PDF changed before cache reuse')
                cached=read_cache(root,original['cache'],original['sha256'])
                for page in cached['pages']:
                    text=page.get('text','');number=page.get('pdf_page')
                    if not isinstance(number,int) or isinstance(number,bool) or number<1:raise ValueError('Invalid cached physical page')
                    quality=pdf_quality.text_quality(text,doc.get('expected_script','unknown'))
                    insert('pdf_page',text,{'path':original['path'],'sha256':original['sha256'],'cache':original['cache']},original['id'],number,quality)
                    if quality['gate']=='hold_text_for_review':count['held_pages']+=1
            for card in doc.get('review_sources',[]):
                if card['role']=='legacy_card_unsafe_identity':continue
                path=core.inside(root,card['path'])
                if not path.is_file() or core.sha(path)!=card['sha256']:raise ValueError('Reading source changed')
                # Inherited reading notes remain a distinct kind, never PDF evidence.
                content=path.read_text(encoding='utf-8-sig')
                if card.get('line'):
                    lines=content.splitlines();n=card['line']
                    if not 1<=n<=len(lines):raise ValueError('Inherited note line is outside source')
                    content=lines[n-1]
                    if doc['alias'] not in content:raise ValueError('Inherited note line does not identify this document')
                insert('inherited_note',content,card)
        con.commit()
    finally:con.close()
    return {'passed':True,'path':str(output),'sha256':core.sha(output),'counts':dict(count),
            'scope':'Reuses hash-matched machine text. Reading, implementation and adoption states are not promoted.'}

def build_search(manifest,output,root_override=None):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Preserve existing corpus index')
    m,root,_=read_manifest(manifest,root_override)
    derived=core.inside(root,m['derived_root'])
    if not output.is_relative_to(derived):raise ValueError('Index must stay in the declared derived directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    # Only the private staging directory is cleaned on failure. Never delete a
    # final path another writer may have created while this build was running.
    with tempfile.TemporaryDirectory(prefix='.corpus-build-',dir=output.parent) as staging:
        staged=Path(staging)/'index.sqlite'
        result=_build_search_file(manifest,staged,root_override)
        # Same-filesystem link publishes a completed, closed SQLite file without
        # replacing an existing target. Unsupported publication fails cleanly.
        os.link(staged,output)
    return {**result,'path':str(output)}

def chinese_fallback_terms(words):
    fragments=[]
    for word in words:
        for phrase in re.findall(r'[\u3400-\u9fff]+',word):
            if len(phrase)<4:continue
            for length in (3,2):
                for start in range(len(phrase)-length+1):
                    fragment=phrase[start:start+length]
                    if fragment not in fragments:fragments.append(fragment)
    return fragments[:40]

def search(manifest,question,terms=None,limit=10,alias=None,include_flagged=False,root_override=None,pool=False,per_document=3,source_snapshot=None):
    if not question.strip():raise ValueError('Question is required')
    if not 1<=limit<=(2000 if pool else 100):raise ValueError('Use a bounded retrieval limit')
    if not 1<=per_document<=2000:raise ValueError('Invalid document passage cap')
    m,root,data=read_manifest(manifest,root_override,source_snapshot=source_snapshot)
    binding=m.get('search_index')
    if not binding:raise ValueError('Build and bind the corpus search index first')
    db=core.inside(root,binding['path'])
    if core.sha(db)!=binding['sha256']:raise ValueError('Derived search index changed')
    words=[]
    for entry in (terms or [])+[question]:
        for word in re.findall(r'[\w\u3400-\u9fff-]+',normal(entry)):
            if len(word)>=2 and word not in {'the','with','and','what','how','for','from','this','that','does'} and word not in words:words.append(word)
    words=words[:40]
    literal_words=list(words);expansion='none'
    if not words:return {'question':question,'hits':[],'answer_generated':False,'warning':'No searchable terms',
                         'declared_terms':[],'retrieval_terms':[],'query_expansion':expansion}
    con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    try:
        if con.execute("SELECT value FROM context WHERE key='catalog_sha256'").fetchone()[0]!=m['catalog']['sha256']:
            raise ValueError('Index/catalog version mismatch')
        def matching_rows(search_words):
            where=' OR '.join('instr(normalized,?)>0' for _ in search_words);args=list(search_words)
            if alias:where='('+where+') AND alias=?';args.append(alias)
            return list(con.execute('SELECT * FROM item WHERE '+where,args))
        rows=matching_rows(words)
        if not rows:
            fragments=chinese_fallback_terms(words)
            if fragments:
                words=fragments;rows=matching_rows(words)
                expansion='cjk_fallback_after_no_literal_matches'
    finally:con.close()
    frequencies=Counter(word for row in rows for word in words if word in row['normalized']) if expansion!='none' else Counter()
    docs={d['id']:d for d in data['documents']};hits=[];held=0;stale=[];verified={}
    def intact(binding):
        key=(binding['path'],binding['sha256'])
        if key not in verified:
            path=core.inside(root,key[0]);verified[key]=path.is_file() and core.sha(path)==key[1]
        return verified[key]
    for row in rows:
        quality=json.loads(row['quality']);source=json.loads(row['binding'])
        if row['kind']=='pdf_page' and quality['gate']=='hold_text_for_review' and not include_flagged:
            held+=1;continue
        if not intact(source) or (source.get('cache') and not intact(source['cache'])):
            stale.append({'alias':row['alias'],'kind':row['kind'],'path':source['path']});continue
        matched=[w for w in words if w in row['normalized']]
        if expansion=='none':
            rank=len(matched)*10+sum(min(row['normalized'].count(w),8) for w in matched)
        else:
            # Rare, longer fragments get more weight; repeated common words
            # should not dominate the expanded Chinese candidate list.
            rank=sum(len(w)*math.log1p((len(rows)+1)/(frequencies[w]+1))*(1+min(row['normalized'].count(w),2)/2) for w in matched)
        # Metadata stays findable without suggesting it supports scientific claims.
        if row['kind']=='metadata' and normal(question) in row['normalized']:rank+=100
        first=min(row['normalized'].find(w) for w in matched)
        excerpt=source_excerpt(row['body'],first,matched)
        doc=docs[row['document_id']]
        hits.append({'document_id':row['document_id'],'alias':row['alias'],'batch':doc['batch'],'title':doc['title'],
                     'kind':row['kind'],'report_id':row['report_id'],'page':row['page'],'excerpt':excerpt,'score':rank,
                     'quality':quality,'source_binding':source,'reading':doc['reading'],
                     'anchor':row_anchor(m,row,binding),
                     'scope':'Metadata, inherited note and extracted PDF text are distinct; no entailment or new reading asserted.'})
    hits.sort(key=lambda x:(-x['score'],x['alias'],x['kind'],x['page'] or 0))
    picked=[];diversity=Counter()
    for hit in hits:
        if diversity[hit['document_id']]>=per_document:continue
        picked.append(hit);diversity[hit['document_id']]+=1
        if len(picked)>=limit:break
    return {'question':question,'declared_terms':literal_words,'retrieval_terms':words,'query_expansion':expansion,
            'registered_documents':len(data['documents']),
            'registered_batches':dict(Counter(d['batch'] for d in data['documents'])),
            'hits':picked,'held_flagged_matches':held,'stale_matches_withheld':stale,
            'matched_passages':len(hits),'pool_may_be_truncated':len(hits)>len(picked),
            'index_sha256':binding['sha256'],'answer_generated':False,
            'scope':'Whole registered corpus unless alias filter supplied. Lexical retrieval and quality triage only.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    for name in ['build','search','status']:
        a=s.add_parser(name);a.add_argument('--manifest',required=True);a.add_argument('--root')
        if name=='build':a.add_argument('--output',required=True)
        if name=='search':
            a.add_argument('--question',required=True);a.add_argument('--terms',action='append',default=[]);a.add_argument('--limit',type=int,default=10)
            a.add_argument('--alias');a.add_argument('--include-flagged',action='store_true')
    a=p.parse_args()
    if a.command=='build':out=build_search(a.manifest,a.output,a.root)
    elif a.command=='search':out=search(a.manifest,a.question,a.terms,a.limit,a.alias,a.include_flagged,a.root)
    else:
        m,root,d=read_manifest(a.manifest,a.root);out={'documents':len(d['documents']),'batches':dict(Counter(x['batch'] for x in d['documents'])),'root':str(root),'scope':m['scope']}
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
