import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import runtime_lifecycle
import runtime_lifecycle_broker
from capability_broker import CapabilityBroker
from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager
from runtime_lifecycle_capabilities import runtime_lifecycle_descriptors


def test_lifecycle_status_absent_broker(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_lifecycle, "BROKER_STATUS", tmp_path / "missing.json")
    monkeypatch.setattr(
        runtime_lifecycle,
        "_tcp_listening",
        lambda port: port == 8766,
    )

    status = runtime_lifecycle.lifecycle_status()

    assert status["http"]["pid"] == os.getpid()
    assert status["http"]["listening"] is True
    assert status["tunnel"]["listening"] is False
    assert status["broker"]["state"] == "absent"
    assert status["broker"]["ready"] is False


def test_lifecycle_status_fresh_but_dead_broker_is_not_ready(monkeypatch, tmp_path):
    path = tmp_path / "broker_status.json"
    path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 123,
                "updated_at": runtime_lifecycle._now(),
                "current_request_id": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_lifecycle, "BROKER_STATUS", path)
    monkeypatch.setattr(runtime_lifecycle, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(runtime_lifecycle, "_tcp_listening", lambda _port: True)

    status = runtime_lifecycle.lifecycle_status()

    assert status["broker"]["stale"] is False
    assert status["broker"]["alive"] is False
    assert status["broker"]["ready"] is False


def test_lifecycle_status_marks_stale_broker(monkeypatch, tmp_path):
    path = tmp_path / "broker_status.json"
    path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 123,
                "updated_at": "2000-01-01T00:00:00+00:00",
                "current_request_id": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_lifecycle, "BROKER_STATUS", path)
    monkeypatch.setattr(runtime_lifecycle, "_tcp_listening", lambda _port: True)

    status = runtime_lifecycle.lifecycle_status()

    assert status["broker"]["state"] == "running"
    assert status["broker"]["stale"] is True
    assert status["broker"]["ready"] is False


def test_restart_request_fails_closed_without_ready_broker(monkeypatch):
    monkeypatch.setattr(
        runtime_lifecycle,
        "_broker_snapshot",
        lambda: {"ready": False},
    )

    with pytest.raises(RuntimeError, match="Lifecycle Broker is not ready"):
        runtime_lifecycle.request_http_restart()


def test_restart_request_is_bounded_and_status_is_durable(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_lifecycle, "STATE_DIR", tmp_path)
    monkeypatch.setattr(
        runtime_lifecycle,
        "_broker_snapshot",
        lambda: {"ready": True},
    )
    monkeypatch.setattr(runtime_lifecycle.os, "getpid", lambda: 4242)

    accepted = runtime_lifecycle.request_http_restart()

    assert accepted["status"] == "accepted"
    assert accepted["state"] == "queued"
    assert accepted["expected_http_pid"] == 4242
    assert len(accepted["request_id"]) == 32

    request_path = tmp_path / f"{accepted['request_id']}.request.json"
    status_path = tmp_path / f"{accepted['request_id']}.status.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))

    assert set(request) == {
        "schema_version",
        "request_id",
        "kind",
        "created_at",
        "not_before",
        "expected_http_pid",
        "requested_by",
    }
    assert request["kind"] == "restart_http"
    assert request["requested_by"] == "runtime.restart_http"
    assert status["state"] == "queued"

    queried = runtime_lifecycle.restart_request_status(accepted["request_id"])
    assert queried["request_id"] == accepted["request_id"]
    assert queried["state"] == "queued"


@pytest.mark.parametrize("request_id", ["x", "../bad", "g" * 32, "a" * 31])
def test_restart_status_rejects_invalid_request_id(request_id):
    with pytest.raises(ValueError, match="request_id"):
        runtime_lifecycle.restart_request_status(request_id)


def _valid_broker_request():
    now = runtime_lifecycle_broker._now_dt()
    return {
        "schema_version": 1,
        "request_id": "a" * 32,
        "kind": "restart_http",
        "created_at": now.isoformat(),
        "not_before": now.isoformat(),
        "expected_http_pid": 1234,
        "requested_by": "runtime.restart_http",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "stop_http"),
        ("requested_by", "somewhere.else"),
        ("schema_version", 99),
        ("expected_http_pid", -1),
    ],
)
def test_broker_rejects_unreviewed_request_shapes(field, value):
    request = _valid_broker_request()
    request[field] = value

    with pytest.raises(ValueError):
        runtime_lifecycle_broker._validate_request(request)


def test_broker_rejects_unknown_fields():
    request = _valid_broker_request()
    request["command"] = "whoami"

    with pytest.raises(ValueError, match="Unknown lifecycle request fields"):
        runtime_lifecycle_broker._validate_request(request)


def test_broker_rejects_stale_request():
    request = _valid_broker_request()
    request["created_at"] = "2000-01-01T00:00:00+00:00"
    request["not_before"] = "2000-01-01T00:00:00+00:00"

    with pytest.raises(ValueError, match="age window"):
        runtime_lifecycle_broker._validate_request(request)


def test_execute_restart_uses_exact_script_and_expected_pid(monkeypatch, tmp_path):
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"x")
    script = tmp_path / "restart_pla.ps1"
    script.write_text("# fixture", encoding="utf-8")
    status_path = tmp_path / "status.json"
    captured = {}

    monkeypatch.setattr(runtime_lifecycle_broker, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runtime_lifecycle_broker, "RESTART_SCRIPT", script)
    monkeypatch.setattr(runtime_lifecycle_broker, "_powershell", lambda: powershell)

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "completed",
                    "old_pid": 1234,
                    "new_pid": 5678,
                    "tunnel_pid": 999,
                    "tunnel_listening": True,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(runtime_lifecycle_broker.subprocess, "run", fake_run)

    request = runtime_lifecycle_broker._validate_request(_valid_broker_request())
    runtime_lifecycle_broker._execute_restart(request, status_path)

    assert captured["argv"] == [
        str(powershell),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ExpectedPid",
        "1234",
        "-Json",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 45
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["result"]["new_pid"] == 5678
    assert status["result"]["tunnel_pid"] == 999


def test_restart_capability_requires_invoke_confirmation():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    broker = CapabilityBroker(registry, manager)
    registry.register_provider(
        "runtime",
        runtime_lifecycle_descriptors(),
        enabled=True,
    )
    broker.register_internal_handler(
        "runtime.restart_http",
        lambda _args: {"status": "should-not-run"},
    )

    with pytest.raises(PermissionError, match="confirmation='INVOKE'"):
        asyncio.run(broker.invoke("runtime.restart_http", {}))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell integration")
def test_restart_script_refuses_unexpected_http_pid():
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "restart_pla.ps1"
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ExpectedPid",
            "2147483000",
            "-Json",
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    assert "Expected PLA HTTP PID" in (completed.stdout + completed.stderr)
