import json
from pathlib import Path
import sqlite3
import sys
import time

from execution import execution_runner_runtime as runner_runtime
from tooling import local_tools
from runtime.task_store import TaskStore


def _redirect_runner_state(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "runner-state"
    monkeypatch.setattr(runner_runtime, "STATE_DIR", state_dir)
    monkeypatch.setattr(runner_runtime, "STATE_PATH", state_dir / "runtime.json")
    monkeypatch.setattr(runner_runtime, "AUTH_PATH", state_dir / "runner.auth")


def _prepare_db(path: Path) -> None:
    store = TaskStore(db_path=path)
    assert store.initialize()["status"] == "ready"
    store.close()


def test_running_production_task_recovers_from_runner_ledger(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace.resolve()))
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)
    started = runner_runtime.start_execution_runner()
    request_id = "4" * 32
    task_id = "recover-task"
    db_path = tmp_path / "tasks.sqlite3"
    try:
        state = runner_runtime._read_state()
        client = runner_runtime._client_from_state(state)
        submitted = client.submit_resolved(
            execution_request_id=request_id,
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(0.35); print('RECOVERED_TASK_OK')",
            ],
            selected_root="workspace",
            cwd_relative=".",
            timeout=10,
        )
        assert submitted["status"] in {"accepted", "running", "completed"}

        _prepare_db(db_path)
        request = {
            "tool": "run_process",
            "arguments": {
                "program": sys.executable,
                "args": [
                    "-c",
                    "import time; time.sleep(0.35); print('RECOVERED_TASK_OK')",
                ],
                "root": "workspace",
            },
        }
        now = "2026-01-01T00:00:00+00:00"
        with sqlite3.connect(db_path) as db:
            db.execute(
                "INSERT INTO tasks(task_id,status,created_at,started_at,request) "
                "VALUES (?, 'running', ?, ?, ?)",
                (task_id, now, now, json.dumps(request)),
            )
            db.execute(
                "INSERT INTO events(task_id,kind,created_at,data) VALUES (?, ?, ?, ?)",
                (
                    task_id,
                    "execution_started",
                    now,
                    json.dumps(
                        {
                            "backend_preference": "production_runner",
                            "execution_request_id": request_id,
                            "runner": started["runner"],
                        }
                    ),
                ),
            )

        recovered = TaskStore(db_path=db_path)
        try:
            assert recovered.initialize()["status"] == "ready"
            deadline = time.monotonic() + 10
            record = recovered.get(task_id, cursor=0, wait_seconds=1)
            while record["status"] not in {"completed", "failed", "cancelled"}:
                assert time.monotonic() < deadline
                record = recovered.get(task_id, cursor=0, wait_seconds=1)
            assert record["status"] == "completed", record
            terminal = recovered.get(task_id)
            assert terminal["result"]["ok"] is True
            result = terminal["result"]["result"]
            assert result["stdout"] == "RECOVERED_TASK_OK\n"
            assert result["execution_request_id"] == request_id
            assert result["production"]["status"] == "recovered_after_http_restart"
            assert result["runner"]["runner_instance_id"] == started["runner"]["runner_instance_id"]
            events = record["events"]
            assert any(event["type"] == "task_recovery_queued" for event in events)
            assert any(event["type"] == "task_recovery_started" for event in events)
            assert any(event["type"] == "execution_recovered_after_restart" for event in events)
        finally:
            recovered.close()
    finally:
        stopped = runner_runtime.stop_execution_runner()
        if stopped.get("status") == "stop_pending":
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    terminal = runner_runtime.execution_runner_result(request_id)
                except Exception:
                    terminal = {"status": "error"}
                if terminal.get("status") != "running":
                    break
                time.sleep(0.05)
            runner_runtime.stop_execution_runner()


def test_nonproduction_running_task_keeps_no_replay_restart_policy(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    _prepare_db(db_path)
    task_id = "legacy-running-task"
    request = {
        "tool": "run_process",
        "arguments": {"program": "python", "args": ["-c", "print('x')"]},
    }
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO tasks(task_id,status,created_at,started_at,request) "
            "VALUES (?, 'running', ?, ?, ?)",
            (task_id, now, now, json.dumps(request)),
        )
        db.execute(
            "INSERT INTO events(task_id,kind,created_at,data) VALUES (?, ?, ?, ?)",
            (
                task_id,
                "execution_started",
                now,
                json.dumps(
                    {
                        "backend_preference": "default",
                        "execution_request_id": "5" * 32,
                    }
                ),
            ),
        )
    store = TaskStore(db_path=db_path)
    try:
        assert store.initialize()["status"] == "ready"
        record = store.get(task_id)
        assert record["status"] == "failed"
        assert record["error"]["type"] == "TaskInterruptedByRestart"
    finally:
        store.close()
