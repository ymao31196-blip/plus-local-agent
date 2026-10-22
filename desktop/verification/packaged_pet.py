"""Real frozen pet lifecycle with generated, copyright-free test PNGs only."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import socket
import struct
import tempfile
import time
import zlib

from components_mcp import FrozenManager


def visible_pet(pid):
    found = []
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value == 'PLA Agent Pet':
                found.append(hwnd)
        return True
    user32.EnumWindows(callback, 0)
    return bool(found)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    data = Path(tempfile.mkdtemp(prefix='pla-rc17-pet-'))
    manager = FrozenManager(data, args.resources.resolve())
    checks = []
    def check(name, condition):
        assert condition, name
        checks.append({'test': name, 'status': 'PASS'})
        print('PASS ' + name, flush=True)
    def wait_window(pid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if visible_pet(pid):
                return True
            time.sleep(.2)
        return False
    try:
        check('default disabled without artwork', manager.dispatch('pet_status')['enabled'] is False)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        manager.dispatch('configure', {'runtime_port': port})
        check('Runtime starts without artwork', manager.start()['runtime'] == 'ready')
        check('no default pet process', 'agent-pet' not in manager.dispatch('status')['owned_processes'])
        manager.dispatch('stop')
        frames = data / 'components/agent-pet/frames3d'
        frames.mkdir(parents=True)
        def chunk(kind, payload):
            return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 200, 200, 8, 6, 0, 0, 0))
        png += chunk(b'IDAT', zlib.compress((b'\x00' + b'\x55\xbb\x99\xff' * 200) * 200)) + chunk(b'IEND', b'')
        for label, count in [('idle',6), ('work',6), ('waiting',6), ('review',6), ('failed',8)]:
            for index in range(count):
                (frames / f'{label}_{index}.png').write_bytes(png)
        check('enable while stopped waits', manager.dispatch('pet_action', {'enabled': True, 'confirmed': True})['state'] == 'waiting_runtime')
        started = manager.start()
        pid = started['agent_pet']['pid']
        runtime_pid = started['owned_processes']['runtime']['pid']
        check('frozen Tk loads PNG and creates real visible window', wait_window(pid))
        manager.dispatch('pet_action', {'enabled': False, 'confirmed': True})
        check('disable closes pet and preserves Runtime PID', not visible_pet(pid) and manager.dispatch('status')['owned_processes']['runtime']['pid'] == runtime_pid)
        pid = manager.dispatch('pet_action', {'enabled': True, 'confirmed': True})['pid']
        check('live enable creates visible pet', wait_window(pid))
        manager.dispatch('stop')
        check('Runtime stop closes owned pet', not visible_pet(pid))
        started = manager.start()
        pid = started['agent_pet']['pid']
        check('saved preference restarts visible pet', wait_window(pid))
        manager.stop()
        check('manager exit closes owned pet', not visible_pet(pid))
    except Exception as exc:
        checks.append({'test': 'frozen pet acceptance', 'status': 'FAIL', 'error': str(exc)})
        raise
    finally:
        if manager.process.poll() is None:
            manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'data': str(data), 'artwork': 'generated solid-color PNG; not distributed', 'checks': checks}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
