"""One bounded, explicitly requested Codex task via native stdio RPC.

Does not install Skills, poll an ongoing project, read auth files or change global
settings. Completion and native process exit are checked independently.
"""
from pathlib import Path
from collections import deque
import argparse,json,queue,shutil,subprocess,sys,threading,time

def terminal_status(message,thread_id,turn_id):
    if message.get('method')!='turn/completed':return None
    data=message.get('params',{});turn=data.get('turn',{})
    if data.get('threadId')!=thread_id or turn.get('id')!=turn_id:return None
    value=turn.get('status')
    if value not in ('completed','failed','interrupted'):raise ValueError('Unknown terminal turn status')
    return value

def outcome_exit(status,native_exit,forced=False,error=None):
    if status=='timeout':return 4
    if error or native_exit!=0 or forced:return 1
    return {'completed':0,'failed':2,'interrupted':3,'timeout':4}.get(status,1)

class Client:
    def __init__(self,command,cwd,folder):
        self.trace=(folder/'events.jsonl').open('x',encoding='utf-8')
        self.stderr=(folder/'stderr.txt').open('x',encoding='utf-8')
        try:
            self.process=subprocess.Popen(command,cwd=str(cwd),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=self.stderr,text=True,encoding='utf-8',bufsize=1,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except BaseException:
            self.trace.close();self.stderr.close();raise
        self.queue=queue.Queue();self.pending=deque();self.counter=0;self.denials=[]
        self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
    def _read(self):
        try:
            for line in self.process.stdout:
                self.trace.write(line);self.trace.flush()
                try:self.queue.put(json.loads(line))
                except json.JSONDecodeError:pass
        finally:self.queue.put(None)
    def send(self,value):
        self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.process.stdin.flush()
    def _next(self,timeout):
        try:item=self.queue.get(timeout=timeout)
        except queue.Empty:raise TimeoutError('No native RPC event before deadline')
        if item is None:raise RuntimeError('Native server closed its output')
        if 'id' in item and 'method' in item:
            # A test client must never silently grant an approval or invent human input.
            if item['method'].endswith('/requestApproval'):
                self.send({'id':item['id'],'result':{'decision':'decline'}})
            else:
                self.send({'id':item['id'],'error':{'code':-32601,'message':'This bounded client does not supply interactive input'}})
            self.denials.append(item['method'])
        return item
    def call(self,method,params,timeout=20):
        self.counter+=1;identity=self.counter
        self.send({'id':identity,'method':method,'params':params})
        until=time.monotonic()+timeout
        while True:
            remaining=until-time.monotonic()
            if remaining<=0:raise TimeoutError(method)
            item=self._next(remaining)
            if item.get('id')==identity and 'method' not in item:
                if 'error' in item:raise RuntimeError(method+': '+json.dumps(item['error'],ensure_ascii=False))
                return item['result']
            if 'method' in item and 'id' not in item:self.pending.append(item)
    def event(self,timeout):return self.pending.popleft() if self.pending else self._next(timeout)
    def close(self,grace):
        forced=False
        try:
            if not self.process.stdin.closed:self.process.stdin.close()
        except (OSError,BrokenPipeError):pass
        try:self.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            forced=True
            if sys.platform=='win32':
                subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,timeout=10)
            else:self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        if not self.reader.is_alive():self.trace.close()
        self.stderr.close()
        return {'native_exit_code':self.process.returncode,'forced_cleanup':forced,
            'native_process_reaped':self.process.poll() is not None,'trace_reader_closed':not self.reader.is_alive()}

