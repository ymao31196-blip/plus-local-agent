import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys
from threading import Event
import time

from fastmcp import Client
import pytest
import local_tools
import task_store
from task_store import TaskStore
from internal_tool_executor import ACTION_LOCAL_TOOLS, INTERNAL_TOOL_SCHEMAS, LocalToolResult, execute_actions_request, EXECUTABLE_LOCAL_TOOLS
from server import mcp


def finish(store, task_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        record = store.get(task_id)
        if record["status"] in task_store.TERMINAL:
            return record
        time.sleep(.01)
    raise AssertionError(record)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    runtime = TaskStore(max_workers=2, db_path=tmp_path / "state.sqlite3")
    yield runtime
    runtime.close()


@pytest.mark.parametrize("tool,status", [("list_directory", "completed"), ("unknown", "failed")])
def test_durable_terminal_roundtrip(tmp_path, monkeypatch, tool, status):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    path = tmp_path / "db.sqlite3"
    store = TaskStore(db_path=path)
    task_id = store.submit({"tool": tool, "arguments": {}})["task_id"]
    original = finish(store, task_id)
    store.close()
    reopened = TaskStore(db_path=path)
    try:
        assert reopened.get(task_id) == original
        assert original["status"] == status
        assert reopened.get(task_id, 0)["events"][-1]["type"] == "task_" + status
    finally:
        reopened.close()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_restart_recovers_pending_without_replay(tmp_path, status):
    path = tmp_path / "db.sqlite3"
    store = TaskStore(db_path=path)
    store.get("init")
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO tasks(task_id,status,created_at,request) VALUES ('old', ?, 'then', ?)",
                   (status, json.dumps({"tool": "write_text", "arguments": {"path": "must-not-exist", "content": "bad"}})))
    reopened = TaskStore(db_path=path)
    try:
        record = reopened.get("old", 0)
        assert record["status"] == "failed"
        assert record["error"]["type"] == "TaskInterruptedByRestart"
        assert record["finished_at"]
        assert not (tmp_path / "must-not-exist").exists()
    finally:
        reopened.close()


def test_database_corruption_is_structured(tmp_path):
    path = tmp_path / "bad.sqlite3"
    path.write_bytes(b"not a database")
    store = TaskStore(db_path=path)
    try:
        assert store.get("x")["error"]["type"] == "TaskStoreError"
        assert store.submit({"tool": "list_directory", "arguments": {}})["error"]["type"] == "TaskStoreError"
        assert store.cancel("x")["error"]["type"] == "TaskStoreError"
        assert path.read_bytes() == b"not a database"
    finally:
        store.close()


def test_malformed_record_is_structured(store):
    task_id = store.submit({"tool": "unknown", "arguments": {}})["task_id"]
    finish(store, task_id)
    with store._lock, store._db:
        store._db.execute("UPDATE tasks SET result='not json' WHERE task_id=?", (task_id,))
    assert store.get(task_id)["error"]["type"] == "TaskStoreError"


def test_second_owner_cannot_recover_live_database(store):
    store.get("init")
    other = TaskStore(db_path=store.db_path)
    try:
        assert other.get("x")["error"]["type"] == "TaskStoreError"
    finally:
        other.close()


def test_concurrent_workers_persist_safely(store):
    with ThreadPoolExecutor(max_workers=8) as clients:
        ids = list(clients.map(lambda _: store.submit({"tool": "list_directory", "arguments": {}})["task_id"], range(24)))
    assert len(set(ids)) == 24
    assert all(finish(store, task_id)["status"] == "completed" for task_id in ids)


def test_cancel_queued_prevents_execution(tmp_path, monkeypatch):
    started, release = Event(), Event()
    calls = []
    def blocked(tool, arguments):
        calls.append(tool)
        started.set()
        release.wait(5)
        return LocalToolResult(tool, True)
    monkeypatch.setattr(task_store, "execute_local_tool", blocked)
    store = TaskStore(max_workers=1)
    try:
        first = store.submit({"tool": "first", "arguments": {}})["task_id"]
        assert started.wait(2)
        queued = store.submit({"tool": "never", "arguments": {}})["task_id"]
        assert store.cancel(queued)["status"] == "cancelled"
        release.set()
        finish(store, first)
        assert finish(store, queued)["started_at"] is None
    finally:
        release.set()
        store.close()
    assert calls == ["first"]


