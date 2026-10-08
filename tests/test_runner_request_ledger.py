from pathlib import Path
import time

from execution import execution_runner_runtime as runtime
from execution.runner_transport import RunnerSpikeClient, read_auth_file


def _redirect_state(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "runner-state"
    monkeypatch.setattr(runtime, "STATE_DIR", state_dir)
    monkeypatch.setattr(runtime, "STATE_PATH", state_dir / "runtime.json")
    monkeypatch.setattr(runtime, "AUTH_PATH", state_dir / "runner.auth")
    monkeypatch.setenv("AGENT_WORKSPACE", str((tmp_path / "workspace").resolve()))
    (tmp_path / "workspace").mkdir()


def _client() -> RunnerSpikeClient:
    state = runtime._read_state()
    return RunnerSpikeClient(state["address"], read_auth_file(runtime.AUTH_PATH))


def _wait_result(client: RunnerSpikeClient, request_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.execution_result(request_id)
        if result.get("status") != "running":
            return result
        time.sleep(0.03)
    raise AssertionError("runner request did not reach terminal state")


def test_execution_request_id_deduplicates_and_replays_without_rerun(tmp_path, monkeypatch):
    _redirect_state(tmp_path, monkeypatch)
    runtime.start_execution_runner()
    request_id = "a" * 32
    script = (
        "from pathlib import Path; "
        "p=Path('count.txt'); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8'); "
        "print('LEDGER_OK')"
    )
    client = _client()
    try:
        first = client.submit_resolved(
            execution_request_id=request_id,
            command=["python", "-c", script],
            selected_root="workspace",
            cwd_relative=".",
            timeout=10,
        )
        assert first["status"] in {"accepted", "running", "completed"}
        completed = _wait_result(client, request_id)
        assert completed["status"] == "completed"
        assert completed["execution_request_id"] == request_id
        assert "LEDGER_OK" in completed["stdout"]["content"]

        replay = client.submit_resolved(
            execution_request_id=request_id,
            command=["python", "-c", script],
            selected_root="workspace",
            cwd_relative=".",
            timeout=10,
        )
        assert replay["status"] == "completed"
        assert replay["execution_request_id"] == request_id
        assert (tmp_path / "workspace" / "count.txt").read_text(encoding="utf-8") == "x"

        mismatch = client.submit_resolved(
            execution_request_id=request_id,
            command=["python", "-c", "print('DIFFERENT')"],
            selected_root="workspace",
            cwd_relative=".",
            timeout=10,
        )
        assert mismatch["status"] == "error"
        assert "already used for a different request" in mismatch["message"]
    finally:
        runtime.stop_execution_runner()


def test_execution_result_survives_client_disconnect_and_busy_shutdown_is_rejected(tmp_path, monkeypatch):
    _redirect_state(tmp_path, monkeypatch)
    runtime.start_execution_runner()
    request_id = "b" * 32
    client1 = _client()
    submitted = client1.submit_resolved(
        execution_request_id=request_id,
        command=["python", "-c", "import time; time.sleep(0.5); print('RECONNECTED_OK')"],
        selected_root="workspace",
        cwd_relative=".",
        timeout=10,
    )
    assert submitted["status"] == "accepted"

    # A fresh connection represents a replacement Control Plane after disconnect/restart.
    client2 = _client()
    busy = client2.shutdown()
    assert busy["status"] == "error"
    assert busy["error_type"] == "RunnerBusy"
    assert busy["workload"]["active_executions"] == 1

    completed = _wait_result(client2, request_id)
    assert completed["status"] == "completed"
    assert completed["execution_request_id"] == request_id
    assert "RECONNECTED_OK" in completed["stdout"]["content"]
    ping = client2.ping()
    assert ping["workload"]["active_executions"] == 0
    assert ping["workload"]["retained_executions"] >= 1

    stopped = client2.shutdown()
    assert stopped["status"] == "stopping"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if runtime._read_state().get("status") == "stopped":
            break
        time.sleep(0.03)
    assert runtime._read_state().get("status") == "stopped"
