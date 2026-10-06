import asyncio
import base64
import sys
import time

import pytest

import local_tools
import server
from session_runtime import InteractiveSessionStore


def _open_python(store: InteractiveSessionStore, args: list[str], tmp_path):
    local_tools.WORKSPACE = tmp_path.resolve()
    return store.open(
        program=sys.executable,
        args=args,
        cwd=".",
        root="workspace",
        terminal_mode="pipe",
    )


def _wait_for_text(store, session_id, needle, timeout=4.0):
    deadline = time.monotonic() + timeout
    cursor = 0
    seen = ""
    while time.monotonic() < deadline:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        for event in result["events"]:
            if event["type"] == "output":
                seen += event["data"]["content"]
        if needle in seen:
            return seen
    raise AssertionError(f"Did not observe {needle!r}; saw {seen!r}")


def test_confirmed_session_write_descriptor_requires_confirmation():
    detail = server.CAPABILITY_REGISTRY.describe("runtime.confirmed_session_write")
    assert detail["available"] is True
    assert detail["risk_level"] == "write_local"
    assert detail["requires_confirmation"] is True
    assert "session" in detail["tags"]
    assert "confirmation" in detail["tags"]


def test_confirmed_session_write_rejects_before_handler_without_invoke():
    with pytest.raises(PermissionError, match="requires confirmation='INVOKE'"):
        asyncio.run(
            server.capability_invoke(
                "runtime.confirmed_session_write",
                {"session_id": "missing-session", "input": "print('x')\n"},
            )
        )


def test_python_repl_write_is_blocked_until_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = InteractiveSessionStore()
    opened = store.open(
        program=sys.executable,
        args=["-u"],
        cwd=".",
        root="workspace",
        terminal_mode="pipe",
    )
    session_id = opened["session_id"]
    try:
        assert opened["permissions"]["semantic_action"] == "repl"
        blocked = store.write(session_id, "print('blocked-until-confirmed')\n")
        assert blocked["status"] == "blocked"
        assert blocked["reason"] == "session_input_confirmation_required"
        assert blocked["operation_seq"] == 0
        assert blocked["input_policy"]["requires_confirmation"] is True
        assert blocked["input_policy"]["policy_id"] == "interactive_session_input"
        assert blocked["input_policy"]["policy_version"] == "1"
        assert blocked["suggested_capabilities"][0]["name"] == (
            "runtime.confirmed_session_write"
        )

        confirmed = store.write_confirmed(
            session_id,
            "print('confirmed-session-write', flush=True)\n",
        )
        assert confirmed["confirmation_required"] is True
        assert confirmed["confirmation_supplied"] is True
        assert confirmed["input_policy"]["policy_id"] == "interactive_session_input"
        assert confirmed["operation_seq"] == 1

        store.close_stdin(session_id)
        output = _wait_for_text(store, session_id, "confirmed-session-write")
        assert "blocked-until-confirmed" not in output
    finally:
        try:
            store.close(session_id)
        except Exception:
            pass


def test_python_inline_script_stdin_remains_data_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = InteractiveSessionStore()
    opened = store.open(
        program=sys.executable,
        args=[
            "-u",
            "-c",
            "import sys; print('DATA:' + sys.stdin.readline().strip(), flush=True)",
        ],
        cwd=".",
        root="workspace",
        terminal_mode="pipe",
    )
    session_id = opened["session_id"]
    try:
        assert opened["permissions"]["semantic_action"] == "inline_code"
        written = store.write(session_id, "hello-data\n")
        assert written["status"] == "running"
        assert written["confirmation_required"] is False
        assert written["confirmation_supplied"] is False
        output = _wait_for_text(store, session_id, "DATA:hello-data")
        assert "DATA:hello-data" in output
    finally:
        try:
            store.close(session_id)
        except Exception:
            pass


def test_confirmed_session_write_cannot_bypass_data_session_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = InteractiveSessionStore()
    opened = store.open(
        program=sys.executable,
        args=["-u", "-c", "import sys; sys.stdin.readline()"],
        cwd=".",
        root="workspace",
        terminal_mode="pipe",
    )
    session_id = opened["session_id"]
    try:
        with pytest.raises(ValueError, match="only accepts session input that requires confirmation"):
            store.write_confirmed(session_id, "ordinary-data\n")
    finally:
        try:
            store.close(session_id)
        except Exception:
            pass


def test_repl_base64_write_is_governed_and_ctrl_c_is_control_input(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = InteractiveSessionStore()
    opened = store.open(
        program=sys.executable,
        args=["-u"],
        cwd=".",
        root="workspace",
        terminal_mode="pipe",
    )
    session_id = opened["session_id"]
    try:
        encoded = base64.b64encode(b"print('base64')\n").decode("ascii")
        blocked = store.write_base64(session_id, encoded)
        assert blocked["status"] == "blocked"
        assert blocked["input_policy"]["requires_confirmation"] is True

        control = store.input_policy(session_id, b"\x03")
        assert control["requires_confirmation"] is False
        assert control["effect_class"] == "control_input"
    finally:
        try:
            store.close(session_id)
        except Exception:
            pass
