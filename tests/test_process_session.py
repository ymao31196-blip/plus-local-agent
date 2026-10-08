import base64
from threading import Barrier, Thread
import time

import pytest

from tooling import local_tools
from runtime.session_runtime import InteractiveSessionStore, MAX_INPUT_CHARACTERS


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", root.resolve())
    return root


def _wait_for_text(store, session_id, cursor, needle, timeout=4.0):
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        for event in result["events"]:
            if event["type"] == "output":
                seen += event["data"]["content"]
        if needle in seen:
            return result, cursor, seen
    raise AssertionError(f"Did not observe {needle!r}; saw {seen!r}")


def test_persistent_session_open_write_read_close(workspace):
    store = InteractiveSessionStore(max_sessions=2)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import sys,time; "
                "print('READY', flush=True); "
                "line=sys.stdin.readline(); "
                "print('ECHO:'+line.strip(), flush=True); "
                "time.sleep(30)"
            ),
        ],
    )

    assert opened["status"] == "running"
    assert opened["terminal_mode"] == "pipe"
    assert opened["permissions"]["route"] == "interactive_pipe_session"
    assert opened["permissions"]["sandbox_mode"] == "none"
    assert opened["permissions"]["semantic_domain"] == "python"
    assert opened["permissions"]["semantic_effect_class"] == "arbitrary_code"

    session_id = opened["session_id"]
    cursor = opened["next_cursor"]
    _, cursor, seen = _wait_for_text(store, session_id, cursor, "READY")
    assert "READY" in seen

    written = store.write(session_id, "hello\n")
    assert written["characters_written"] == 6

    _, cursor, seen = _wait_for_text(store, session_id, cursor, "ECHO:hello")
    assert "ECHO:hello" in seen

    closed = store.close(session_id)
    assert closed["status"] == "closed"

    final = store.read(session_id, cursor=cursor, wait_seconds=1)
    assert final["status"] == "closed"
    assert any(event["type"] == "process_exit" for event in final["events"])


def test_persistent_session_rejects_outside_workdir(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    with pytest.raises(ValueError, match="outside workspace"):
        store.open(program="python", cwd="../outside")


def test_persistent_session_uses_program_allowlist(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    with pytest.raises(ValueError, match="Program not allowed"):
        store.open(program="powershell.exe")


def test_persistent_session_bounds_write_input(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=["-u", "-c", "import time; time.sleep(30)"],
    )
    try:
        with pytest.raises(ValueError, match="input cannot exceed"):
            store.write(opened["session_id"], "x" * (MAX_INPUT_CHARACTERS + 1))
    finally:
        store.close(opened["session_id"])


def test_persistent_session_limit_evicts_terminal_sessions(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    first = store.open(
        program="python",
        args=["-u", "-c", "print('done', flush=True)"],
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = store.read(first["session_id"], wait_seconds=0.1)
        if state["status"] == "exited":
            break
    assert state["status"] == "exited"

    second = store.open(
        program="python",
        args=["-u", "-c", "import time; time.sleep(30)"],
    )
    try:
        with pytest.raises(ValueError, match="Unknown process session"):
            store.read(first["session_id"])
    finally:
        store.close(second["session_id"])


def test_persistent_session_supports_raw_base64_input(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import sys,time; "
                "data=sys.stdin.buffer.read(4); "
                "print('RAW:'+data.hex(), flush=True); "
                "time.sleep(30)"
            ),
        ],
    )
    session_id = opened["session_id"]
    cursor = opened["next_cursor"]
    try:
        payload = b"\x00A\r\n"
        written = store.write_base64(
            session_id,
            base64.b64encode(payload).decode("ascii"),
        )
        assert written["bytes_written"] == 4
        assert written["operation_seq"] == 1
        _, cursor, seen = _wait_for_text(
            store, session_id, cursor, "RAW:00410d0a"
        )
        assert "RAW:00410d0a" in seen
        state = store.read(session_id, cursor=cursor)
        assert state["operation_seq"] == 1
        assert state["stdin_closed"] is False
    finally:
        store.close(session_id)


def test_close_stdin_delivers_eof_without_closing_session_record(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import sys; "
                "data=sys.stdin.buffer.read(); "
                "print('EOF:'+str(len(data)), flush=True)"
            ),
        ],
    )
    session_id = opened["session_id"]
    cursor = opened["next_cursor"]

    closed_stdin = store.close_stdin(session_id)
    assert closed_stdin["stdin_closed"] is True
    assert closed_stdin["operation_seq"] == 1

    _, cursor, seen = _wait_for_text(store, session_id, cursor, "EOF:0")
    assert "EOF:0" in seen
    deadline = time.monotonic() + 3
    state = store.read(session_id, cursor=cursor)
    while time.monotonic() < deadline and state["status"] == "running":
        state = store.read(session_id, cursor=cursor, wait_seconds=0.1)
    assert state["status"] == "exited"
    assert state["stdin_closed"] is True

    closed = store.close(session_id)
    assert closed["status"] == "closed"