def run_task(cwd,prompt,output,timeout=180,shutdown_grace=15,command=None,http_openai=False,resume=None):
    cwd=Path(cwd).resolve();output=Path(output).resolve()
    if not cwd.is_dir():raise ValueError('Use an existing task directory')
    if not 0 < timeout <= 1800 or not 0 < shutdown_grace <= 60:
        raise ValueError('Use bounded positive timeout and grace')
    if command is None:
        executable=shutil.which('codex')
        if not executable:raise FileNotFoundError('Codex CLI is not installed')
        command=[executable,'app-server','--stdio','-c','notify=[]','-c','mcp_servers={}']
        if http_openai:
            settings=['model_provider=research-http-task','model_providers.research-http-task.name=OpenAI HTTP research task',
                'model_providers.research-http-task.requires_openai_auth=true',
                'model_providers.research-http-task.supports_websockets=false','model_providers.research-http-task.wire_api=responses']
            for setting in settings:command+=['-c',setting]
    output.mkdir(parents=True,exist_ok=False)
    (output/'prompt.txt').write_text(prompt,encoding='utf-8')
    try:
        client=Client(command,cwd,output)
    except OSError as exc:
        result={'schema':'research-bounded-task/1.0','passed':False,'task_status':'not_started',
            'task_completed':False,'error':str(exc),'native_exit_code':None,'forced_cleanup':False,
            'native_process_reaped':True,'runner_exit_code':1,'global_config_modified':False,
            'automatic_retries':0}
        (output/'answer.txt').write_text('',encoding='utf-8')
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return result
    thread_id=None;turn_id=None;status=None;error=None
    messages=[];usage=[];unsubscribed=False;start=time.monotonic()
    try:
        client.call('initialize',{'clientInfo':{'name':'research-bounded-task','title':'Research bounded task','version':'1.0'},'capabilities':{'experimentalApi':True}})
        client.send({'method':'initialized','params':{}})
        params={'cwd':str(cwd),'approvalPolicy':'never','sandbox':'read-only'}
        if resume:
            params.update(threadId=resume,excludeTurns=True)
            reply=client.call('thread/resume',params)
        else:reply=client.call('thread/start',params)
        thread_id=reply['thread']['id']
        reply=client.call('turn/start',{'threadId':thread_id,'input':[{'type':'text','text':prompt}]})
        turn_id=reply['turn']['id'];until=time.monotonic()+timeout
        while status is None:
            remaining=until-time.monotonic()
            if remaining<=0:raise TimeoutError('Task deadline reached')
            message=client.event(remaining)
            if message.get('method')=='item/completed':
                params=message.get('params',{});item=params.get('item',{})
                if params.get('threadId')==thread_id and params.get('turnId')==turn_id and item.get('type')=='agentMessage':
                    messages.append(item.get('text',''))
            if message.get('method')=='thread/tokenUsage/updated':usage.append(message.get('params',{}))
            status=terminal_status(message,thread_id,turn_id)
            if status=='failed':error=json.dumps(message.get('params',{}).get('turn',{}).get('error'),ensure_ascii=False)
        try:client.call('thread/unsubscribe',{'threadId':thread_id},timeout=10);unsubscribed=True
        except (RuntimeError,TimeoutError):pass
    except TimeoutError as exc:
        status='timeout';error=str(exc)
        if thread_id and turn_id:
            try:client.call('turn/interrupt',{'threadId':thread_id,'turnId':turn_id},timeout=5)
            except (RuntimeError,TimeoutError,BrokenPipeError):pass
    except Exception as exc:error=str(exc)
    finally:closed=client.close(shutdown_grace)
    result={'schema':'research-bounded-task/1.0','thread_id':thread_id,'turn_id':turn_id,'task_status':status,
        'task_completed':status=='completed','error':error,'approval_or_interactive_requests_declined':client.denials,
        'thread_unsubscribed':unsubscribed,'elapsed_seconds':round(time.monotonic()-start,3),**closed,
        'model_settings':'Existing user settings; no model or reasoning override',
        'global_config_modified':False,'automatic_retries':0,'tokens_if_reported':usage[-1:]}
    result['runner_exit_code']=outcome_exit(status,closed['native_exit_code'],closed['forced_cleanup'],error)
    result['passed']=result['runner_exit_code']==0 and not client.denials and closed['trace_reader_closed']
    if not result['passed'] and result['runner_exit_code']==0:result['runner_exit_code']=1
    (output/'answer.txt').write_text('\n\n'.join(messages),encoding='utf-8')
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cwd',required=True);p.add_argument('--prompt-file',required=True);p.add_argument('--output',required=True)
    p.add_argument('--timeout',type=float,default=180);p.add_argument('--shutdown-grace',type=float,default=15)
    p.add_argument('--http-openai',action='store_true');p.add_argument('--resume-thread')
    a=p.parse_args()
    if not 1<=a.timeout<=1800 or not 1<=a.shutdown_grace<=60:raise ValueError('Use bounded positive timeout and grace')
    result=run_task(a.cwd,Path(a.prompt_file).read_text(encoding='utf-8'),a.output,a.timeout,a.shutdown_grace,http_openai=a.http_openai,resume=a.resume_thread)
    print(json.dumps({k:result[k] for k in ['passed','task_status','native_exit_code','forced_cleanup','runner_exit_code']},ensure_ascii=False))
    return result['runner_exit_code']
if __name__=='__main__':raise SystemExit(main())
