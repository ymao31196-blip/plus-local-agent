"""Actual source Manager -> owned installer -> isolated Python -> MCP test.

Preserves its uniquely allocated Temp data and writes evidence for diagnosis.
No Tunnel or ChatGPT proof is inferred from this local stdio test.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import shutil
import hashlib
import socket

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from desktop_runtime.manager import Manager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def echo(root):
    manifest=json.loads((root/'components/provider_manifests/test-echo-provider.json').read_text())
    runtime=manifest['runtime']
    params=StdioServerParameters(command=str(root/'components'/runtime['python']),
        args=runtime['args'],cwd=str(root/'components'/runtime['cwd']))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            assert {'echo_message','echo_structured'}=={tool.name for tool in tools.tools}
            result=await session.call_tool('echo_message',{'message':'Hello from reviewed repository'})
            assert not result.is_error
            assert any('Echo: Hello from reviewed repository' in getattr(item,'text','') for item in result.content)
            return {'tools':[tool.name for tool in tools.tools],'result':result.model_dump(mode='json')}


def main():
    repository_source=Path(__file__).resolve().parents[2]/'test-provider-repository'
    resources=Path(__file__).resolve().parents[1]/'src-tauri/resources'
    root=Path(tempfile.mkdtemp(prefix='pla-repository-e2e-'))
    repository=root/'test-repository'
    shutil.copytree(repository_source,repository)
    versions=repository/'providers/test-echo-provider/versions'
    shutil.copytree(versions/'1.0.0',versions/'1.0.1')
    spec=versions/'1.0.1/spec.json'
    value=json.loads(spec.read_text());value['version']='1.0.1'
    spec.write_text(json.dumps(value,indent=2),encoding='utf-8')
    hashes=versions/'1.0.1/checksums.json'
    value=json.loads(hashes.read_text());value['version']='1.0.1'
    value['files']['spec.json']='sha256:'+hashlib.sha256(spec.read_bytes()).hexdigest()
    hashes.write_text(json.dumps(value,indent=2),encoding='utf-8')
    print('EVIDENCE_ROOT '+str(root),flush=True)
    manager=Manager(root,resources)
    evidence={'root':str(root),'scope':'source Manager / real installer / local stdio MCP',
              'tunnel':'NOT TESTED','chatgpt':'NOT TESTED','tests':[]}
    def wait(started):
        deadline=time.monotonic()+900
        while time.monotonic()<deadline:
            job=next(row for row in manager.dispatch('installation_status',{})['jobs'] if row['job']==started['job'])
            if job['state']!='running':break
            time.sleep(1)
        assert job['state']=='completed',job
        return job
    try:
        args={'provider_id':'test-echo-provider','repository_url':repository.as_uri(),
              'version':'1.0.0','confirmed':False,'expected_sha256':None}
        preview=manager.dispatch('provider_install_remote',args)
        start=time.monotonic()
        started=manager.dispatch('provider_install_remote',{**args,'confirmed':True,'expected_sha256':preview['sha256']})
        elapsed=time.monotonic()-start
        assert elapsed<10,elapsed
        evidence['tests'].append({'name':'asynchronous confirmed installation','status':'PASS','elapsed_seconds':elapsed})
        job=wait(started)
        evidence['tests'].append({'name':'real verified uv/Python/dependency installation','status':'PASS','job':job})
        evidence['mcp']=asyncio.run(echo(root))
        evidence['tests'].append({'name':'real MCP discovery and echo invocation','status':'PASS'})
        health=manager.dispatch('provider_health_check',{'provider_id':'test-echo-provider'})
        assert health['healthy'] and health['mcp_connection_verified'] is False
        evidence['health']=health
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        manager.config.save({'runtime_port':port})
        manager.start();runtime_pid=manager.children['runtime'].pid
        manager.dispatch('provider_action',{'action':'enable','provider_id':'test-echo-provider','confirmed':True})
        outcome=manager._invoke_management_capability('test-echo-provider.echo_message',{'message':'Hello through PLA Runtime'},confirmed=True)
        assert 'Hello through PLA Runtime' in json.dumps(outcome),outcome
        evidence['tests'].append({'name':'real Runtime hot enable / Broker echo invocation','status':'PASS','result':outcome})
        manager.dispatch('provider_action',{'action':'disable','provider_id':'test-echo-provider','confirmed':True})
        upgrade={'provider_id':'test-echo-provider','repository_url':repository.as_uri(),
                 'to_version':'1.0.1','confirmed':False,'expected_sha256':None}
        preview=manager.dispatch('provider_upgrade_remote',upgrade)
        wait(manager.dispatch('provider_upgrade_remote',{**upgrade,'confirmed':True,'expected_sha256':preview['sha256']}))
        record=manager.components._lifecycle_manager.get_provider_state('test-echo-provider')
        assert record.version=='1.0.1' and len(record.metadata['previous_versions'])==1
        evidence['upgraded_mcp']=asyncio.run(echo(root))
        manager.dispatch('provider_action',{'action':'enable','provider_id':'test-echo-provider','confirmed':True})
        outcome=manager._invoke_management_capability('test-echo-provider.echo_message',{'message':'Hello after upgrade'},confirmed=True)
        assert 'Hello after upgrade' in json.dumps(outcome),outcome
        evidence['tests'].append({'name':'real versioned upgrade and Runtime reconnect','status':'PASS'})
        uninstall={'provider_id':'test-echo-provider','confirmed':False,'expected_sha256':None}
        preview=manager.dispatch('provider_uninstall_remote',uninstall)
        try:
            manager.dispatch('provider_uninstall_remote',{**uninstall,'confirmed':True,'expected_sha256':preview['sha256']})
            raise AssertionError('Enabled provider uninstall accepted')
        except ValueError as error:
            assert 'enabled' in str(error),error
        manager.dispatch('provider_action',{'action':'disable','provider_id':'test-echo-provider','confirmed':True})
        wait(manager.dispatch('provider_uninstall_remote',{**uninstall,'confirmed':True,'expected_sha256':preview['sha256']}))
        assert manager.components._lifecycle_manager.get_provider_state('test-echo-provider') is None
        assert all(not(manager.components.root/f'.provider_envs/test-echo-provider/{version}').exists() for version in ('1.0.0','1.0.1'))
        assert not list((manager.components.root/'.repository-trash').glob('*')), 'Owned environment cleanup left files behind'
        assert manager.children['runtime'].pid==runtime_pid
        evidence['tests'].append({'name':'enabled uninstall refused / disable / uninstall both owned versions / Runtime unchanged','status':'PASS'})
        evidence['status']='PASS'
    except BaseException as error:
        evidence['status']='FAIL';evidence['error']=str(error)
        raise
    finally:
        manager.stop()
        (root/'repository-evidence.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':evidence.get('status'),'evidence':str(root/'repository-evidence.json')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