def test_cancel_real_long_process_and_preserve_unrelated_process(store, tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import pathlib,time; pathlib.Path('started').write_text('ok'); time.sleep(30); pathlib.Path('too-late').write_text('bad')"]}})["task_id"]
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / "started").exists()
        running = store.get(task_id, 0)
        assert running["status"] == "running"
        assert "action_started" in [e["type"] for e in running["events"]]
        with store._lock:
            child = next(iter(store._contexts[task_id].processes))
        store.cancel(task_id)
        record = finish(store, task_id)
        assert record["status"] == "cancelled"
        assert child.poll() is not None
        assert unrelated.poll() is None
        assert not (tmp_path / "too-late").exists()
        assert store.cancel(task_id) == record
        assert store.get(task_id) == record
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_terminal_cancel_idempotent_and_missing(store):
    task_id = store.submit({"tool": "list_directory", "arguments": {}})["task_id"]
    record = finish(store, task_id)
    assert store.cancel(task_id) == record
    assert store.cancel(task_id) == record
    assert store.cancel("missing")["error"]["type"] == "TaskNotFound"


def test_cursor_output_stderr_truncation_and_no_repeat(store):
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import sys;sys.stdout.write('x'*25001);sys.stderr.write('e'*23002)"]}})["task_id"]
    record = finish(store, task_id)
    assert record["status"] == "completed"
    result = record["result"]["result"]
    assert result["stdout_original_length"] == 25001
    assert result["stderr_original_length"] == 23002
    assert len(result["stdout"]) == 20000
    assert result["stdout_truncated"] and result["stderr_truncated"]
    page = store.get(task_id, 0)
    for kind, length in [("stdout", 25001), ("stderr", 23002)]:
        event = next(e for e in page["events"] if e["type"] == kind)
        assert event["data"]["original_length"] == length
        assert event["data"]["truncated"]
    assert page["events"][-1]["type"] == "task_completed"
    assert store.get(task_id, page["next_cursor"])["events"] == []


def test_bounded_event_retention_reports_gaps_and_pagination(store):
    task_id = store.submit({"tool": "unknown", "arguments": {}})["task_id"]
    finish(store, task_id)
    for i in range(300):
        store._observe(task_id, "sample", {"i": i})
    page = store.get(task_id, 0)
    assert page["events_truncated"]
    assert page["dropped_through_cursor"] > 0
    seen = []
    while True:
        seen.extend(e["cursor"] for e in page["events"])
        if not page["has_more"]:
            break
        page = store.get(task_id, page["next_cursor"])
    assert len(seen) == len(set(seen)) == task_store.MAX_EVENTS
    assert seen == sorted(seen)


@pytest.mark.parametrize("cursor", [-1, True, "0"])
def test_invalid_cursor(store, cursor):
    assert store.get("x", cursor)["error"]["type"] == "ValidationError"


@pytest.mark.parametrize("wait_seconds", [-1, True, 61, "1"])
def test_invalid_wait_seconds(store, wait_seconds):
    assert store.get("x", wait_seconds=wait_seconds)["error"]["type"] == "ValidationError"


def test_wait_for_terminal_avoids_external_polling(store):
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import time; time.sleep(.15)"]}})["task_id"]
    started = time.monotonic()
    record = store.get(task_id, wait_seconds=2)
    elapsed = time.monotonic() - started
    assert record["status"] == "completed"
    assert record["wait_timed_out"] is False
    assert elapsed >= .05


def test_wait_timeout_returns_current_state(store):
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import time; time.sleep(5)"]}})["task_id"]
    deadline = time.monotonic() + 2
    while store.get(task_id)["status"] == "queued" and time.monotonic() < deadline:
        time.sleep(.01)
    started = time.monotonic()
    record = store.get(task_id, wait_seconds=.05)
    elapsed = time.monotonic() - started
    assert record["status"] == "running"
    assert record["wait_timed_out"] is True
    assert elapsed >= .04
    store.cancel(task_id)
    finish(store, task_id)


