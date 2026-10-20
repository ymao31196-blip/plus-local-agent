"""Regression coverage for the private, argument-free Desktop activity plane."""
from pathlib import Path

import pytest

from runtime import activity_runtime as activity
from runtime.event_runtime import EventStore
from desktop_runtime.manager import Manager


def test_native_events_are_metadata_only_and_classify_semantic_failures(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)

    @activity.record_native_activity
    def write_text(secret):
        return {"ok": True, "content": secret}

    @activity.record_native_activity
    def run_process(secret):
        return {"returncode": 3, "stderr": secret}

    try:
        write_text("SUPER_SECRET_DO_NOT_LOG")
        run_process("SUPER_SECRET_DO_NOT_LOG")
        events = store.query()["events"]
        assert len(events) == 4
        assert [x["event_type"] for x in events] == [
            "native_tool.started", "native_tool.succeeded",
            "native_tool.started", "native_tool.failed",
        ]
        assert "SUPER_SECRET" not in str(events)
        recent = activity.recent_activity(tmp_path / "events.sqlite3")
        assert [(x["tool"], x["outcome"]) for x in recent["activities"]] == [
            ("run_process", "failed"), ("write_text", "success"),
        ]
        assert all(x["finished_at"] for x in recent["activities"])
    finally:
        store.close()


def test_exception_preserved_and_telemetry_is_fail_open(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)

    @activity.record_native_activity
    def read_text():
        raise PermissionError("private path")

    try:
        with pytest.raises(PermissionError, match="private path"):
            read_text()
        assert [x["event_type"] for x in store.query()["events"]] == [
            "native_tool.started", "native_tool.failed",
        ]
        store.close()
        # Write failures to a closed EventStore must not change execution results.
        @activity.record_native_activity
        def working():
            return {"ok": True}
        assert working() == {"ok": True}
    finally:
        store.close()


def test_projection_filters_housekeeping_and_preserves_unknown(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        store.emit(
            "capability.before_invoke",
            source="capability_broker", subject="runtime.provider_catalog",
            correlation_id="housekeeping",
        )
        store.emit(
            "capability.succeeded",
            source="capability_broker", subject="runtime.provider_catalog",
            correlation_id="housekeeping",
        )
        store.emit(
            "native_tool.started", source="native_mcp",
            subject="run_process", correlation_id="unresolved",
        )
        before = store.emit(
            "capability.before_invoke",
            source="capability_broker", subject="browser.navigate",
            provider_id="browser", correlation_id="browser-1",
        )
        store.emit(
            "capability.succeeded",
            source="capability_broker", subject="browser.navigate",
            provider_id="browser", correlation_id="browser-1",
            causation_id=before["event_id"],
        )
        items = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert len(items) == 2
        assert items[0]["tool"] == "browser.navigate"
        assert items[0]["outcome"] == "success"
        assert items[0]["provider"] == "browser"
        assert items[1]["tool"] == "run_process"
        assert items[1]["outcome"] == "unknown"
        assert items[1]["finished_at"] is None
    finally:
        store.close()


def test_internal_action_is_single_pair_and_no_args(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)
    try:
        uid = activity.record_internal_action_start("write_text")
        activity.record_internal_action_finish("write_text", uid, True)
        events = store.query()["events"]
        assert len(events) == 2
        assert events[0]["correlation_id"] == events[1]["correlation_id"]
        assert events[0]["payload"] == events[1]["payload"] == {}
    finally:
        store.close()


def test_private_rpc_is_read_only_and_stopped_runtime_safe(tmp_path):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    result = manager.dispatch("activity_recent", {})
    assert result == {"activities": [], "available": True,
                      "runtime_alive": False, "runtime_started_at": None}
    assert not (manager.config.root / "state" / "events.sqlite3").exists()
    with pytest.raises(ValueError, match="no arguments"):
        manager.dispatch("activity_recent", {"sql": "SELECT * FROM runtime_events"})


def test_unrecognized_internal_tool_name_is_not_persisted(tmp_path, monkeypatch):
    from tooling.internal_tool_executor import execute_local_tool
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)
    try:
        result = execute_local_tool("SECRET_TOKEN_SHOULD_NOT_PERSIST", {})
        assert result.ok is False
        events = store.query()["events"]
        assert len(events) == 2
        assert all(event["subject"] == "unknown_local_tool" for event in events)
        assert "SECRET_TOKEN" not in str(events)
    finally:
        store.close()


def test_denied_capability_is_a_completed_failure(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        store.emit("capability.gate_denied", source="capability_broker",
                   subject="browser.navigate", correlation_id="blocked")
        items = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert len(items) == 1
        assert items[0]["outcome"] == "failed"
        assert items[0]["finished_at"] is not None
    finally:
        store.close()


def test_real_server_endpoint_uses_activity_wrapper(tmp_path, monkeypatch):
    import server
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)
    monkeypatch.setattr(server, "internal_list_directory", lambda path, root: ["safe.txt"])
    try:
        assert server.list_directory() == ["safe.txt"]
        items = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert len(items) == 1
        assert items[0]["tool"] == "list_directory"
        assert items[0]["outcome"] == "success"
    finally:
        store.close()


def test_activity_unavailable_on_corrupt_database(tmp_path):
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not-a-database")
    assert activity.recent_activity(bad)["available"] is False
    with pytest.raises(ValueError):
        activity.recent_activity(bad, limit=500)

def test_activity_rpc_reports_current_owned_runtime_boundary(tmp_path, monkeypatch):
    from desktop_runtime import manager as manager_module
    manager = Manager(tmp_path / "data", tmp_path / "resources")

    class FakeRunningProcess:
        def poll(self):
            return None

    async def healthy_probe(verify=False):
        return True

    def fake_spawn(role, command, env):
        process = FakeRunningProcess()
        manager.children[role] = process
        return process

    monkeypatch.setattr(manager, "_free_port", lambda _port: None)
    monkeypatch.setattr(manager, "_spawn", fake_spawn)
    monkeypatch.setattr(manager, "_command", lambda _role: ["mock-runtime"])
    monkeypatch.setattr(manager, "_environment", lambda: {})
    monkeypatch.setattr(manager, "_probe", healthy_probe)
    monkeypatch.setattr(manager, "status", lambda: {"runtime": "ready"})
    monkeypatch.setattr(manager_module, "terminate_owned_process_tree", lambda _: None)

    assert manager.runtime_started_at is None
    assert manager.start() == {"runtime": "ready"}
    first = manager.dispatch("activity_recent", {})
    assert first["runtime_alive"] is True
    assert first["runtime_started_at"] == manager.runtime_started_at
    assert first["runtime_started_at"] is not None

    manager.stop()
    after_stop = manager.dispatch("activity_recent", {})
    assert after_stop["runtime_alive"] is False
    assert after_stop["runtime_started_at"] is None

    manager.start()
    after_restart = manager.dispatch("activity_recent", {})
    assert after_restart["runtime_alive"] is True
    assert after_restart["runtime_started_at"] is not None
    assert after_restart["runtime_started_at"] >= first["runtime_started_at"]
    manager.stop()

