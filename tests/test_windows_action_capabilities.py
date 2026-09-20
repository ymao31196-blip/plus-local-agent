import asyncio
import json
import subprocess

import pytest

import server
import windows_action_capabilities as actions
from transaction_action_envelope import (
    complete_external_capability_in_transaction,
    invoke_capability_in_transaction,
)
from transaction_runtime import ActionTransactionStore


def _safe_service(name: str = "Spooler") -> dict:
    return {
        "Name": name,
        "DisplayName": "Print Spooler",
        "Status": "Running",
        "StartType": "Automatic",
        "CanStop": True,
        "DependentServices": [],
        "RequiredServices": [],
    }


def test_windows_action_descriptors_enforce_governance():
    descriptors = {item.id: item for item in actions.windows_action_descriptors()}
    assert set(descriptors) == {
        "windows.action_status",
        "windows.flush_dns_cache",
        "windows.service_control_preflight",
        "windows.service_control",
        "windows.service_control_status",
        "windows.elevation_broker_restart",
    }

    status = descriptors["windows.action_status"]
    assert status.risk_level == "read"
    assert status.requires_confirmation is False
    assert status.requires_transaction is False

    dns = descriptors["windows.flush_dns_cache"]
    assert dns.risk_level == "privileged"
    assert dns.requires_confirmation is True
    assert dns.requires_transaction is False

    service = descriptors["windows.service_control"]
    assert service.risk_level == "destructive"
    assert service.requires_confirmation is True
    assert service.requires_transaction is True

    restart = descriptors["windows.elevation_broker_restart"]
    assert restart.risk_level == "privileged"
    assert restart.requires_confirmation is True
    assert restart.requires_transaction is False


def test_broker_rejects_elevation_broker_restart_without_confirmation():
    with pytest.raises(PermissionError, match="requires confirmation"):
        asyncio.run(
            server.CAPABILITY_BROKER.invoke(
                "windows.elevation_broker_restart",
                {},
            )
        )


def test_service_control_preflight_reports_ready_and_blockers(
    tmp_path, monkeypatch,
):
    config = tmp_path / "windows_actions.local.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["restart"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)
    monkeypatch.setattr(
        actions,
        "_query_service",
        lambda _name: {
            "Name": "Spooler",
            "DisplayName": "Print Spooler",
            "Status": "Running",
            "StartType": "Automatic",
            "CanStop": True,
            "DependentServices": [],
            "RequiredServices": ["RPCSS"],
        },
    )
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "state": "running",
            "pid": 321,
            "current_launch_id": None,
        },
    )

    ready = actions.service_control_preflight("spooler", "restart")
    assert ready["status"] == "ready"
    assert ready["policy_authorized"] is True
    assert ready["allowed_operations"] == ["restart"]
    assert ready["blockers"] == []
    assert ready["requires_confirmation"] is True
    assert ready["requires_transaction"] is True
    assert ready["requires_uac"] is True

    blocked = actions.service_control_preflight("Spooler", "stop")
    assert blocked["status"] == "blocked"
    assert blocked["policy_authorized"] is False
    assert "operation_not_authorized" in blocked["blockers"]


@pytest.mark.parametrize(
    "operation,service,expected_blocker",
    [
        (
            "start",
            {
                "Status": "Stopped",
                "StartType": "Disabled",
                "CanStop": False,
                "DependentServices": [],
            },
            "service_disabled",
        ),
        (
            "restart",
            {
                "Status": "Running",
                "StartType": "Automatic",
                "CanStop": False,
                "DependentServices": [],
            },
            "service_cannot_stop",
        ),
        (
            "restart",
            {
                "Status": "Running",
                "StartType": "Automatic",
                "CanStop": True,
                "DependentServices": [
                    {
                        "Name": "ChildSvc",
                        "Status": "Running",
                        "StartType": "Manual",
                    }
                ],
            },
            "active_dependents_running",
        ),
        (
            "restart",
            {
                "Status": "StartPending",
                "StartType": "Automatic",
                "CanStop": False,
                "DependentServices": [],
            },
            "service_not_stable",
        ),
    ],
)
def test_service_execution_blockers(operation, service, expected_blocker):
    assert expected_blocker in actions._service_execution_blockers(
        service, operation
    )


