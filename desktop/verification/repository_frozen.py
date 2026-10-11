"""Execute existing acceptance fixtures through actual frozen stdio RPC in fresh isolated Temp data.
The adapter only changes test transport; all management/install/runtime work runs
inside the selected packaged executable. ComponentProject is used solely for file inspection.
"""
import argparse, asyncio, json, queue, runpy, subprocess, threading
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from desktop_runtime.components import ComponentProject

parser=argparse.ArgumentParser()
parser.add_argument('--fixture',choices=['python','node'],required=True)
parser.add_argument('--resources',type=Path,required=True)
args=parser.parse_args()
resources=args.resources.resolve()
executable=resources/'runtime/pla-runtime.exe'

class ConfigProxy:
    def __init__(self,manager):self.manager=manager
    def save(self,value):return self.manager.dispatch('configure',value)

class FrozenManager:
    def __init__(self,data,ignored_resources):
        self.data=data
        data.mkdir(parents=True,exist_ok=True)
        self.components=ComponentProject(data,resources)
        self.config=ConfigProxy(self)
        self.lines=queue.Queue()
        self.stderr=(data/'frozen-manager.stderr.log').open('wb')
        self.process=subprocess.Popen([str(executable),'manager','--data-dir',str(data),'--resources',str(resources)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def reader():
            for line in self.process.stdout:self.lines.put(line)
            self.lines.put(None)
        threading.Thread(target=reader,daemon=True).start()
        self.port=None
    def dispatch(self,command,args):
        self.process.stdin.write((json.dumps({'command':command,'args':args})+'\n').encode());self.process.stdin.flush()
        line=self.lines.get(timeout=120)
        if line is None:raise RuntimeError('Frozen Manager exited; inspect '+str(self.data/'frozen-manager.stderr.log'))
        result=json.loads(line)
        if not result['ok']:raise ValueError(result['error'])
        if command=='configure' and 'runtime_port' in args:self.port=args['runtime_port']
        return result['result']
    def start(self):return self.dispatch('start',{})
    @property
    def children(self):
        status=self.dispatch('status',{})
        return {role:type('OwnedProcess',(),{'pid':value['pid']})() for role,value in status['owned_processes'].items()}
    def _invoke_management_capability(self,capability_id,arguments,confirmed=False):
        from fastmcp import Client
        async def call():
            async with Client(f'http://127.0.0.1:{self.port}/mcp',timeout=90) as client:
                result=await client.call_tool('capability_invoke',{'capability_id':capability_id,'arguments':arguments,'confirmation':'INVOKE' if confirmed else None})
                assert not result.is_error,result
                return result.data
        return asyncio.run(call())
    def stop(self):
        try:
            if self.process.poll() is None:self.dispatch('stop',{})
        finally:
            self.process.stdin.close()
            self.process.wait(timeout=30)
            self.stderr.close()

fixture=Path(__file__).resolve().parent/('repository_install.py' if args.fixture=='python' else 'repository_node_install.py')
namespace=runpy.run_path(str(fixture),run_name='frozen_fixture')
namespace['main'].__globals__['Manager']=FrozenManager
namespace['main']()
