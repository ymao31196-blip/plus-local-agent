import ctypes
from ctypes import wintypes
import re
import time

from tooling import local_tools
from runtime.session_runtime import InteractiveSessionStore


def _output_text(result):
    return "".join(
        event["data"]["content"]
        for event in result["events"]
        if event["type"] == "output"
    )


def _wait_for_text(store, session_id, needle, timeout=4.0):
    cursor = 0
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        seen += _output_text(result)
        if needle in seen:
            return seen
    raise AssertionError(f"Did not observe {needle!r}; saw {seen!r}")


def _process_is_running(pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def test_kill_on_close_job_reaps_descendant(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())

    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "print('CHILD:'+str(p.pid), flush=True); "
                "time.sleep(30)"
            ),
        ],
    )
    session_id = opened["session_id"]

    seen = _wait_for_text(store, session_id, "CHILD:")
    match = re.search(r"CHILD:(\d+)", seen)
    assert match is not None
    child_pid = int(match.group(1))
    assert _process_is_running(child_pid)

    store.close(session_id)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _process_is_running(child_pid):
        time.sleep(0.05)
    assert not _process_is_running(child_pid)


def test_close_all_terminates_running_sessions(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())

    store = InteractiveSessionStore(max_sessions=2)
    first = store.open(
        "python",
        ["-u", "-c", "import time; print('one', flush=True); time.sleep(30)"],
    )
    second = store.open(
        "python",
        ["-u", "-c", "import time; print('two', flush=True); time.sleep(30)"],
    )

    result = store.close_all()
    assert result == {
        "sessions_seen": 2,
        "sessions_closed": 2,
        "errors": 0,
    }
    assert store.read(first["session_id"])["status"] == "closed"
    assert store.read(second["session_id"])["status"] == "closed"


def test_conpty_session_open_write_resize_close(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())

    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import sys,time; "
                "print('CONPTY_READY', flush=True); "
                "line=sys.stdin.readline(); "
                "print('CONPTY_ECHO:'+line.strip(), flush=True); "
                "time.sleep(30)"
            ),
        ],
        terminal_mode="conpty",
        columns=100,
        rows=25,
    )
    session_id = opened["session_id"]
    assert opened["terminal_mode"] == "conpty"
    assert opened["permissions"]["route"] == "interactive_conpty_session"
    assert opened["columns"] == 100
    assert opened["rows"] == 25

    cursor = opened["next_cursor"]
    deadline = time.monotonic() + 5
    seen = ""
    while time.monotonic() < deadline and "CONPTY_READY" not in seen:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        seen += _output_text(result)
    assert "CONPTY_READY" in seen

    store.write(session_id, "hello-conpty\r")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "CONPTY_ECHO:hello-conpty" not in seen:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        seen += _output_text(result)
    assert "CONPTY_ECHO:hello-conpty" in seen

    resized = store.resize(session_id, 120, 40)
    assert resized["columns"] == 120
    assert resized["rows"] == 40
    state = store.read(session_id, cursor=cursor)
    assert state["terminal_mode"] == "conpty"
    assert state["columns"] == 120
    assert state["rows"] == 40

    closed = store.close(session_id)
    assert closed["status"] == "closed"