def test_service_control_preflight_reports_missing_and_busy(monkeypatch):
    monkeypatch.setattr(
        actions,
        "load_windows_action_policy",
        lambda: {"version": 1, "configured": False, "services": {}},
    )
    monkeypatch.setattr(actions, "_query_service", lambda _name: None)
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "state": "running",
            "pid": 321,
            "current_launch_id": "f" * 32,
        },
    )

    result = actions.service_control_preflight("MissingSvc", "restart")
    assert result["status"] == "blocked"
    assert result["service"] is None
    assert result["blockers"] == [
        "service_not_found",
        "operation_not_authorized",
        "elevation_broker_busy",
    ]


def test_broker_rejects_dns_flush_without_confirmation():
    with pytest.raises(PermissionError, match="requires confirmation"):
        asyncio.run(
            server.CAPABILITY_BROKER.invoke(
                "windows.flush_dns_cache",
                {},
            )
        )


def test_broker_rejects_service_control_without_transaction():
    with pytest.raises(PermissionError, match="requires a transaction context"):
        asyncio.run(
            server.CAPABILITY_BROKER.invoke(
                "windows.service_control",
                {"service_name": "Spooler", "operation": "restart"},
                confirmation="INVOKE",
            )
        )


def test_policy_absent_fails_closed_for_service_control(tmp_path, monkeypatch):
    config = tmp_path / "windows_actions.local.json"
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)

    status = actions.windows_action_status()
    assert status["policy_configured"] is False
    assert status["service_rules"] == []

    with pytest.raises(PermissionError, match="not present in the local action allowlist"):
        actions.service_control("Spooler", "restart")


def test_policy_rejects_unknown_fields_and_invalid_operations(tmp_path, monkeypatch):
    config = tmp_path / "windows_actions.local.json"
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)

    config.write_text(
        json.dumps({
            "version": 1,
            "services": {},
            "unexpected": True,
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported fields"):
        actions.windows_action_status()

    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["restart", "restart"]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate Windows service operation"):
        actions.windows_action_status()

    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["delete"]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unsupported Windows service operation"):
        actions.windows_action_status()


def test_allowlisted_service_queues_elevation_request(
    tmp_path, monkeypatch,
):
    config = tmp_path / "windows_actions.local.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["restart"]},
        }),
        encoding="utf-8",
    )
    state_dir = tmp_path / "elevation"
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)
    monkeypatch.setattr(actions, "ELEVATION_STATE_DIR", state_dir)
    monkeypatch.setattr(actions, "_query_service", lambda _name: _safe_service())
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {"status": "ready", "pid": 4321, "current_launch_id": None},
    )

    result = actions.service_control("spooler", "restart")

    assert result["status"] == "external_pending"
    assert result["elevation_requested"] is True
    assert result["service_name"] == "Spooler"
    assert result["operation"] == "restart"
    request = json.loads(
        (state_dir / f"{result['launch_id']}.request.json").read_text(encoding="utf-8")
    )
    assert request == {
        "kind": "service_control",
        "launch_id": result["launch_id"],
        "created_at": request["created_at"],
        "service_name": "Spooler",
        "operation": "restart",
        "timeout_seconds": 60,
    }
    assert "executable" not in request
    assert "args" not in request

    status = actions.windows_action_status()
    assert status["service_rules"] == [
        {"service_name": "Spooler", "operations": ["restart"]}
    ]

    with pytest.raises(PermissionError, match="operation is not allowed"):
        actions.service_control("Spooler", "stop")


