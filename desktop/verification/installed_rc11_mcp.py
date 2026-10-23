"""Actual installed RC11 loopback MCP acceptance in a unique retained namespace.
Requires a running user-authorized Runtime. Does not start/stop services, change
credentials, or qualify Tunnel/ChatGPT. An optional read root targets a public
fixture; all mutations use the explicitly selected write/execute root.
"""
import argparse
import os
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from fastmcp import Client

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--resources',type=Path,required=True)
parser.add_argument('--data-dir',type=Path,default=Path(os.environ['LOCALAPPDATA'])/'io.pla.desktop')
parser.add_argument('--expected-root',action='append',default=[])
parser.add_argument('--read-root')
parser.add_argument('--read-path',default='README.md')
parser.add_argument('--write-root',default='workspace')
parser.add_argument('--report',type=Path,default=root/'.desktop-build/rc11-installed-mcp-diagnostic.json')
args=parser.parse_args()
data=args.data_dir.resolve()
config=json.loads((data/'config/desktop.json').read_text(encoding='utf-8'))
port=config['runtime_port']
assert type(port) is int and 1024 <= port <= 65535
script='''$taskListeners=@(Get-NetTCPConnection -State Listen -LocalPort PORT -ErrorAction Stop);
@($taskListeners | ForEach-Object {
    $taskOwner=Get-CimInstance Win32_Process -Filter ("ProcessId="+$_.OwningProcess);
    [pscustomobject]@{ip=$_.LocalAddress;pid=$_.OwningProcess;executable=$taskOwner.ExecutablePath}
}) | ConvertTo-Json -Compress'''.replace('PORT',str(port))
listeners=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-Command',script]).decode('utf-8-sig'))
if isinstance(listeners,dict):listeners=[listeners]
assert len(listeners)==1 and listeners[0]['ip']=='127.0.0.1'
pid=listeners[0]['pid']
executable=Path(listeners[0]['executable']).resolve()
assert executable==(args.resources/'runtime/pla-runtime.exe').resolve()
digest=hashlib.sha256(executable.read_bytes()).hexdigest()
assert digest=='2459e912fa0eaac48a281bbfb72f82709f7a7a4026364b557810e3cc874d0775'
async def probe():
    async with Client(f'http://127.0.0.1:{port}/mcp',timeout=12) as client:
        tools=await client.list_tools()
        assert 'diagnose_client' in {t.name for t in tools}
        result=await client.call_tool('diagnose_client',{})
        assert not result.is_error
        value=result.data
        assert set(args.expected_root).issubset(value['available_roots'])
        assert all(value['available_roots'][args.write_root][flag] for flag in ('read','write','execute'))
        namespace='acceptance-rc11-'+uuid.uuid4().hex
        marker='PLA_RC11_INSTALLED_'+uuid.uuid4().hex
        checks=[{'name':'real installed local MCP diagnostic','status':'PASS'}]
        async def call(name,args):
            result=await client.call_tool(name,args)
            assert not result.is_error,(name,result)
            return result.data
        if args.read_root:
            read=await call('read_text',{'root':args.read_root,'path':args.read_path,'start_line':1,'end_line':3})
            assert read is not None
            checks.append({'name':'read-only file under authorized root','status':'PASS','root':args.read_root,'path':args.read_path,'result':read})
        paths=[namespace+'/a.txt',namespace+'/b.txt']
        for path in paths:
            await call('write_text',{'root':args.write_root,'path':path,'content':marker+'\n','expected_sha256':None})
            actual=await call('read_text',{'root':args.write_root,'path':path,'start_line':1,'end_line':2})
            assert marker in json.dumps(actual)
        checks.append({'name':'create/read unique private-workspace files','status':'PASS'})
        changes=[{'path':path,'expected_sha256':hashlib.sha256((marker+'\n').encode()).hexdigest(),
                  'patch':f'--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{marker}\n+{marker}_TRANSACTION\n'} for path in paths]
        await call('apply_changeset',{'root':args.write_root,'changes':changes})
        for path in paths:
            actual=await call('read_text',{'root':args.write_root,'path':path,'start_line':1,'end_line':2})
            assert marker+'_TRANSACTION' in json.dumps(actual)
        checks.append({'name':'SHA-bound two-file transaction and reread','status':'PASS'})
        process=await call('run_process',{'root':args.write_root,'program':'python','args':['-c',f'print("{marker}")'],'cwd':namespace,'timeout':15})
        assert marker in json.dumps(process)
        checks.append({'name':'controlled bundled Python process','status':'PASS','result':process})
        return {'diagnostic':value,'tools':[t.name for t in tools],'checks':checks,
                'retained_test_files':paths,'marker':marker}
report={'status':'PASS','time':datetime.now(timezone.utc).isoformat(),
        'scope':'actual user-installed RC11 / loopback MCP / file create-read / SHA transaction / controlled Python; no remote Tunnel proof',
        'runtime_pid':pid,'runtime_executable':str(executable),'runtime_sha256':digest,
        **asyncio.run(probe())}
args.report.parent.mkdir(parents=True,exist_ok=True)
args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'runtime_pid':pid,'tool_count':len(report['tools']),'root_names':list(report['diagnostic']['available_roots'])}))