@pytest.mark.parametrize("tool", ["cancel_task", "apply_changeset", "git_stage", "git_commit", "project_state_init", "project_state_update", "project_checkpoint", "project_decision_record", "project_evidence_record"])
def test_control_and_transaction_excluded_from_batches(tool):
    result = execute_actions_request([{"tool": tool, "arguments": {}}])
    assert result["results"][0]["error"]["type"] == "ToolNotAllowedInActions"
    assert "cancel_task" not in EXECUTABLE_LOCAL_TOOLS
    assert "apply_changeset" in EXECUTABLE_LOCAL_TOOLS
    assert "apply_changeset" not in ACTION_LOCAL_TOOLS
    assert "apply_changeset" in {tool["name"] for tool in INTERNAL_TOOL_SCHEMAS}
    assert "git_stage" in EXECUTABLE_LOCAL_TOOLS
    assert "git_stage" not in ACTION_LOCAL_TOOLS
    assert "git_stage" in {item["name"] for item in INTERNAL_TOOL_SCHEMAS}
    assert "git_commit" in EXECUTABLE_LOCAL_TOOLS
    assert "git_commit" not in ACTION_LOCAL_TOOLS
    assert "git_commit" in {item["name"] for item in INTERNAL_TOOL_SCHEMAS}
    for name in ("project_state_init", "project_state_update", "project_checkpoint"):
        assert name in EXECUTABLE_LOCAL_TOOLS
        assert name not in ACTION_LOCAL_TOOLS
        assert name in {item["name"] for item in INTERNAL_TOOL_SCHEMAS}
    assert "project_state_get" in EXECUTABLE_LOCAL_TOOLS
    assert "project_state_get" in ACTION_LOCAL_TOOLS
    for name in ("project_decision_record", "project_evidence_record"):
        assert name in EXECUTABLE_LOCAL_TOOLS
        assert name not in ACTION_LOCAL_TOOLS
    for name in ("project_decisions_get", "project_evidence_get"):
        assert name in EXECUTABLE_LOCAL_TOOLS
        assert name in ACTION_LOCAL_TOOLS
    for name in ("project_acceptance_set", "project_acceptance_evaluate"):
        assert name in EXECUTABLE_LOCAL_TOOLS
        assert name not in ACTION_LOCAL_TOOLS
        assert name in {item["name"] for item in INTERNAL_TOOL_SCHEMAS}
    for name in ("project_acceptance_get", "project_acceptance_evaluations_get"):
        assert name in EXECUTABLE_LOCAL_TOOLS
        assert name in ACTION_LOCAL_TOOLS
    assert "project_verify_acceptance" in EXECUTABLE_LOCAL_TOOLS
    assert "project_verify_acceptance" not in ACTION_LOCAL_TOOLS
    assert "project_verify_acceptance" in {item["name"] for item in INTERNAL_TOOL_SCHEMAS}
    assert "project_verifications_get" in EXECUTABLE_LOCAL_TOOLS
    assert "project_verifications_get" in ACTION_LOCAL_TOOLS


def test_mcp_schemas_and_cancel_does_not_accept_pid():
    async def check():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schema = tools["cancel_task"].input_schema
            assert set(schema["properties"]) == {"task_id"}
            task_result_properties = tools["task_result"].input_schema["properties"]
            assert "cursor" in task_result_properties
            assert "wait_seconds" in task_result_properties
            changes = tools["apply_changeset"].input_schema["properties"]["changes"]["items"]
            assert set(changes["required"]) == {"path", "patch", "expected_sha256"}
            record = (await client.call_tool_mcp("cancel_task", {"task_id": "missing"})).structured_content
            assert record["error"]["type"] == "TaskNotFound"
    asyncio.run(check())


def test_task_process_timeout_stdin_env(store):
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import os,sys,time;print(os.environ['TEST_VALUE']+sys.stdin.read(),flush=True);time.sleep(5)"],
        "stdin": "input", "env": {"TEST_VALUE": "env"}, "timeout": 1}})["task_id"]
    result = finish(store, task_id)["result"]
    assert result["error"]["type"] == "ProcessTimeout"
    assert "envinput" in result["result"]["stdout"]