def test_broker_allows_allowlisted_service_inside_confirmed_transaction(
    tmp_path, monkeypatch,
):
    config = tmp_path / "windows_actions.local.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["restart"]},
        }),
        encoding="utf-8",
    )
    state_dir = tmp_path / "elevation"
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)
    monkeypatch.setattr(actions, "ELEVATION_STATE_DIR", state_dir)
    monkeypatch.setattr(actions, "_query_service", lambda _name: _safe_service())
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {"status": "ready", "pid": 4321, "current_launch_id": None},
    )

    result = asyncio.run(
        server.CAPABILITY_BROKER.invoke(
            "windows.service_control",
            {"service_name": "Spooler", "operation": "restart"},
            confirmation="INVOKE",
            transaction_context=True,
            transaction_id="test-windows-action",
        )
    )
    assert result["is_error"] is False
    payload = result["data"]
    assert payload["status"] == "external_pending"
    assert payload["service_name"] == "Spooler"
    assert payload["operation"] == "restart"


def test_service_control_status_reads_broker_result(tmp_path, monkeypatch):
    launch_id = "a" * 32
    state_dir = tmp_path / "elevation"
    state_dir.mkdir()
    monkeypatch.setattr(actions, "ELEVATION_STATE_DIR", state_dir)
    (state_dir / f"{launch_id}.request.json").write_text(
        json.dumps({
            "kind": "service_control",
            "launch_id": launch_id,
            "service_name": "Spooler",
            "operation": "restart",
        }),
        encoding="utf-8",
    )
    (state_dir / f"{launch_id}.status.json").write_text(
        json.dumps({
            "launch_id": launch_id,
            "state": "completed",
            "returncode": 0,
            "win32_error": None,
            "updated_at": "2026-09-20T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    (state_dir / f"{launch_id}.result.json").write_text(
        json.dumps({
            "ServiceName": "Spooler",
            "Operation": "restart",
            "BeforeStatus": "Running",
            "AfterStatus": "Running",
        }),
        encoding="utf-8",
    )

    result = actions.service_control_status(launch_id)

    assert result["status"] == "completed"
    assert result["result"]["ServiceName"] == "Spooler"
    assert result["result"]["Operation"] == "restart"


def test_service_control_transaction_stays_open_until_external_verification(
    tmp_path, monkeypatch,
):
    config = tmp_path / "windows_actions.local.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "services": {"Spooler": ["restart"]},
        }),
        encoding="utf-8",
    )
    state_dir = tmp_path / "elevation"
    monkeypatch.setattr(actions, "WINDOWS_ACTIONS_CONFIG", config)
    monkeypatch.setattr(actions, "ELEVATION_STATE_DIR", state_dir)
    monkeypatch.setattr(actions, "_query_service", lambda _name: _safe_service())
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "state": "running",
            "pid": 4321,
            "current_launch_id": None,
        },
    )

    store = ActionTransactionStore(tmp_path / "transactions.sqlite3")
    try:
        record = store.create(
            "Restart one allowlisted service",
            [{
                "step_id": "service",
                "title": "Restart Spooler",
                "kind": "action",
            }],
        )
        pending = asyncio.run(
            invoke_capability_in_transaction(
                store,
                server.CAPABILITY_BROKER,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="service",
                capability_id="windows.service_control",
                arguments={
                    "service_name": "Spooler",
                    "operation": "restart",
                },
                confirmation="INVOKE",
            )
        )

        assert pending["status"] == "external_interaction_pending"
        assert pending["transaction"]["steps"][0]["state"] == "running"
        launch_id = pending["result"]["data"]["launch_id"]

        with pytest.raises(ValueError, match="Cannot finalize while a step is running"):
            store.finalize(
                record["transaction_id"],
                pending["transaction"]["revision"],
                "commit",
                "Too early",
            )

        status_path = state_dir / f"{launch_id}.status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status.update({
            "state": "completed",
            "returncode": 0,
            "updated_at": "2026-09-20T00:00:00+00:00",
        })
        status_path.write_text(json.dumps(status), encoding="utf-8")
        (state_dir / f"{launch_id}.result.json").write_text(
            json.dumps({
                "ServiceName": "Spooler",
                "Operation": "restart",
                "BeforeStatus": "Running",
                "AfterStatus": "Running",
            }),
            encoding="utf-8",
        )

        verification = actions.service_control_status(launch_id)
        assert verification["status"] == "completed"

        completed = asyncio.run(
            complete_external_capability_in_transaction(
                store,
                server.CAPABILITY_BROKER,
                transaction_id=record["transaction_id"],
                expected_revision=pending["transaction"]["revision"],
                step_id="service",
            )
        )
        assert completed["status"] == "completed"
        checked = completed["transaction"]
        committed = store.finalize(
            record["transaction_id"],
            checked["revision"],
            "commit",
            "External service action verified",
        )
        assert committed["status"] == "committed"
        assert committed["steps"][0]["state"] == "succeeded"
    finally:
        store.close()


