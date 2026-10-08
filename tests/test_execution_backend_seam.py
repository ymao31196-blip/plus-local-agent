from tooling import local_tools
from execution.execution_backend import runner_metadata
from runtime.session_runtime import InteractiveSessionStore
from runtime.task_store import TaskStore


def test_runner_metadata_is_stable_and_truthful():
    first = runner_metadata()
    second = runner_metadata()
    assert first == second
    assert len(first["runner_instance_id"]) == 32
    assert first["protocol_version"] == "1"
    assert first["process_isolation"] == "shared_http_process"
    assert first["process_id"] > 0


def test_oneshot_result_and_session_share_runner_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    one = local_tools.run_process(
        program="python",
        args=["-c", "print('runner-one')"],
        root="workspace",
    )
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        program="python",
        args=["-u", "-c", "import time; print('runner-session', flush=True); time.sleep(1)"],
        root="workspace",
    )
    try:
        assert one["runner"]["runner_instance_id"] == opened["runner"]["runner_instance_id"]
        assert one["runner"]["backend"] == opened["runner"]["backend"]
        assert opened["runner"]["process_isolation"] == "shared_http_process"
        read = store.read(opened["session_id"], wait_seconds=0.2)
        assert read["runner"] == opened["runner"]
    finally:
        store.close(opened["session_id"])


def test_task_execution_event_exposes_runner_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = TaskStore(max_workers=1)
    try:
        submitted = store.submit(
            {
                "tool": "run_process",
                "arguments": {
                    "program": "python",
                    "args": ["-c", "print('runner-task')"],
                },
            }
        )
        task_id = submitted["task_id"]
        record = store.get(task_id, cursor=0, wait_seconds=5)
        while record["status"] not in {"completed", "failed", "cancelled"}:
            record = store.get(task_id, cursor=0, wait_seconds=1)
        started = next(event for event in record["events"] if event["type"] == "execution_started")
        assert started["data"]["runner"]["backend"] == "production_runner_policy"
        completed = next(
            event for event in record["events"] if event["type"] == "execution_completed"
        )
        runner = completed["data"]["runner"]
        assert runner["runner_instance_id"] == runner_metadata()["runner_instance_id"]
        assert runner["process_isolation"] == "shared_http_process"
        assert completed["data"]["production"]["status"] == "fallback_pre_submit"
    finally:
        store.close()
