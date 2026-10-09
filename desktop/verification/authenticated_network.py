"""Real authenticated disconnect/recovery through a test-only TCP CONNECT proxy.

Run after stopping the owner's desktop test services to avoid competing clients
on the same test Tunnel. Credentials are read only from an explicitly supplied
file, sent to the frozen manager over its private stdin, and never reported.
TLS remains end-to-end between the client and api.openai.com. No OS proxy,
firewall, trust store, or network setting is changed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import subprocess
import tempfile
import threading
import time


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class NetworkGate(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self):
        self.blocked = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.forwarded = 0
        super().__init__(('127.0.0.1', 0), ConnectHandler)

    def drop(self):
        self.blocked.set()
        with self.lock:
            pairs = list(self.connections)
        for pair in pairs:
            for connection in pair:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(10)
        upstream = None
        pair = None
        try:
            request = b''
            while b'\r\n\r\n' not in request and len(request) <= 8192:
                block = client.recv(2048)
                if not block:
                    return
                request += block
            # This gate cannot forward to unrelated destinations.
            if request.split(b'\r\n', 1)[0].split()[:2] != [b'CONNECT', b'api.openai.com:443']:
                client.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                return
            if self.server.blocked.is_set():
                client.sendall(b'HTTP/1.1 503 Test network disconnected\r\nContent-Length: 0\r\n\r\n')
                return
            upstream = socket.create_connection(('api.openai.com', 443), timeout=10)
            pair = (client, upstream)
            with self.server.lock:
                self.server.connections.add(pair)
                self.server.forwarded += 1
            client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            while not self.server.blocked.is_set():
                ready, _, _ = select.select(pair, [], [], 1)
                for incoming in ready:
                    payload = incoming.recv(65536)
                    if not payload:
                        return
                    (upstream if incoming is client else client).sendall(payload)
        except OSError:
            pass
        finally:
            if pair:
                with self.server.lock:
                    self.server.connections.discard(pair)
            if upstream:
                upstream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    resources = args.resources.resolve()
    supplied = args.credential_file.read_text(encoding='utf-8-sig')
    tunnel_match = re.search(r'tunnel_[a-z0-9]{32}', supplied)
    key_match = re.search(r'sk-[A-Za-z0-9_-]+', supplied)
    if not tunnel_match or not key_match:
        raise ValueError('The supplied credential file lacks a test Tunnel ID or key')
    tunnel_id, secret = tunnel_match.group(), key_match.group()
    data = Path(tempfile.mkdtemp(prefix='pla-authenticated-network-'))
    gate = NetworkGate()
    threading.Thread(target=gate.serve_forever, daemon=True).start()
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}}
    proxy = f'http://127.0.0.1:{gate.server_address[1]}'
    env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY='127.0.0.1,localhost,::1')
    child = subprocess.Popen([str(resources / 'runtime/pla-runtime.exe'), 'manager',
                              '--data-dir', str(data), '--resources', str(resources)],
                             env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding='utf-8')
    evidence = {'resources': str(resources), 'data': str(data), 'tests': [],
                'os_network_settings_changed': False, 'tls_intercepted': False}

    def rpc(command, fields=None):
        child.stdin.write(json.dumps({'command': command, 'args': fields or {}})+'\n')
        child.stdin.flush()
        response = json.loads(child.stdout.readline())
        if not response['ok']:
            raise RuntimeError(response.get('error', 'Management request failed'))
        return response['result']

    def await_state(expected, timeout=100):
        deadline = time.monotonic() + timeout
        status = None
        while time.monotonic() < deadline:
            status = rpc('status')
            if status['tunnel'] == expected:
                return status
            time.sleep(1)
        raise AssertionError(f'Tunnel did not reach {expected}; last state {status["tunnel"]}')

    try:
        rpc('configure', {'runtime_port': free_port(), 'health_port': free_port(), 'tunnel_id': tunnel_id})
        rpc('credential', {'secret': secret})
        rpc('start')
        rpc('connect')
        initial = await_state('ready')
        assert gate.forwarded > 0, 'Tunnel bypassed the test proxy; interruption would not be real'
        runtime_pid = initial['owned_processes']['runtime']['pid']
        tunnel_pid = initial['owned_processes']['tunnel']['pid']
        evidence['tests'].append({'test': 'installed frozen manager genuine authenticated connection through gate',
                                  'status': 'PASS', 'successful_poll': initial['tunnel_last_successful_poll']})
        gate.drop()
        disconnected = await_state('disconnected')
        assert disconnected['runtime'] == 'ready'
        assert disconnected['owned_processes']['runtime']['pid'] == runtime_pid
        assert disconnected['owned_processes']['tunnel']['pid'] == tunnel_pid
        evidence['tests'].append({'test': 'actual proxy sockets severed; frozen status observes disconnected while Runtime stays ready',
                                  'status': 'PASS'})
        time.sleep(3)
        assert rpc('status')['tunnel'] == 'disconnected'
        gate.blocked.clear()
        recovered = await_state('ready')
        assert recovered['tunnel_last_successful_poll'] > initial['tunnel_last_successful_poll']
        assert recovered['owned_processes']['runtime']['pid'] == runtime_pid
        assert recovered['owned_processes']['tunnel']['pid'] == tunnel_pid
        evidence['tests'].append({'test': 'authenticated remote polling recovers without restarting Runtime or Tunnel',
                                  'status': 'PASS', 'successful_poll': recovered['tunnel_last_successful_poll']})
        credential_blob = (data / 'config/tunnel.secret').read_bytes()
        assert secret.encode() not in credential_blob
        assert secret not in (data / 'logs/desktop.log').read_text(encoding='utf-8')
        evidence['tests'].append({'test': 'real key remains encrypted and absent from disconnected/recovery logs', 'status': 'PASS'})
        evidence['proxy_connections_forwarded'] = gate.forwarded
    except Exception as exc:
        safe = str(exc).replace(secret, '[REDACTED]').replace(tunnel_id, '[TEST_TUNNEL]')
        evidence['tests'].append({'test': 'authenticated network acceptance', 'status': 'FAIL', 'error': safe})
        raise RuntimeError(safe) from None
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # This Popen is ours; never terminate a discovered foreign process.
            subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                            '/PID', str(child.pid), '/T', '/F'], check=True,
                           capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            child.wait(timeout=10)
        # This file was created by this test, not the user-supplied credential file.
        (data / 'config/tunnel.secret').unlink(missing_ok=True)
        evidence['isolated_credential_removed'] = True
        gate.drop()
        gate.shutdown()
        gate.server_close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