def test_elevation_broker_restart_refuses_busy_broker(monkeypatch):
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "pid": 123,
            "current_launch_id": "a" * 32,
        },
    )

    with pytest.raises(RuntimeError, match="busy"):
        actions.restart_elevation_broker()


def test_elevation_broker_restart_refuses_identity_mismatch(monkeypatch):
    monkeypatch.setattr(
        actions,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "pid": 123,
            "current_launch_id": None,
        },
    )
    monkeypatch.setattr(
        actions,
        "_process_command_line",
        lambda _pid: "python.exe unrelated.py",
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        actions.restart_elevation_broker()


def test_elevation_broker_restart_uses_verified_pid_and_fixed_entrypoint(
    tmp_path, monkeypatch,
):
    system_root = tmp_path / "Windows"
    taskkill = system_root / "System32" / "taskkill.exe"
    taskkill.parent.mkdir(parents=True)
    taskkill.write_bytes(b"stub")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"stub")
    start_script = tmp_path / "start_elevation_broker.ps1"
    start_script.write_text("stub", encoding="utf-8")

    statuses = iter([
        {
            "status": "ready",
            "state": "running",
            "pid": 123,
            "current_launch_id": None,
        },
        {
            "status": "ready",
            "state": "running",
            "pid": 456,
            "current_launch_id": None,
        },
    ])
    monkeypatch.setattr(actions, "_elevation_broker_status", lambda: next(statuses))
    monkeypatch.setattr(
        actions,
        "_process_command_line",
        lambda _pid: f'python "{actions.PLA_ROOT}\\interactive_elevation_broker.py"',
    )
    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setattr(actions, "ELEVATION_BROKER_START_SCRIPT", start_script)
    monkeypatch.setattr(actions, "_powershell_executable", lambda: powershell)

    captured = {}

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["stop_argv"] = argv
        captured["stop_kwargs"] = kwargs
        return Completed()

    class FakeProcess:
        pid = 777

        def terminate(self):
            captured["terminated"] = True

    def fake_popen(argv, **kwargs):
        captured["start_argv"] = argv
        captured["start_kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)

    result = actions.restart_elevation_broker()

    assert result["status"] == "completed"
    assert result["old_pid"] == 123
    assert result["new_pid"] == 456
    assert captured["stop_argv"] == [
        str(taskkill), "/PID", "123", "/T", "/F",
    ]
    assert captured["stop_kwargs"]["shell"] is False
    assert captured["start_argv"][-2:] == ["-File", str(start_script)]
    assert captured["start_kwargs"]["shell"] is False


def test_fixed_action_uses_shell_false_and_payload_env(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = b'{"Action":"flush_dns_cache","Status":"completed"}'
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(actions, "_powershell_executable", lambda: actions.Path("powershell.exe"))
    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    result = actions.flush_dns_cache()

    assert result["status"] == "completed"
    assert result["data"]["Action"] == "flush_dns_cache"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert "PLUS_LOCAL_AGENT_WINDOWS_ACTION" in captured["kwargs"]["env"]
    assert "-EncodedCommand" in captured["argv"]
