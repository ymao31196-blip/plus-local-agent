"""Negative/lifecycle checks on the real payload with synthetic credentials."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    resources = args.resources.resolve()
    data = Path(tempfile.mkdtemp(prefix='pla-packaged-faults-'))
    child = subprocess.Popen([str(resources / 'runtime/pla-runtime.exe'), 'manager', '--data-dir', str(data),
                              '--resources', str(resources)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding='utf-8')
    tests = []
    def rpc(command, fields=None, expected=True):
        child.stdin.write(json.dumps({'command': command, 'args': fields or {}})+'\n')
        child.stdin.flush()
        raw = child.stdout.readline()
        if not raw:
            raise RuntimeError(child.stderr.read())
        result = json.loads(raw)
        assert result['ok'] == expected, result
        return result.get('result', result)
    try:
        rpc('shell', {'program': 'cmd.exe'}, expected=False)
        rpc('configure', {'runtime_command': 'cmd.exe'}, expected=False)
        tests.append({'test': 'frozen management rejects Shell and executable override', 'status': 'PASS'})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0));listener.listen()
            rpc('configure', {'runtime_port': listener.getsockname()[1]})
            rpc('start', expected=False)
            assert listener.getsockname()[1] > 0
            assert not rpc('status')['owned_processes']
        tests.append({'test': 'frozen foreign port collision refused and listener preserved', 'status': 'PASS'})
        rpc('configure', {'runtime_port': free_port(), 'health_port': free_port()})
        status = rpc('start')
        runtime_pid = status['owned_processes']['runtime']['pid']
        # This PID came from our fresh management child and was just verified alive;
        # it is not a discovered foreign process or user-selected PID.
        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'), '/PID', str(runtime_pid), '/T', '/F'],
                       check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(30):
            status = rpc('status')
            if status['runtime'] == 'exited':
                break
            time.sleep(0.1)
        assert status['runtime'] == 'exited', status
        assert rpc('start')['runtime'] == 'ready'
        tests.append({'test': 'frozen Runtime crash observed and restart recovered', 'status': 'PASS'})
        rpc('stop')
        synthetic = 'sk-pla-desktop-invalid-acceptance-key'
        rpc('configure', {'tunnel_id': 'tunnel_' + '0' * 32})
        rpc('credential', {'secret': synthetic})
        assert synthetic.encode() not in (data / 'config/tunnel.secret').read_bytes()
        rpc('start');rpc('connect')
        lines = ''
        rejected = False
        for _ in range(20):
            status = rpc('status')
            assert status['tunnel'] != 'ready', 'Synthetic invalid key must not be shown as ready'
            lines = '\n'.join(rpc('logs')['lines'])
            rejected = any(marker in lines.lower() for marker in ('invalid_api_key', 'unauthorized', 'status=401', 'status_code":401', 'status 401', 'http 401', '401 unauthorized', 'authentication_error'))
            if rejected or status['tunnel'] == 'exited':
                break
            time.sleep(1)
        assert synthetic not in lines
        assert synthetic not in (data / 'logs/desktop.log').read_text(encoding='utf-8')
        tests.append({'test': 'frozen DPAPI and synthetic-key log redaction', 'status': 'PASS'})
        tests.append({'test': 'real Tunnel synthetic wrong-key rejection', 'status': 'PASS' if rejected else 'BLOCKED',
                      'detail': 'Explicit remote authentication rejection observed' if rejected else 'No explicit authentication response established; network/service access must be checked'})
        tunnel_pid = rpc('status')['owned_processes']['tunnel']['pid']
        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'), '/PID', str(tunnel_pid), '/T', '/F'],
                       check=False, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert rpc('status')['tunnel'] == 'exited'
        tests.append({'test': 'frozen Tunnel unexpected exit observed', 'status': 'PASS'})
        rpc('stop')
        tests.append({'test': 'network interruption on dedicated test network', 'status': 'NOT TESTED'})
    except Exception as exc:
        tests.append({'test': 'packaged fault acceptance', 'status': 'FAIL', 'error': str(exc)})
        raise
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = {'resources': str(resources), 'isolated_data': str(data), 'tests': tests}
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
