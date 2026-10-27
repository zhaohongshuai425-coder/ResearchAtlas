"""Offline, resumable local PDF/folder/ZIP intake into an existing literature registry."""
from pathlib import Path
import argparse, hashlib, json, os, uuid, zipfile
import core, literature_tools as lt, research_flow as flow
from intake_sources import bounded_read, zip_name, candidates

def reported_dois(doc):
    # Only the primary recorded DOI is a version-matching key. Unreviewed
    # variants are review leads, never aliases of a verified identity.
    value=doc.get('metadata',{}).get('doi')
    return {lt.doi_key(value)} if value else set()

def candidate_dois(doc):
    return {lt.doi_key(v['record']['doi']) for v in doc.get('metadata_variants',[]) if v.get('record',{}).get('doi')}

def flag_doi_conflict(doc):
    values=candidate_dois(doc)|reported_dois(doc)
    if len(values)<=1:return False
    changed=not doc.get('metadata_conflict') or doc.get('doi_conflict_candidates')!=sorted(values)
    doc.update(metadata_conflict=True,doi_conflict_candidates=sorted(values),identity_review='needs_review')
    return changed

def version_conflicts(doc,supplied):
    problems=[]
    if doc.get('metadata_conflict'):problems.append('Existing document has unresolved metadata/identity conflicts')
    title=supplied.get('title');old=doc['metadata'].get('title')
    if title and old and doc['metadata'].get('title_status')!='filename_placeholder_unverified':
        if ' '.join(title.split()).casefold()!=' '.join(old.split()).casefold():problems.append('Reported title conflicts with the recorded document')
    doi=lt.doi_key(supplied.get('doi'));primary=reported_dois(doc)
    if doi and primary and doi not in primary:problems.append('Reported DOI conflicts with primary identity')
    return problems

def supplement_metadata(doc,supplied,raw):
    lt.validate_metadata(supplied,local=True)
    fields={k:v for k,v in supplied.items() if k not in ('document_id','version_note')}
    if not fields:return False
    if 'title' in fields and (not isinstance(fields['title'],str) or not fields['title'].strip()):raise ValueError('Reported title must be nonempty text')
    if 'doi' in fields:fields['doi']=lt.doi_key(fields['doi'])
    variants=doc.setdefault('metadata_variants',[])
    if any(v.get('source',{}).get('sha256')==raw['sha256'] and all(v.get('record',{}).get(k)==value for k,value in fields.items()) for v in variants):return flag_doi_conflict(doc)
    variants.append({'record':fields,'source':{**raw,'locator':'local intake supplemental metadata'},'identity_review':'pending'})
    metadata=doc['metadata'];unverified=doc.get('identity_status') in ('pending_identity_verification','source_reported_not_scientifically_verified')
    for key,value in fields.items():
        placeholder=key=='title' and metadata.get('title_status')=='filename_placeholder_unverified'
        if unverified and (not metadata.get(key) or placeholder):
            metadata[key]=value
            if key=='title':metadata['title_status']='user_reported'
        elif value and key in ('title','doi') and metadata.get(key) and str(metadata[key]).casefold()!=str(value).casefold():
            doc['metadata_conflict']=True
    flag_doi_conflict(doc)
    return True