def test_actual_runtime_process_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    code = """
import os, sys, time
import task_store
from internal_tool_executor import LocalToolResult
def blocked(tool, arguments):
    time.sleep(30)
    return LocalToolResult(tool, True)
task_store.execute_local_tool = blocked
store = task_store.TaskStore(max_workers=1, db_path=sys.argv[1])
first = store.submit({'tool':'read_text', 'arguments':{'path':'never'}})['task_id']
second = store.submit({'tool':'write_text', 'arguments':{'path':'never','content':'no'}})['task_id']
while store.get(first)['status'] != 'running':
    time.sleep(.01)
os._exit(0)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(path)], timeout=10, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    with sqlite3.connect(path) as db:
        assert {r[0] for r in db.execute("SELECT status FROM tasks")} == {"queued", "running"}
        ids = [r[0] for r in db.execute("SELECT task_id FROM tasks")]
    reopened = TaskStore(db_path=path)
    try:
        assert reopened.initialize()["status"] == "ready"
        for task_id in ids:
            record = reopened.get(task_id)
            assert record["status"] == "failed"
            assert record["error"]["type"] == "TaskInterruptedByRestart"
    finally:
        reopened.close()


def test_cancel_batch_retains_prior_results(store, tmp_path):
    task_id = store.submit({"actions": [
        {"tool": "write_text", "arguments": {"path": "prior", "content": "kept"}},
        {"tool": "run_process", "arguments": {"program": sys.executable, "args": ["-c", "import pathlib,time;pathlib.Path('ready').write_text('ok');time.sleep(30)"]}},
        {"tool": "write_text", "arguments": {"path": "never", "content": "no"}},
    ], "stop_on_error": False})["task_id"]
    deadline = time.monotonic() + 5
    while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert (tmp_path / "ready").exists()
    store.cancel(task_id)
    record = finish(store, task_id)
    assert record["status"] == "cancelled"
    assert record["result"]["actions_executed"] == 2
    assert record["result"]["results"][0]["ok"]
    assert (tmp_path / "prior").read_text() == "kept"
    assert not (tmp_path / "never").exists()


def test_database_cannot_be_inside_production_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = TaskStore(db_path=tmp_path / "state.sqlite3", require_outside_workspace=True)
    try:
        assert store.initialize()["error"]["type"] == "TaskStoreError"
        assert not (tmp_path / "state.sqlite3").exists()
    finally:
        store.close()


def test_task_powershell_uses_controlled_capture(store, tmp_path):
    (tmp_path / "large.txt").write_text("x" * 21000, encoding="utf-8")
    task_id = store.submit({"tool": "run_powershell", "arguments": {
        "command": "Get-Content", "parameters": {"LiteralPath": "large.txt", "Raw": True}}})["task_id"]
    record = finish(store, task_id)
    assert record["status"] == "completed", record
    assert record["result"]["result"]["stdout_truncated"]
    assert len(record["result"]["result"]["stdout"]) == 20000


def test_task_text_capture_counts_normalized_unicode(store):
    task_id = store.submit({"tool": "run_process", "arguments": {
        "program": sys.executable, "args": ["-c", "import sys;sys.stdout.buffer.write(('你好\\r\\n'*8000).encode('utf-8'))"]}})["task_id"]
    record = finish(store, task_id)
    result = record["result"]["result"]
    assert record["status"] == "completed"
    assert result["stdout_original_length"] == 24000
    assert len(result["stdout"]) == 20000
    assert "\r" not in result["stdout"]


def test_cursor_mode_avoids_repeating_request_and_result(store):
    task_id = store.submit({"tool": "list_directory", "arguments": {}})["task_id"]
    finish(store, task_id)
    record = store.get(task_id, 0)
    assert record["result_available"]
    assert "request" not in record and "result" not in record
    assert "request" in store.get(task_id) and "result" in store.get(task_id)


def test_action_limit_is_explicit(store):
    actions = [{"tool": "list_directory", "arguments": {}}] * 101
    with pytest.raises(ValueError, match="100"):
        execute_actions_request(actions)
    assert store.submit({"actions": actions})["status"] == "error"
