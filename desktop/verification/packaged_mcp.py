"""Run against the actual frozen payload; writes isolated evidence, no credentials."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import struct
import urllib.error
import urllib.request
import zlib

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
        await call('write_text', {'root': 'approved', 'path': 'approved.txt', 'content': 'approved-one\n'})
        await call('replace_text', {'root': 'approved', 'path': 'approved.txt', 'old': 'approved-one', 'new': 'approved-two'})
        assert 'approved-two' in (await call('read_text', {'root': 'approved', 'path': 'approved.txt'}))['content']
        results.append({'test': 'frozen MCP file creation/read/modification in explicitly authorized external directory', 'status': 'PASS'})
        value = await call('run_process', {'program': 'python', 'args': ['-c', "print('PLA_PACKAGED_EXECUTION_OK')"]})
        assert value['returncode'] == 0 and 'PLA_PACKAGED_EXECUTION_OK' in value['stdout'], value
        results.append({'test': 'bundled Python controlled process', 'status': 'PASS', 'runner': value.get('runner')})
        value = await call('run_process', {'program': 'python', 'args': ['--version'], 'root': 'approved', 'backend': 'candidate_runner'})
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
            registry = await call('capability_invoke', {'capability_id': 'core.workspace_roots_get', 'arguments': {}})
            expected_sha = registry['data']['config_sha256']
            assert expected_sha
            try:
                value = await call('capability_invoke', {'capability_id': 'core.workspace_root_upsert',
                               'arguments': {'name': 'private', 'path': str(data), 'read': True, 'write': True,
                                             'execute': True, 'expected_sha256': expected_sha}, 'confirmation': 'INVOKE'})
                assert value['status'] == 'error' and 'private desktop data' in json.dumps(value), value
            except Exception as exc:
                from fastmcp.exceptions import ToolError
                assert isinstance(exc, ToolError) and 'private desktop data' in str(exc), exc
            results.append({'test': 'frozen MCP workspace registration rejects private data even with confirmation', 'status': 'PASS'})
    return results


def _web_request(base, path, *, method="GET", token=None, origin=None,
                 body=None, extra_headers=None):
    headers = dict(extra_headers or {})
    if token:
        headers["Authorization"] = "Bearer " + token
    if origin:
        headers["Origin"] = origin
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, {str(key).casefold(): value for key, value in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {str(key).casefold(): value for key, value in exc.headers.items()}, exc.read()


def _png_fixture():
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
            + chunk(b"IEND", b""))


def exercise_web_api(port, token, origin, data):
    base = f"http://127.0.0.1:{port}/web/v1"
    results = []

    status, headers, raw = _web_request(base, "/tools", token=token, origin=origin)
    assert status == 200, (status, raw[:500])
    catalog = json.loads(raw)
    names = {row.get("name") for row in catalog.get("tools", [])}
    assert names == {"list_directory", "read_text", "run_process"}, names
    assert catalog.get("allowed_roots") == ["workspace"], catalog
    assert headers.get("access-control-allow-origin") == origin
    results.append({"test": "packaged Web API authenticated discovery and exact allow-list", "status": "PASS",
                    "tool_count": len(names), "root_count": len(catalog["allowed_roots"])})

    status, _, _ = _web_request(base, "/tools", origin=origin)
    assert status == 401, status
    status, _, _ = _web_request(base, "/tools", token=token, origin="http://127.0.0.1:8897")
    assert status == 403, status
    status, headers, _ = _web_request(
        base, "/tasks", method="OPTIONS", origin=origin,
        extra_headers={"Access-Control-Request-Method": "POST",
                       "Access-Control-Request-Headers": "Authorization, Content-Type"},
    )
    assert status == 200 and headers.get("access-control-allow-origin") == origin, (status, headers)
    results.append({"test": "packaged Web API rejects missing credentials and unapproved origins", "status": "PASS"})

    payload = {"tool": "run_process", "arguments": {
        "program": "python", "args": ["-c", "print('PLA_WEB_API_PACKAGED_OK')"],
        "root": "workspace",
    }}
    status, _, raw = _web_request(base, "/tasks", method="POST", token=token,
                                 origin=origin, body=payload)
    assert status == 202, (status, raw[:1000])
    task_id = json.loads(raw)["task_id"]
    deadline = time.monotonic() + 30
    task = None
    while time.monotonic() < deadline:
        status, _, raw = _web_request(base, "/tasks/" + task_id, token=token, origin=origin)
        assert status == 200, (status, raw[:1000])
        task = json.loads(raw)
        if task.get("status") in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.2)
    assert task and task.get("status") == "completed", task
    assert "PLA_WEB_API_PACKAGED_OK" in json.dumps(task.get("result")), task
    results.append({"test": "packaged Web API submits and tracks a real local process task", "status": "PASS"})

    workspace = data / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    image = _png_fixture()
    (workspace / "packaged-web-api.png").write_bytes(image)
    status, headers, received = _web_request(
        base, "/artifact?root=workspace&path=packaged-web-api.png",
        token=token, origin=origin,
    )
    assert status == 200 and headers.get("content-type") == "image/png" and received == image
    results.append({"test": "packaged Web API returns a bounded PNG artifact", "status": "PASS",
                    "artifact_bytes": len(received)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    resources = args.resources.resolve()
    data = Path(tempfile.mkdtemp(prefix='pla-desktop-packaged-'))
    authorized = Path(tempfile.mkdtemp(prefix='pla-approved-workspace-'))
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
    report = {'payload': str(resources), 'isolated_data': str(data), 'authorized_workspace': str(authorized), 'tests': [],
              'tunnel_e2e': 'NOT TESTED', 'chatgpt_e2e': 'NOT TESTED', 'clean_windows': 'NOT TESTED'}
    try:
        rpc('status')
        rpc('workspace_save', {'name': 'approved', 'path': str(authorized), 'read': True, 'write': True, 'execute': True, 'expected_sha256': None})
        # An ephemeral port selected only for this isolated test.
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            browser_port = listener.getsockname()[1]
        origin = 'http://127.0.0.1:8896'
        rpc('configure', {
            'runtime_port': port, 'browser_port': browser_port, 'browser_enabled': args.browser,
            'web_api_enabled': True, 'web_api_allowed_origins': [origin],
            'web_api_allowed_tools': ['list_directory', 'read_text', 'run_process'],
            'web_api_allowed_roots': ['workspace'],
        })
        assert rpc('start')['runtime'] == 'ready'
        assert rpc('verify')['local_mcp'] == 'PASS'
        credentials = rpc('web_api_token')
        assert credentials['endpoint'] == f'http://127.0.0.1:{port}/web/v1'
        probe = rpc('web_api_test')
        assert probe['ok'] and probe['tool_count'] == 3 and probe['root_count'] == 1, probe
        report['tests'] = asyncio.run(exercise(port, args.browser, data))
        report['tests'] += exercise_web_api(port, credentials['token'], origin, data)
        # Exercise the actual frozen importer, not the host's pypdf module.
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_blank_page(width=72, height=72)
        with (authorized / 'sample.pdf').open('wb') as handle:
            writer.write(handle)
        async def read_pdf():
            async with Client(f'http://127.0.0.1:{port}/mcp', timeout=15) as client:
                result = await client.call_tool('extract_document_text', {
                    'root': 'approved', 'path': 'sample.pdf', 'start': 2, 'end': 2})
                assert not result.is_error, result
                assert result.data['format'] == 'pdf' and result.data['unit_count'] == 2, result
                assert result.data['content'].startswith('[Page 2]'), result
        asyncio.run(read_pdf())
        report['tests'].append({'test': 'frozen PDF text extraction with bundled pypdf', 'status': 'PASS'})
        first_token = credentials['token']
        rpc('stop')
        assert rpc('start')['runtime'] == 'ready'
        renewed = rpc('web_api_token')
        assert renewed['token'] != first_token, 'Web API token did not rotate on Runtime restart'
        assert rpc('web_api_test')['ok']
        report['tests'].append({'test': 'frozen stop/restart, persistent configuration and Web API token rotation', 'status': 'PASS'})
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
