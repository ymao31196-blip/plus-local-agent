"""Conversation identity propagation across durable TaskStore worker threads."""
from __future__ import annotations

import sqlite3
from time import monotonic, sleep

from runtime import activity_runtime, conversation_activity, task_store
from runtime.event_runtime import EventStore
from tooling.internal_tool_executor import LocalToolResult


def _wait_for_terminal(store: task_store.TaskStore, task_id: str) -> dict:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        record = store.get(task_id)
        if record["status"] in task_store.TERMINAL:
            return record
        sleep(.01)
    raise AssertionError(store.get(task_id))


def test_background_tools_keep_submitters_number_and_reset_reused_threads(tmp_path, monkeypatch):
    activity_store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity_runtime, "EVENT_STORE", activity_store)
    calls = []

    def fake_execute(tool, arguments):
        number = conversation_activity.current_conversation_number()
        calls.append((arguments["case"], number))
        call_id = activity_runtime.record_internal_action_start("run_process", {"program": "python"})
        activity_runtime.record_internal_action_finish("run_process", call_id, True)
        return LocalToolResult(tool, True, {"seen_number": number})

    monkeypatch.setattr(task_store, "execute_local_tool", fake_execute)
    tasks = task_store.TaskStore(max_workers=1, db_path=tmp_path / "tasks.sqlite3")
    try:
        submitted = []
        for name, number in (("first", 4), ("same", 4), ("second", 9), ("unknown", None)):
            token = conversation_activity.CURRENT_CONVERSATION_NUMBER.set(number)
            try:
                result = tasks.submit({"tool": "fake", "arguments": {"case": name}})
            finally:
                conversation_activity.CURRENT_CONVERSATION_NUMBER.reset(token)
            submitted.append((name, number, result["task_id"]))
        for name, number, task_id in submitted:
            record = _wait_for_terminal(tasks, task_id)
            assert record["status"] == "completed"
            assert record["conversation_number"] == number
            assert record["result"]["result"]["seen_number"] == number
        assert sorted(calls) == sorted((name, number) for name, number, _ in submitted)
        rows = activity_runtime.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert {row["conversation_number"] for row in rows} == {4, 9, None}
        assert len(rows) == 4
    finally:
        tasks.close()
        activity_store.close()
    assert conversation_activity.current_conversation_number() is None


def test_upgrade_old_task_database_adds_nullable_conversation_number(tmp_path):
    path = tmp_path / "legacy_tasks.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, "
            "request TEXT NOT NULL, result TEXT, error TEXT, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0, dropped_cursor INTEGER NOT NULL DEFAULT 0)"
        )
    tasks = task_store.TaskStore(db_path=path)
    try:
        assert tasks.initialize() == {"status": "ready"}
        with sqlite3.connect(path) as db:
            cols = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        assert "conversation_number" in cols
        result = tasks.submit({"tool": "not_known", "arguments": {}})
        record = _wait_for_terminal(tasks, result["task_id"])
        assert record["conversation_number"] is None
        assert record["status"] == "failed"
    finally:
        tasks.close()