def test_terminate_keeps_session_readable_until_close(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=["-u", "-c", "import time; print('RUNNING', flush=True); time.sleep(30)"],
    )
    session_id = opened["session_id"]
    cursor = opened["next_cursor"]
    _, cursor, seen = _wait_for_text(store, session_id, cursor, "RUNNING")
    assert "RUNNING" in seen

    terminated = store.terminate(session_id)
    assert terminated["status"] == "exited"
    assert terminated["operation_seq"] == 1

    state = store.read(session_id, cursor=cursor)
    assert state["status"] == "exited"
    assert state["operation_seq"] == 1
    assert any(event["type"] == "terminate" for event in state["events"])

    closed = store.close(session_id)
    assert closed["status"] == "closed"
    assert closed["operation_seq"] == 2


def test_write_rejects_ambiguous_or_invalid_base64_through_dispatch(workspace):
    from runtime.session_runtime import process_session_request

    opened = process_session_request({
        "action": "open",
        "program": "python",
        "args": ["-u", "-c", "import time; time.sleep(30)"],
    })
    session_id = opened["session_id"]
    try:
        with pytest.raises(ValueError, match="exactly one"):
            process_session_request({
                "action": "write",
                "session_id": session_id,
                "input": "x",
                "input_base64": "eA==",
            })
        with pytest.raises(ValueError, match="valid base64"):
            process_session_request({
                "action": "write",
                "session_id": session_id,
                "input_base64": "%%%",
            })
    finally:
        # The global dispatcher owns SESSION_STORE, so close through the same path.
        process_session_request({"action": "close", "session_id": session_id})


def test_read_output_budget_is_explicit_and_recoverable(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            "import time; print('Z'*64, flush=True); time.sleep(30)",
        ],
    )
    session_id = opened["session_id"]
    execution_id = opened["execution_id"]
    try:
        deadline = time.monotonic() + 4
        limited = None
        while time.monotonic() < deadline:
            candidate = store.read(
                session_id,
                cursor=0,
                wait_seconds=0.25,
                max_output_bytes=8,
            )
            if any(event["type"] == "output" for event in candidate["events"]):
                limited = candidate
                break
        assert limited is not None
        assert limited["execution_id"] == execution_id
        assert limited["output_truncated"] is True
        assert limited["output_returned_bytes"] <= 8
        assert limited["output_omitted_bytes"] > 0
        assert limited["output_original_bytes"] > limited["output_returned_bytes"]
        output_events = [
            event for event in limited["events"] if event["type"] == "output"
        ]
        assert all(event["execution_id"] == execution_id for event in output_events)
        limited_text = "".join(event["data"]["content"] for event in output_events)
        assert len(limited_text.encode("utf-8")) <= 8
        assert any(event["data"].get("truncated") is True for event in output_events)

        recovered = store.read(
            session_id,
            cursor=0,
            wait_seconds=0,
            max_output_bytes=200,
        )
        recovered_text = "".join(
            event["data"]["content"]
            for event in recovered["events"]
            if event["type"] == "output"
        )
        assert "Z" * 64 in recovered_text
        assert recovered["output_truncated"] is False
    finally:
        store.close(session_id)


def test_interaction_lock_serializes_concurrent_writes(workspace):
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=[
            "-u",
            "-c",
            (
                "import sys,time; "
                "first=sys.stdin.readline(); second=sys.stdin.readline(); "
                "print('LINES:'+first.strip()+'|'+second.strip(), flush=True); "
                "time.sleep(30)"
            ),
        ],
    )
    session_id = opened["session_id"]
    execution_id = opened["execution_id"]
    barrier = Barrier(3)
    results = []
    errors = []

    def writer(text):
        try:
            barrier.wait(timeout=2)
            results.append(store.write(session_id, text))
        except Exception as exc:
            errors.append(exc)

    threads = [
        Thread(target=writer, args=("one\n",)),
        Thread(target=writer, args=("two\n",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    try:
        assert errors == []
        assert len(results) == 2
        assert sorted(result["operation_seq"] for result in results) == [1, 2]
        assert all(result["execution_id"] == execution_id for result in results)

        deadline = time.monotonic() + 4
        state = store.read(session_id, cursor=0, wait_seconds=0.25)
        while time.monotonic() < deadline:
            interactions = [
                event for event in state["events"] if event["type"] == "interaction"
            ]
            if len(interactions) >= 2:
                break
            state = store.read(session_id, cursor=0, wait_seconds=0.25)
        assert [event["data"]["operation_seq"] for event in interactions[:2]] == [1, 2]
        assert all(event["execution_id"] == execution_id for event in interactions[:2])
    finally:
        store.close(session_id)