def write_immutable(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    from file_commit import atomic_json,locked
    with locked(path.parent/'.history.lock'):
        if path.exists():raise FileExistsError('Immutable execution record already exists')
        atomic_json(path,value)

def preserve_previous_report(path,folder):
    if not path.exists():return
    previous=core.load(path)
    if previous.get('run_id') and previous.get('run_report') and Path(previous['run_report']).is_file():return
    target=folder/('legacy-'+core.sha(path)+'.json')
    if not target.exists():
        try:write_immutable(target,previous)
        except FileExistsError:pass

def finish_report(report):
    # Each execution has an immutable record; only the convenience pointer is
    # replaced. Completion is written after snapshot cleanup has been reported.
    write_immutable(report['run_report'],report)
    from file_commit import atomic_json
    atomic_json(report['report'],report)
    pending=Path(report['checkpoint'])
    if pending.exists():
        try:pending.unlink()
        except OSError:pass  # complete immutable run is already available

def _ingest(library,inputs,metadata=None,index=False,max_files=500,max_file_mb=30,max_total_mb=1024,_rows=None):
    library=Path(library).resolve();lt.load_library(library)
    if not inputs or type(max_files)is not int or not 1<=max_files<=10000:raise ValueError('Use a bounded nonempty input batch')
    if not 1<=max_file_mb<=300 or not 1<=max_total_mb<=10000:raise ValueError('Invalid byte budgets')
    mappings=core.load(metadata) if metadata else {}
    if not isinstance(mappings,dict) or any(not isinstance(v,dict) for v in mappings.values()):raise ValueError('Metadata must map a source path/name or PDF SHA to an object')
    root=library.parent;rows=_rows
    batch_key=hashlib.sha256(json.dumps({'inputs':[str(Path(p).resolve()) for p in inputs],
         'metadata':mappings,'index':index},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    report_path=root/'.intake_batches'/(batch_key+'.json')
    run_id=uuid.uuid4().hex;run_folder=root/'.intake_batches'/batch_key
    preserve_previous_report(report_path,run_folder)
    run_report=run_folder/(run_id+'.json');checkpoint=run_folder/(run_id+'.pending.json')
    results=[];received_bytes=0
    for locator,reader,container,member in rows:
        row={'source':locator,'status':'failed','document_id':None}
        try:
            content=reader()
            row['input_sha256']=lt.digest(content)
            received_bytes+=len(content)
            if received_bytes>max_total_mb*1024*1024:raise ValueError("Actual received bytes exceed the declared batch budget")
            if not content.lstrip().startswith(b'%PDF-'):raise ValueError('Content is not a PDF; original is not registered as full text')
            digest=lt.digest(content)
            supplied=mappings.get(digest,mappings.get(locator,mappings.get(member or '',mappings.get(Path(locator).name,{}))))
            row['reported_metadata']=supplied
            lt.validate_metadata(supplied,local=True)
            with flow.locked(library):
                data=lt.load_library(library)
                same=[d for d in data['documents'] if any(b['sha256']==digest for b in d['fulltexts'])]
                explicit=supplied.get('document_id')
                doi=lt.doi_key(supplied.get('doi'))
                bydoi=[d for d in data['documents'] if doi and doi in reported_dois(d)]
                if len(same)>1 or len(bydoi)>1:raise ValueError('Ambiguous document identity; needs manual review')
                doc=next((d for d in data['documents'] if d['id']==explicit),None) if explicit else (same or bydoi or [None])[0]
                if explicit and doc is None:raise ValueError('Explicit document_id does not exist')
                if same and doc is not same[0]:raise ValueError('PDF bytes already belong to another document; preserve conflict')
                if doc is not None and bydoi and doc is not bydoi[0]:raise ValueError('PDF and DOI refer to different registered documents; preserve identity conflict')
                if doc and doi and doc['metadata'].get('doi') and doi!=lt.doi_key(doc['metadata']['doi']):raise ValueError('DOI conflicts with registered identity')
                provenance={'origin':'local_intake','source_path':locator,'container':container,
                            'member':member,'pdf_sha256':digest,'user_metadata':supplied}
                raw=lt.blob(root,'sources/raw',flow.dump(provenance).encode('utf-8'),'.json')
                binding={**lt.blob(root,'sources/pdf',content,'.pdf'),'origin':'user_local_pdf',
                    'source_path':locator,'container':container,'member':member,'imported_at':flow.now(),
                    'reading_status':'not_reviewed','version_status':'pending_version_review'}
                if supplied.get('version_note'):binding['version_note']=supplied['version_note']
                if doc is not None and not same:
                    problems=version_conflicts(doc,supplied)
                    if doc.get('identity_status')=='verified' and not explicit:
                        problems.append('A new original for a verified document needs an explicit reviewed document_id mapping')
                    if problems:
                        review={'id':lt.digest((doc['id']+digest+raw['sha256']).encode()),'candidate_document_id':doc['id'],
                            'fulltext':binding,'metadata_source':raw,'reasons':problems,'status':'needs_identity_review','run_id':run_id}
                        queue=data.setdefault('intake_reviews',[])
                        if not any(r['id']==review['id'] for r in queue):queue.append(review);flow.commit(library,data)
                        row.update(status='needs_identity_review',candidate_document_id=doc['id'],pdf_sha256=digest)
                        raise ValueError('; '.join(problems))
                changed=False
                if doc is None:
                    title=supplied.get('title') or Path(member or locator).stem
                    record={k:v for k,v in supplied.items() if k not in ('document_id','version_note')}
                    record.update(title=title,doi=doi,provider='local_upload',provider_id='sha256:'+digest,
                                  title_status='user_reported' if supplied.get('title') else 'filename_placeholder_unverified')
                    identity=lt.document_id(record)
                    doc={'id':identity,'metadata':record,'metadata_variants':[{'record':record,'source':{**raw,'locator':'local intake metadata'}}],
                         'metadata_conflict':False,'identity_status':'pending_identity_verification',
                         'availability':'available','reading_status':'not_reviewed','selection_status':'candidate',
                         'selection_history':[],'fulltexts':[],'indexes':[],'active_index':None}
                    data['documents'].append(doc);status='added_document';changed=True
                    leads=[d['id'] for d in data['documents'] if d is not doc and doi and doi in candidate_dois(d)]
                    if leads:doc.update(possible_identity_matches=leads,identity_review='needs_review')
                else:
                    status='duplicate_bytes' if same else 'added_version'
                    changed=supplement_metadata(doc,supplied,raw)
                if not same:doc['fulltexts'].append(binding);changed=True
                if doc.get('availability')!='available':doc['availability']='available';changed=True
                item_key=lt.digest(flow.dump(provenance).encode())
                if not any(x.get('intake_item_key')==item_key for x in data['attempts']):
                    data['attempts'].append({'operation':'local_intake','success':True,'intake_item_key':item_key,
                         'document_id':doc['id'],'status':status,'source':raw,'at':flow.now()})
                    changed=True
                if changed:flow.commit(library,data)
                row.update(status=status,document_id=doc['id'],pdf_sha256=digest,reading_status='not_reviewed',
                           metadata_conflict=bool(doc.get('metadata_conflict')),identity_review=doc.get('identity_review'))
            if index:
                current=lt.document(lt.load_library(library),row['document_id'])
                existing=any(b.get('source_sha256')==digest for b in current['indexes'])
                if current.get('metadata_conflict'):
                    row.update(index_status='held_identity_review',error='Resolve identity/metadata conflict before changing the active index')
                elif existing:row['index_status']='already_indexed'
                elif current['fulltexts'][-1]['sha256']==digest:
                    try:lt.index_pdf(library,row['document_id']);row['index_status']='indexed_not_read'
                    except Exception as exc:row.update(index_status='failed',index_error=str(exc))
                else:row['index_status']='historical_version_not_reindexed'
        except (OSError,ValueError,KeyError,zipfile.BadZipFile,RuntimeError) as exc:row['error']=str(exc)
        unsuccessful=row['status'] in ('failed','needs_identity_review') or row.get('index_status') in ('failed','held_identity_review')
        if unsuccessful:
            try:
                lt.failed_attempt(library,'local_intake',row.get('error',row.get('index_error','Intake incomplete')),
                    run_id=run_id,status=row['status'],source_path=locator,input_sha256=row.get('input_sha256'),
                    reported_metadata=row.get('reported_metadata'),index_status=row.get('index_status'))
                row['failure_attempt_recorded']=True
            except (OSError,ValueError,KeyError) as exc:row.update(failure_attempt_recorded=False,attempt_registration_error=str(exc))
        results.append(row)
        # A registry commit is the durable intake point. The summary can be
        # rebuilt by rerunning: exact bytes and the provenance key deduplicate.
        from file_commit import atomic_json
        atomic_json(checkpoint,{'schema':'research-local-intake/1.1','library':str(library),'run_id':run_id,'items':results,'complete':False})
    failed=sum(r['status'] in ('failed','needs_identity_review') or r.get('index_status') in ('failed','held_identity_review') for r in results)
    report={'schema':'research-local-intake/1.0','passed':failed==0,'complete':True,'items':results,
        'processed':len(results),'failed':failed,'report':str(report_path),'run_id':run_id,'run_report':str(run_report),'checkpoint':str(checkpoint),'network_used':False,
        'scope':'PDF receipt, identity candidates and optional text index only; no full-paper review or scientific validity inferred.'}
    return report

def ingest(library,inputs,metadata=None,index=False,max_files=500,max_file_mb=30,max_total_mb=1024):
    lt.load_library(library)
    if not inputs or type(max_files)is not int or not 1<=max_files<=10000:raise ValueError('Use a bounded nonempty input batch')
    if not 1<=max_file_mb<=300 or not 1<=max_total_mb<=10000:raise ValueError('Invalid byte budgets')
    with candidates(inputs,max_files,max_file_mb*1024*1024,max_total_mb*1024*1024) as rows:
        result=_ingest(library,inputs,metadata,index,max_files,max_file_mb,max_total_mb,_rows=rows)
    result.update(archive_snapshots=rows.snapshots_created,archive_snapshot_bytes=rows.snapshot_bytes,cleanup_warnings=rows.cleanup_warnings)
    finish_report(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--library',required=True)
    p.add_argument('--input',action='append',required=True);p.add_argument('--metadata');p.add_argument('--index',action='store_true')
    p.add_argument('--max-files',type=int,default=500);p.add_argument('--max-file-mb',type=int,default=30);p.add_argument('--max-total-mb',type=int,default=1024)
    a=p.parse_args();out=ingest(a.library,a.input,a.metadata,a.index,a.max_files,a.max_file_mb,a.max_total_mb)
    print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
