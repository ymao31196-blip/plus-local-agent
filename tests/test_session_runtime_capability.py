import asyncio
import time

from tooling import local_tools
import server


def _invoke(arguments):
    return asyncio.run(
        server.capability_invoke("runtime.process_session", arguments)
    )


def test_runtime_process_session_is_dynamic_capability():
    detail = server.CAPABILITY_REGISTRY.describe("runtime.process_session")
    assert detail["available"] is True
    assert detail["provider_id"] == "runtime"
    assert detail["risk_level"] == "write_local"
    assert detail["requires_confirmation"] is False
    assert "process" in detail["tags"]
    assert detail["input_schema"]["properties"]["action"]["enum"] == [
        "open",
        "write",
        "close_stdin",
        "read",
        "resize",
        "terminate",
        "close",
    ]
    assert "input_base64" in detail["input_schema"]["properties"]
    assert "max_output_bytes" in detail["input_schema"]["properties"]
    assert detail["input_schema"]["properties"]["terminal_mode"]["enum"] == [
        "pipe", "conpty"
    ]


def test_runtime_process_session_invokes_through_capability_broker(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())

    opened = _invoke({
        "action": "open",
        "program": "python",
        "args": [
            "-u",
            "-c",
            (
                "import sys,time; "
                "print('BROKER_READY', flush=True); "
                "line=sys.stdin.readline(); "
                "print('BROKER_ECHO:'+line.strip(), flush=True); "
                "time.sleep(30)"
            ),
        ],
    })
    assert opened["status"] == "completed"
    assert opened["data"]["terminal_mode"] == "pipe"
    session_id = opened["data"]["session_id"]
    cursor = opened["data"]["next_cursor"]

    try:
        deadline = time.monotonic() + 4
        seen = ""
        while time.monotonic() < deadline and "BROKER_READY" not in seen:
            read = _invoke({
                "action": "read",
                "session_id": session_id,
                "cursor": cursor,
                "wait_seconds": 0.25,
            })
            cursor = read["data"]["next_cursor"]
            seen += "".join(
                event["data"]["content"]
                for event in read["data"]["events"]
                if event["type"] == "output"
            )
        assert "BROKER_READY" in seen

        written = _invoke({
            "action": "write",
            "session_id": session_id,
            "input": "hello-broker\n",
        })
        assert written["data"]["characters_written"] == 13

        deadline = time.monotonic() + 4
        seen = ""
        while time.monotonic() < deadline and "BROKER_ECHO:hello-broker" not in seen:
            read = _invoke({
                "action": "read",
                "session_id": session_id,
                "cursor": cursor,
                "wait_seconds": 0.25,
            })
            cursor = read["data"]["next_cursor"]
            seen += "".join(
                event["data"]["content"]
                for event in read["data"]["events"]
                if event["type"] == "output"
            )
        assert "BROKER_ECHO:hello-broker" in seen
    finally:
        closed = _invoke({
            "action": "close",
            "session_id": session_id,
        })
        assert closed["data"]["status"] == "closed"
