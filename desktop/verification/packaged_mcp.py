"""Run against the actual frozen payload; writes isolated evidence, no credentials."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from fastmcp import Client


async def exercise(port, browser=False, data=None):
    results = []
    async with Client(f'http://127.0.0.1:{port}/mcp', timeout=15) as client:
        async def call(name, args):
            value = await client.call_tool(name, args)
            assert not value.is_error, (name, value)
            return value.data
        tools = await client.list_tools()
        assert {'read_text', 'write_text', 'run_process', 'apply_changeset'} <= {tool.name for tool in tools}
        results.append({'test': 'frozen MCP discovery', 'status': 'PASS', 'tool_count': len(tools)})
        await call('write_text', {'path': 'a.txt', 'content': 'first\n'})
        value = await call('read_text', {'path': 'a.txt'})
        assert 'first' in value['content']
        await call('replace_text', {'path': 'a.txt', 'old': 'first', 'new': 'second'})
        assert 'second' in (await call('read_text', {'path': 'a.txt'}))['content']
        results.append({'test': 'frozen file read/create/modify', 'status': 'PASS'})
        value = await call('run_process', {'program': 'python', 'args': ['-c', "print('PLA_PACKAGED_EXECUTION_OK')"]})
        assert value['returncode'] == 0 and 'PLA_PACKAGED_EXECUTION_OK' in value['stdout'], value
        results.append({'test': 'bundled Python controlled process', 'status': 'PASS', 'runner': value.get('runner')})
        value = await call('run_process', {'program': 'python', 'args': ['--version'], 'backend': 'candidate_runner'})
        assert value['returncode'] == 0 and value['runner']['process_isolation'] == 'separate_process', value
        results.append({'test': 'frozen named-pipe execution runner', 'status': 'PASS'})
        await call('write_text', {'path': 'b.txt', 'content': 'before\n'})
        changes = []
        for path, before, after in [('a.txt', 'second', 'third'), ('b.txt', 'before', 'after')]:
            changes.append({'path': path, 'expected_sha256': hashlib.sha256((before+'\n').encode()).hexdigest(),
                            'patch': f'--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n'})
        await call('apply_changeset', {'changes': changes})
        assert 'third' in (await call('read_text', {'path': 'a.txt'}))['content']
        assert 'after' in (await call('read_text', {'path': 'b.txt'}))['content']
        results.append({'test': 'frozen transactional two-file changeset', 'status': 'PASS'})
        if browser:
            import http.server
            import threading
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(b'<html><head><title>PLA packaged browser fixture</title></head><body><h1>PLA_BROWSER_PACKAGED_OK</h1></body></html>')
                def log_message(self, *args):
                    pass
            httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                available = await call('capability_describe', {'capability_id': 'browser.inspect'})
                assert available['available'], available
                navigated = await call('capability_invoke', {'capability_id': 'browser.navigate',
                                       'arguments': {'url': f'http://127.0.0.1:{httpd.server_port}/'}})
                assert navigated['status'] == 'completed', navigated
                inspected = await call('capability_invoke', {'capability_id': 'browser.inspect', 'arguments': {}})
                assert 'PLA_BROWSER_PACKAGED_OK' in json.dumps(inspected), inspected
                results.append({'test': 'packaged browser Provider navigate and semantic inspection', 'status': 'PASS'})
            finally:
                httpd.shutdown()
                httpd.server_close()
        for name, args in [('read_text', {'path': '../config/desktop.json'}),
                           ('run_process', {'program': 'cmd.exe', 'args': ['/c', 'echo bypass']}),
                           ('write_text', {'root': 'pla', 'path': 'unauthorized-install-mutation.txt', 'content': 'must be refused'}),
                           ('read_text', {'root': 'pla', 'path': 'state/private.secret'})]:
            try:
                value = await client.call_tool(name, args, raise_on_error=False)
                assert value.is_error, (name, 'unsafe call accepted')
            except Exception as exc:
                # Only tool errors establish rejection, not network/transport failures.
                from fastmcp.exceptions import ToolError
                if not isinstance(exc, ToolError):
                    raise
        results.append({'test': 'frozen traversal/program/private-path rejection', 'status': 'PASS'})
        if data:
            try:
                value = await call('capability_invoke', {'capability_id': 'core.workspace_root_upsert',
                               'arguments': {'name': 'private', 'path': str(data), 'read': True, 'write': True,
                                             'execute': True, 'expected_sha256': None}, 'confirmation': 'INVOKE'})
                assert value['status'] == 'error' and 'private desktop data' in json.dumps(value), value
            except Exception as exc:
                from fastmcp.exceptions import ToolError
                assert isinstance(exc, ToolError) and 'private desktop data' in str(exc), exc
            results.append({'test': 'frozen MCP workspace registration rejects private data even with confirmation', 'status': 'PASS'})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    resources = args.resources.resolve()
    data = Path(tempfile.mkdtemp(prefix='pla-desktop-packaged-'))
    process = subprocess.Popen([str(resources / 'runtime/pla-runtime.exe'), 'manager', '--data-dir', str(data),
                                '--resources', str(resources)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8')
    def rpc(command, fields=None):
        process.stdin.write(json.dumps({'command': command, 'args': fields or {}})+'\n')
        process.stdin.flush()
        raw = process.stdout.readline()
        if not raw:
            raise RuntimeError(process.stderr.read())
        result = json.loads(raw)
        assert result['ok'], result
        return result['result']
    report = {'payload': str(resources), 'isolated_data': str(data), 'tests': [],
              'tunnel_e2e': 'NOT TESTED', 'chatgpt_e2e': 'NOT TESTED', 'clean_windows': 'NOT TESTED'}
    try:
        rpc('status')
        # An ephemeral port selected only for this isolated test.
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            browser_port = listener.getsockname()[1]
        rpc('configure', {'runtime_port': port, 'browser_port': browser_port, 'browser_enabled': args.browser})
        assert rpc('start')['runtime'] == 'ready'
        assert rpc('verify')['local_mcp'] == 'PASS'
        report['tests'] = asyncio.run(exercise(port, args.browser, data))
        rpc('stop')
        assert rpc('start')['runtime'] == 'ready'
        report['tests'].append({'test': 'frozen stop/restart and persistent configuration', 'status': 'PASS'})
        rpc('stop')
    except Exception as exc:
        report['tests'].append({'test': 'packaged MCP acceptance', 'status': 'FAIL', 'error': str(exc)})
        raise
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
