import asyncio
from types import SimpleNamespace

from tooling import local_tools
import server
from runtime.task_store import TaskStore


def test_task_execution_events_include_task_id_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = TaskStore(max_workers=1)
    try:
        submitted = store.submit(
            {
                "tool": "run_process",
                "arguments": {
                    "program": "python",
                    "args": ["-c", "print('trace-task')"],
                },
            }
        )
        task_id = submitted["task_id"]
        record = store.get(task_id, cursor=0, wait_seconds=5)
        while record["status"] not in {"completed", "failed", "cancelled"}:
            record = store.get(task_id, cursor=0, wait_seconds=1)

        started = next(
            event for event in record["events"] if event["type"] == "execution_started"
        )
        completed = next(
            event for event in record["events"] if event["type"] == "execution_completed"
        )
        assert started["data"]["trace"]["task_id"] == task_id
        assert completed["data"]["trace"]["task_id"] == task_id
        assert "correlation_id" not in started["data"]["trace"]
    finally:
        store.close()


def test_confirmed_capability_result_includes_broker_correlation(monkeypatch, tmp_path):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    def fake_run(_request):
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(local_tools, "run_oneshot", fake_run)
    result = asyncio.run(
        server.capability_invoke(
            "runtime.external_process",
            {
                "program": "gh",
                "args": ["issue", "create", "--title", "trace-test"],
                "root": "workspace",
            },
            confirmation="INVOKE",
        )
    )

    trace = result["data"]["execution_trace"]
    assert len(trace["correlation_id"]) == 32
    assert trace["capability_id"] == "runtime.external_process"
    assert trace["causation_id"] is None or isinstance(trace["causation_id"], str)
    assert trace["transaction_id"] is None


def test_session_origin_and_interaction_have_distinct_capability_correlations(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    opened = asyncio.run(
        server.capability_invoke(
            "runtime.process_session",
            {
                "action": "open",
                "program": "python",
                "args": [
                    "-u",
                    "-c",
                    "import sys,time; print('READY', flush=True); "
                    "print('ECHO:'+sys.stdin.readline().strip(), flush=True); time.sleep(0.2)",
                ],
                "root": "workspace",
            },
        )
    )["data"]
    session_id = opened["session_id"]
    origin = opened["origin_trace"]
    try:
        assert origin["capability_id"] == "runtime.process_session"
        assert len(origin["correlation_id"]) == 32

        written = asyncio.run(
            server.capability_invoke(
                "runtime.process_session",
                {
                    "action": "write",
                    "session_id": session_id,
                    "input": "hello-trace\n",
                },
            )
        )["data"]
        assert written["operation_seq"] == 1

        read = asyncio.run(
            server.capability_invoke(
                "runtime.process_session",
                {
                    "action": "read",
                    "session_id": session_id,
                    "cursor": 0,
                    "wait_seconds": 1,
                },
            )
        )["data"]
        assert read["origin_trace"]["correlation_id"] == origin["correlation_id"]
        interaction = next(
            event
            for event in read["events"]
            if event["type"] == "interaction" and event["data"]["action"] == "write"
        )
        interaction_trace = interaction["data"]["trace"]
        assert interaction_trace["capability_id"] == "runtime.process_session"
        assert interaction_trace["correlation_id"] != origin["correlation_id"]
    finally:
        try:
            asyncio.run(
                server.capability_invoke(
                    "runtime.process_session",
                    {"action": "close", "session_id": session_id},
                )
            )
        except Exception:
            pass
