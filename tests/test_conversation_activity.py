"""Anonymous conversation check-in and desktop visibility regression tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.event_runtime import EventStore
from runtime import conversation_activity as conversations


KEY_A = "a1234567890123456789012345678901"
KEY_B = "b1234567890123456789012345678901"


def test_checkin_deduplicates_and_never_persists_raw_keys(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conversations, "EVENT_STORE", store)
    started = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    try:
        assert conversations.conversation_checkin(KEY_A)["registered"]
        assert conversations.conversation_checkin(KEY_A)["registered"]
        assert conversations.conversation_checkin(KEY_B)["registered"]
        result = conversations.conversation_summary(
            tmp_path / "events.sqlite3", runtime_started_at=started
        )
        assert result["identified_count"] == 2
        assert result["is_exact_window_count"] is False
        assert sorted(item["checkins"] for item in result["conversations"]) == [1, 2]
        events = store.query(event_types=["conversation.checkin"])["events"]
        assert len(events) == 3
        assert all("fingerprint" in event["payload"] for event in events)
        assert KEY_A not in str(events)
        assert KEY_B not in str(events)
    finally:
        store.close()


def test_current_runtime_boundary_hides_past_registrations(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conversations, "EVENT_STORE", store)
    try:
        conversations.conversation_checkin(KEY_A)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert conversations.conversation_summary(
            tmp_path / "events.sqlite3", runtime_started_at=future
        )["identified_count"] == 0
        assert conversations.conversation_summary(
            tmp_path / "events.sqlite3", runtime_started_at=None
        )["identified_count"] == 0
    finally:
        store.close()


def test_invalid_token_rejected_and_event_store_error_is_fail_open(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        conversations.conversation_checkin("short")
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conversations, "EVENT_STORE", store)
    store.close()
    assert conversations.conversation_checkin(KEY_A)["registered"] is False


def test_transport_probe_never_claims_browser_window_identity():
    class Request:
        protocol_version = "2026-07-28"

    class Context:
        request_context = Request()
        client_id = "sample-private-client"
        session_id = "sample-request-session"

    result = conversations.transport_probe(Context())
    assert result["protocol"] == "mrtr"
    assert result["client_fingerprint"] is not None
    assert result["request_session_fingerprint"] is not None
    assert result["conversation_identity_from_transport"] is False
    assert "sample-private" not in str(result)
    assert "sample-request" not in str(result)

def test_desktop_rpc_counts_auto_sessions_only_while_runtime_is_alive(tmp_path, monkeypatch):
    from desktop_runtime.manager import Manager

    manager = Manager(tmp_path / "data", tmp_path / "resources")
    event_db = manager.config.root / "state" / "events.sqlite3"
    store = EventStore(event_db)
    monkeypatch.setattr(conversations, "EVENT_STORE", store)

    class Alive:
        def poll(self):
            return None

    manager.children["runtime"] = Alive()
    manager.runtime_started_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    try:
        fingerprint = "d" * 64
        conversations.emit_tool_activity(
            "conversation.tool_started", correlation_id="call-1",
            tool="read_text", fingerprint=fingerprint)
        conversations.emit_tool_activity(
            "conversation.tool_finished", correlation_id="call-1",
            tool="read_text", fingerprint=fingerprint)
        # A legacy manual check-in is ignored by automatic counts.
        conversations.conversation_checkin(KEY_A)
        response = manager.dispatch("activity_recent", {})
        assert response["conversations"]["available"] is True
        assert response["conversations"]["identified_count"] == 1
        assert response["conversations"]["method"] == "openai_session"
        assert response["conversations"]["conversations"][0]["last_tool"] == "read_text"
        assert response["conversations"]["is_exact_window_count"] is False
        manager.children.clear()
        stopped = manager.dispatch("activity_recent", {})
        assert stopped["conversations"]["identified_count"] == 0
    finally:
        store.close()


def test_lease_expires_after_60_seconds_and_renews_on_same_key(tmp_path):
    """Different check-ins have independent TTLs; refreshing A does not create another A."""
    import sqlite3

    path = tmp_path / "events.sqlite3"
    store = EventStore(path)
    base = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    events = []
    try:
        for fingerprint in ("a" * 64, "b" * 64, "a" * 64, "c" * 64):
            event = store.emit(
                "conversation.checkin",
                source="agent_declared_conversation",
                subject="anonymous_dialogue",
                payload={"fingerprint": fingerprint},
            )
            events.append(event["event_id"])
    finally:
        store.close()

    # Historical check-ins are injected into a test database without sleeping.
    with sqlite3.connect(path) as con:
        for event_id, timestamp in zip(events, (
            base - timedelta(seconds=65),
            base - timedelta(seconds=20),
            base - timedelta(seconds=10),
            base - timedelta(seconds=61),
        )):
            con.execute("UPDATE runtime_events SET timestamp=? WHERE event_id=?",
                        (timestamp.isoformat(), event_id))
    start = (base - timedelta(hours=1)).isoformat()
    live = conversations.conversation_summary(path, runtime_started_at=start, now=base)
    assert live["identified_count"] == 2
    assert live["active_window_seconds"] == 60
    assert {r["anonymous_id"] for r in live["conversations"]} == {"a" * 10, "b" * 10}

    # B is now exactly 60 seconds old, so only A remains.
    exactly_sixty = conversations.conversation_summary(
        path, runtime_started_at=start, now=base + timedelta(seconds=40)
    )
    assert exactly_sixty["identified_count"] == 1
    assert exactly_sixty["conversations"][0]["anonymous_id"] == "a" * 10

    # A also expires after 60 seconds without another check-in.
    expired = conversations.conversation_summary(
        path, runtime_started_at=start, now=base + timedelta(seconds=50)
    )
    assert expired["identified_count"] == 0


def test_future_checkin_not_counted_until_time_passes(tmp_path):
    import sqlite3

    path = tmp_path / "events.sqlite3"
    store = EventStore(path)
    try:
        event = store.emit(
            "conversation.checkin", source="agent_declared_conversation",
            subject="anonymous_dialogue", payload={"fingerprint": "d" * 64},
        )
    finally:
        store.close()

    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE runtime_events SET timestamp=? WHERE event_id=?",
                    ((now + timedelta(seconds=30)).isoformat(), event["event_id"]))
    start = (now - timedelta(minutes=5)).isoformat()
    assert conversations.conversation_summary(
        path, runtime_started_at=start, now=now
    )["identified_count"] == 0
    assert conversations.conversation_summary(
        path, runtime_started_at=start, now=now + timedelta(seconds=30)
    )["identified_count"] == 1
