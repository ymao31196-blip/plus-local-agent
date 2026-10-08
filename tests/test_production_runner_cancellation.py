from pathlib import Path
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


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _wait_terminal_result(request_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = runner_runtime.execution_runner_result(request_id)
        if result.get("status") != "running":
            return result
        time.sleep(0.02)
    raise AssertionError("execution did not reach a terminal Runner state")


def test_runner_cancel_execution_stops_owned_child_tree(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace.resolve()))
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)
    runner_runtime.start_execution_runner()
    request_id = "6" * 32
    started_marker = workspace / "started.txt"
    done_marker = workspace / "done.txt"
    try:
        state = runner_runtime._read_state()
        client = runner_runtime._client_from_state(state)
        submitted = client.submit_resolved(
            execution_request_id=request_id,
            command=[
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import time; "
                    "Path('started.txt').write_text('started'); "
                    "time.sleep(30); Path('done.txt').write_text('done')"
                ),
            ],
            selected_root="workspace",
            cwd_relative=".",
            timeout=60,
        )
        assert submitted["status"] in {"accepted", "running"}
        _wait_for(started_marker)
        cancelled = runner_runtime.cancel_execution_runner_request(request_id)
        assert cancelled["status"] == "cancellation_requested"
        terminal = _wait_terminal_result(request_id)
        assert terminal["status"] == "cancelled"
        assert not done_marker.exists()
    finally:
        runner_runtime.stop_execution_runner()


def test_task_cancel_forwards_exact_production_request_id(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace.resolve()))
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)
    runner_runtime.start_execution_runner()
    store = TaskStore(db_path=tmp_path / "tasks.sqlite3")
    started_marker = workspace / "task-started.txt"
    done_marker = workspace / "task-done.txt"
    try:
        assert store.initialize()["status"] == "ready"
        submitted = store.submit(
            {
                "tool": "run_process",
                "arguments": {
                    "program": sys.executable,
                    "args": [
                        "-c",
                        (
                            "from pathlib import Path; import time; "
                            "Path('task-started.txt').write_text('started'); "
                            "time.sleep(30); Path('task-done.txt').write_text('done')"
                        ),
                    ],
                    "root": "workspace",
                    "timeout": 60,
                },
            }
        )
        task_id = submitted["task_id"]
        _wait_for(started_marker)
        cancelled = store.cancel(task_id)
        assert cancelled["cancel_requested"] is True

        deadline = time.monotonic() + 10
        record = store.get(task_id, cursor=0, wait_seconds=1)
        while record["status"] not in {"completed", "failed", "cancelled"}:
            assert time.monotonic() < deadline
            record = store.get(task_id, cursor=0, wait_seconds=1)
        assert record["status"] == "cancelled", record
        assert not done_marker.exists()
        forwarded = [
            event for event in record["events"]
            if event["type"] == "execution_cancellation_forwarded"
        ]
        assert len(forwarded) == 1
        request_id = forwarded[0]["data"]["execution_request_id"]
        terminal = _wait_terminal_result(request_id)
        assert terminal["status"] == "cancelled"
    finally:
        store.close()
        stopped = runner_runtime.stop_execution_runner()
        if stopped.get("status") == "stop_pending":
            time.sleep(0.2)
            runner_runtime.stop_execution_runner()
