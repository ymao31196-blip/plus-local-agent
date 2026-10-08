import asyncio
from pathlib import Path

import pytest

from execution import execution_runner_capabilities as runner_caps
from execution import execution_runner_runtime as runtime
import server


def _redirect_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "STATE_DIR", tmp_path)
    monkeypatch.setattr(runtime, "STATE_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(runtime, "AUTH_PATH", tmp_path / "runner.auth")


def test_candidate_runner_lifecycle_and_probe_survive_supervisor_state_reload(tmp_path, monkeypatch):
    _redirect_state(tmp_path, monkeypatch)
    assert runtime.execution_runner_status()["state"] == "stopped"

    started = runtime.start_execution_runner()
    runner_id = started["runner"]["runner_instance_id"]
    assert started["status"] == "started"
    assert started["runner"]["process_isolation"] == "separate_process"
    assert started["default_backend"] is True
    try:
        # Status is reconstructed from durable state + auth, not an in-memory Popen handle.
        status = runtime.execution_runner_status()
        assert status["running"] is True
        assert status["runner"]["runner_instance_id"] == runner_id

        probe = runtime.execution_runner_probe()
        assert probe["status"] == "completed"
        assert probe["returncode"] == 0
        assert "PLA_EXECUTION_RUNNER_PROBE_OK" in probe["stdout"]["content"]
        assert probe["runner"]["runner_instance_id"] == runner_id
        assert probe["runner"]["scope"] == "opt_in_text_oneshot"
        assert isinstance(probe["child_pid"], int)
        assert probe["child_pid"] > 0
        assert probe["child_pid"] != probe["runner"]["process_id"]
    finally:
        stopped = runtime.stop_execution_runner()
        assert stopped["status"] in {"stopped", "already_stopped"}

    after = runtime.execution_runner_status()
    assert after["running"] is False
    assert after["default_backend"] is True


def test_execution_runner_capabilities_have_explicit_lifecycle_policy():
    status = server.CAPABILITY_REGISTRY.describe("runtime.execution_runner_status")
    probe = server.CAPABILITY_REGISTRY.describe("runtime.execution_runner_probe")
    start = server.CAPABILITY_REGISTRY.describe("runtime.execution_runner_start")
    stop = server.CAPABILITY_REGISTRY.describe("runtime.execution_runner_stop")
    assert status["risk_level"] == "read"
    assert status["requires_confirmation"] is False
    assert probe["risk_level"] == "read"
    assert probe["requires_confirmation"] is False
    for item in (start, stop):
        assert item["risk_level"] == "privileged"
        assert item["requires_confirmation"] is True
        assert "production" in item["tags"]


def test_execution_runner_start_is_rejected_before_handler_without_invoke(monkeypatch):
    called = False

    def fake_start():
        nonlocal called
        called = True
        return {"status": "started"}

    monkeypatch.setattr(runner_caps, "start_execution_runner", fake_start)
    with pytest.raises(PermissionError, match="requires confirmation='INVOKE'"):
        asyncio.run(server.capability_invoke("runtime.execution_runner_start", {}))
    assert called is False

    result = asyncio.run(
        server.capability_invoke(
            "runtime.execution_runner_start",
            {},
            confirmation="INVOKE",
        )
    )
    assert result["status"] == "completed"
    assert result["data"]["status"] == "started"
    assert called is True


def test_runner_status_exposes_verified_production_health(tmp_path, monkeypatch):
    _redirect_state(tmp_path, monkeypatch)
    started = runtime.start_execution_runner()
    try:
        status = runtime.execution_runner_status()
        assert status["state"] == "running"
        assert status["production_ready"] is True
        assert status["recovery_required"] is False
        assert status["health"]["process_identity_match"] is True
        assert status["health"]["protocol_compatible"] is True
        assert status["health"]["ping_identity_match"] is True
        assert status["health"]["workload"] == {
            "active_executions": 0,
            "retained_executions": 0,
            "completed_executions": 0,
        }
        runner = status["runner"]
        assert isinstance(runner["process_creation_time_100ns"], int)
        assert runner["process_creation_time_100ns"] > 0
        assert runner["process_image_path"]
        assert runner["runner_instance_id"] == started["runner"]["runner_instance_id"]
    finally:
        runtime.stop_execution_runner()


def test_stale_dead_state_can_be_recovered_by_start(tmp_path, monkeypatch):
    import json

    _redirect_state(tmp_path, monkeypatch)
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime.STATE_PATH.write_text(
        json.dumps({
            "status": "ready",
            "runner_instance_id": "dead-runner",
            "backend": "named_pipe_candidate",
            "protocol_version": "2",
            "process_id": 2147483000,
            "process_creation_time_100ns": 1,
            "process_image_path": "C:/definitely/missing/python.exe",
            "process_isolation": "separate_process",
            "scope": "opt_in_text_oneshot",
            "address": r"\\.\pipe\missing-runner",
        }),
        encoding="utf-8",
    )
    status = runtime.execution_runner_status()
    assert status["state"] == "stale_dead_or_reused"
    assert status["recovery_required"] is False

    started = runtime.start_execution_runner()
    try:
        assert started["status"] == "started"
        assert started["runner"]["runner_instance_id"] != "dead-runner"
        assert runtime.execution_runner_status()["production_ready"] is True
    finally:
        runtime.stop_execution_runner()


def test_stale_live_state_fails_closed_instead_of_overwriting_ownership(tmp_path, monkeypatch):
    state = {
        "status": "ready",
        "runner_instance_id": "live-but-unreachable",
        "backend": "named_pipe_candidate",
        "protocol_version": runtime.SPIKE_PROTOCOL_VERSION,
        "process_id": 4242,
        "process_creation_time_100ns": 99,
        "process_image_path": "C:/Python/python.exe",
        "process_isolation": "separate_process",
        "scope": "opt_in_text_oneshot",
        "address": r"\\.\pipe\unreachable-runner",
    }
    _redirect_state(tmp_path, monkeypatch)
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime.STATE_PATH.write_text(__import__("json").dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "query_process_identity",
        lambda pid: {
            "process_id": 4242,
            "creation_time_100ns": 99,
            "image_path": "C:/Python/python.exe",
        },
    )

    class BrokenClient:
        def ping(self):
            raise ConnectionError("unreachable")

    monkeypatch.setattr(runtime, "_client_from_state", lambda value: BrokenClient())
    status = runtime.execution_runner_status()
    assert status["state"] == "stale_live"
    assert status["recovery_required"] is True
    with pytest.raises(RuntimeError, match="refusing to overwrite ownership state"):
        runtime.start_execution_runner()
    assert runtime.STATE_PATH.exists()


def test_runner_mutex_rejects_second_owner_on_windows(tmp_path):
    import os
    from execution import execution_runner_service as service
    if os.name != "nt":
        pytest.skip("Windows named mutex")
    state = tmp_path / "owned" / "runtime.json"
    alternate = tmp_path / "isolated" / "runtime.json"
    assert service._runner_mutex_name(state) == service._runner_mutex_name(state)
    assert service._runner_mutex_name(state) != service._runner_mutex_name(alternate)
    first = service._acquire_runner_mutex(state)
    try:
        with pytest.raises(RuntimeError, match="already owns the runtime mutex"):
            service._acquire_runner_mutex(state)
        second = service._acquire_runner_mutex(alternate)
        service._release_runner_mutex(second)
    finally:
        service._release_runner_mutex(first)


def test_isolated_runner_does_not_replace_production_owner(tmp_path, monkeypatch):
    """A temporary test Runner must not take over the configured production owner."""
    before = runtime.execution_runner_status()
    production_id = (before.get("runner") or {}).get("runner_instance_id")
    with monkeypatch.context() as isolated:
        _redirect_state(tmp_path / "isolated-runner", isolated)
        started = runtime.start_execution_runner()
        try:
            assert started["status"] == "started"
            assert started["runner"]["runner_instance_id"] != production_id
            assert runtime.execution_runner_status()["production_ready"] is True
        finally:
            assert runtime.stop_execution_runner()["status"] in {"stopped", "already_stopped"}

    after = runtime.execution_runner_status()
    assert after["running"] == before["running"]
    if before["running"]:
        assert after["production_ready"] is True
        assert after["runner"]["runner_instance_id"] == production_id
