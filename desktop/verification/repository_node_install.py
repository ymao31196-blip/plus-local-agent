"""Real repository Node dependency resolution and MCP/Broker invocation."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from desktop_runtime.manager import Manager


SERVER = r'''
const readline = require('node:readline');
const isNumber = require('is-number');
readline.createInterface({input:process.stdin}).on('line',line=>{
  const req=JSON.parse(line);
  if(req.id===undefined)return;
  let result;
  switch(req.method){
    case 'initialize': result={protocolVersion:req.params.protocolVersion,
      capabilities:{tools:{}},serverInfo:{name:'reviewed-node-echo',version:'1.0.0'}};break;
    case 'ping': result={};break;
    case 'tools/list': result={tools:[{name:'number_echo',description:'Echo numeric input',
      inputSchema:{type:'object',properties:{value:{type:'string'}},required:['value']}}]};break;
    case 'tools/call': result={content:[{type:'text',text:JSON.stringify({
      value:req.params.arguments.value,numeric:isNumber(req.params.arguments.value)})}],isError:false};break;
    default: process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,error:{code:-32601,message:'Method not found'}})+'\n');return;
  }
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,result})+'\n');
});
'''


def main():
    root=Path(tempfile.mkdtemp(prefix='pla-node-repository-e2e-'))
    resources=Path(__file__).resolve().parents[1]/'src-tauri/resources'
    repository=root/'repository'
    version=repository/'providers/node-echo/versions/1.0.0'
    version.mkdir(parents=True)
    spec={'schema_version':1,'provider_id':'node-echo','version':'1.0.0',
          'runtime_kind':'executable_stdio', 'dependencies':{'python_packages':[],
          'node_packages':['is-number@7.0.0'],'system':[],'providers':[]}}
    manifest={'schema_version':1,'id':'node-echo','autostart':False,
              'runtime':{'kind':'executable_stdio','command':'node.exe','args':['providers/server.cjs'],
                         'cwd':'.','discovery_timeout_seconds':20,'invoke_timeout_seconds':30},
              'tool_allowlist':['number_echo']}
    files={'spec.json':json.dumps(spec).encode(),'manifest.json':json.dumps(manifest).encode(),
           'assets/providers/server.cjs':SERVER.encode()}
    for name,content in files.items():
        target=version/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    (version/'checksums.json').write_text(json.dumps({'schema_version':1,'provider_id':'node-echo',
        'version':'1.0.0','files':{k:'sha256:'+hashlib.sha256(v).hexdigest() for k,v in files.items()}}))
    manager=Manager(root/'data',resources)
    evidence={'root':str(root),'scope':'source Manager / actual npm / Runtime Broker',
              'tunnel':'NOT TESTED','chatgpt':'NOT TESTED','tests':[]}
    print('EVIDENCE_ROOT '+str(root),flush=True)
    try:
        args={'provider_id':'node-echo','repository_url':repository.as_uri(),
              'version':'1.0.0','confirmed':False,'expected_sha256':None}
        plan=manager.dispatch('provider_install_remote',args)
        started=manager.dispatch('provider_install_remote',{**args,'confirmed':True,'expected_sha256':plan['sha256']})
        def wait(job):
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                status=next(x for x in manager.dispatch('installation_status',{})['jobs'] if x['job']==job['job'])
                if status['state']!='running': break
                time.sleep(1)
            assert status['state']=='completed',status
            return status
        evidence['installation']=wait(started)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        manager.config.save({'runtime_port':port});manager.start()
        pid=manager.children['runtime'].pid
        manager.dispatch('provider_action',{'action':'enable','provider_id':'node-echo','confirmed':True})
        result=manager._invoke_management_capability('node-echo.number_echo',{'value':'42'},confirmed=True)
        assert 'true' in json.dumps(result).lower() and '42' in json.dumps(result),result
        evidence['invocation']=result
        evidence['tests'].append({'name':'managed Node / private npm dependency / Broker tool','status':'PASS'})
        manager.dispatch('provider_action',{'action':'disable','provider_id':'node-echo','confirmed':True})
        args={'provider_id':'node-echo','confirmed':False,'expected_sha256':None}
        plan=manager.dispatch('provider_uninstall_remote',args)
        wait(manager.dispatch('provider_uninstall_remote',{**args,'confirmed':True,'expected_sha256':plan['sha256']}))
        assert not(manager.components.root/'.provider_envs/node-echo/1.0.0').exists()
        assert not list((manager.components.root/'.repository-trash').glob('*'))
        assert manager.children['runtime'].pid==pid
        evidence['tests'].append({'name':'disable/uninstall/private npm cleanup/Runtime preserved','status':'PASS'})
        evidence['status']='PASS'
    except BaseException as error:
        evidence['status']='FAIL';evidence['error']=str(error);raise
    finally:
        manager.stop()
        (root/'node-repository-evidence.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':evidence.get('status'),'evidence':str(root/'node-repository-evidence.json')}),flush=True)


if __name__=='__main__':main()
