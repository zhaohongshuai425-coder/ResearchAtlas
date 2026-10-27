"""Build a new portable distribution, generate entries, run offline tests and optionally ZIP it."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile,zipfile

SOURCE=Path(__file__).resolve().parent;NAME='research-evidence-workflow'
GENERATED={'manifest.json','harness-contract.json','DeepSeek_直接复制使用.txt','Harness_执行入口.md','DeepSeek_API请求示例.json','build_report.json'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
def excluded(name):
    return name in GENERATED or name.endswith(('.receipt.json','.lock','.pyc','.log')) or name in ('__pycache__','.git','dist')

def signature(root):
    result={}
    for folder,dirs,files in os.walk(root):
        dirs[:]=sorted(n for n in dirs if not excluded(n))
        for name in sorted(files):
            if not excluded(name):
                path=Path(folder)/name;result[path.relative_to(root).as_posix()]=sha(path)
    return result

def freeze(root):
    files={p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*'))
           if p.is_file() and p.name!='manifest.json' and not p.name.endswith('.lock') and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    version=json.loads((root/'release_metadata.json').read_text(encoding='utf-8'))['version']
    save(root/'manifest.json',{'schema':'research-skill-release/1.0','version':version,'files':files});return files
def call(command,cwd):
    try:r=subprocess.run(command,cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=180)
    except subprocess.TimeoutExpired as exc:raise RuntimeError('Build subprocess exceeded its 180-second limit') from exc
    if r.returncode:raise RuntimeError(r.stdout[-4000:]+'\n'+r.stderr[-8000:])
    return r

def verify_archive(path,prefix,expected):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!={prefix+n for n in expected}:raise ValueError('Archive member set differs from frozen manifest')
        for name,digest in expected.items():
            h=hashlib.sha256()
            with z.open(prefix+name) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=digest:raise ValueError('Archive member changed after directory verification: '+name)
    return True
def build(output,test=True,archive=False):
    output=Path(output).resolve()
    if output.is_relative_to(SOURCE):raise ValueError('Build into a new directory outside the source tree')
    target=Path(str(output)+'.zip')
    if archive and target.exists():raise FileExistsError('Archive already exists; choose a new output name')
    def ignore(folder,names):
        return [n for n in names if excluded(n)]
    before=signature(SOURCE)
    shutil.copytree(SOURCE,output,ignore=ignore)
    tool=output/NAME/'scripts/workflow.py'
    for filename,mode,extra in [('DeepSeek_直接复制使用.txt','chat',[]),('Harness_执行入口.md','harness',[]),
       ('DeepSeek_API请求示例.json','api',['--model','MODEL_FROM_YOUR_ACCOUNT','--task','读取已授权项目的研究状态，依据证据说明下一步。','--with-tools'])]:
        call([sys.executable,'-B','-X','utf8',str(tool),'portable-context','--mode',mode,'--output',str(output/filename),*extra],output)
        receipt=output/(filename+'.receipt.json');record=json.loads(receipt.read_text(encoding='utf-8'))
        if filename.endswith('.md'):
            # The compiled entry is at the release root, while SKILL links are
            # relative to its child Skill directory. Keep the copied entry usable.
            entry=output/filename;text=entry.read_text(encoding='utf-8')
            def relocate(match):
                path=match.group(2)
                if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',path) or path.startswith('#'):return match.group(0)
                return ']('+NAME+'/'+path+')'
            text=re.sub(r'\]\((<)?([^<>\n)]+)(>)?\)',relocate,text)
            entry.write_text(text,encoding='utf-8',newline='\n')
            record.update(output_sha256=sha(entry),output_characters=len(text),release_relative_links_rebased=True)
        record['output']=filename;record['output_locator_scope']='release_relative';save(receipt,record)
    defs=json.loads(call([sys.executable,'-B','-X','utf8',str(tool),'harness-tools','--list'],output).stdout)
    save(output/'harness-contract.json',{'schema':'research-harness-adapter/1.0','kind':'project_defined_adapter_not_official_plugin_config',
        'function_tools':defs,'executable':['{python}','-B','-X','utf8','{skill}/scripts/workflow.py','harness-tools','--root','{authorized_project_root}'],
        'read_only':True,'model_calls':False,'request_id_max_characters':256,'complete_response_max_characters':16000})
    freeze(output)
    report={'passed':True,'version':json.loads((output/'release_metadata.json').read_text(encoding='utf-8'))['version'],
            'source_files_unchanged_by_build':True,'model_calls':0,'tests_performed':test,'native_models_tested_by_build':False,
            'completion_state':'verifying','installable':False}
    if test:
        try:
            result=call([sys.executable,'-B','-X','utf8',str(output/'run_tests.py')],output)
            report['tests']=json.loads(result.stdout.strip().splitlines()[-1])
            if report['tests'].get('passed') is not True:raise ValueError('Portable tests were not accepted')
        except BaseException:
            report.update(passed=False,failure_phase='portable_tests')
            save(output/'build_report.json',report);freeze(output)
            raise
    def require_unchanged_source():
        if signature(SOURCE)!=before:
            report.update(passed=False,source_files_unchanged_by_build=False)
            save(output/'build_report.json',report);freeze(output)
            raise RuntimeError('Source changed during build; do not use this distribution')
    require_unchanged_source()
    save(output/'build_report.json',report);files=freeze(output)
    try:
        call([sys.executable,'-B','-X','utf8',str(output/'install.py'),'--verify-content-only'],output)
        require_unchanged_source()
        if any(sha(output/name)!=digest for name,digest in files.items()):
            raise ValueError('Output changed before build completion')
        report.update(completion_state='complete',installable=True)
        save(output/'build_report.json',report)
        # Preserve the already checked payload hashes, changing only the report.
        files['build_report.json']=sha(output/'build_report.json')
        save(output/'manifest.json',{'schema':'research-skill-release/1.0','version':report['version'],'files':files})
        call([sys.executable,'-B','-X','utf8',str(output/'install.py'),'--verify-only'],output)
        require_unchanged_source()
    except BaseException:
        report.update(passed=False,completion_state='failed',installable=False,failure_phase='release_verification')
        save(output/'build_report.json',report);freeze(output)
        raise
    if archive:
        expected={**files,'manifest.json':sha(output/'manifest.json')}
        fd,name=tempfile.mkstemp(prefix='.build-archive-',suffix='.zip',dir=output.parent);os.close(fd);temporary=Path(name)
        cleanup=[]
        try:
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for relative in expected:z.write(output/relative,output.name+'/'+relative)
            verify_archive(temporary,output.name+'/',expected)
            require_unchanged_source()
            # Publish complete verified bytes without overwriting a target
            # that another process may have created after preflight.
            os.link(temporary,target)
            report.update(archive=str(target),archive_sha256=sha(target),archive_verified=True)
        except Exception as exc:
            report.update(passed=False,archive_verified=False,error=str(exc))
            save(output/'build_report.json',report);freeze(output)
            raise RuntimeError('Archive not published: '+str(exc)) from exc
        finally:
            try:temporary.unlink()
            except OSError as exc:cleanup.append(str(exc))
        if cleanup:report['cleanup_warnings']=cleanup
    return {'passed':True,'output':str(output),**report}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--skip-tests',action='store_true');p.add_argument('--zip',action='store_true');a=p.parse_args()
    try:out=build(a.output,not a.skip_tests,a.zip)
    except (OSError,ValueError,RuntimeError) as exc:print(json.dumps({'passed':False,'error':str(exc)},ensure_ascii=False));return 1
    print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
