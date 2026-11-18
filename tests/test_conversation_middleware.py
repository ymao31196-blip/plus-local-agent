"""End-to-end FastMCP middleware tests with synthetic host metadata.

The live ChatGPT -> Secure MCP Tunnel path still requires a post-install E2E.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest
from fastmcp import Client, Context, FastMCP

from runtime import conversation_activity as conv
from runtime import activity_runtime as activity
from runtime.conversation_middleware import AutoConversationMiddleware
from runtime.event_runtime import EventStore


def _server():
    mcp = FastMCP("Session Attribution Test")
    mcp.add_middleware(AutoConversationMiddleware())

    @mcp.tool
    def ping() -> str:
        return "pong"

    @mcp.tool
    def explode() -> str:
        raise ValueError("synthetic failure")

    @mcp.tool
    def diagnose(ctx: Context) -> dict:
        return conv.session_diagnostic(ctx)

    @activity.record_native_activity
    def attributed_number() -> str:
        return str(conv.current_conversation_number() or "?")

    @mcp.tool
    def whoami() -> str:
        return attributed_number()

    return mcp


def test_real_fastmcp_tool_calls_auto_count_without_checkin(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conv, "EVENT_STORE", store)
    start = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

    async def scenario():
        async with Client(_server()) as client:
            for key in ("ChatGPT-dialogue-A", "ChatGPT-dialogue-A",
                        "ChatGPT-dialogue-B"):
                result = await client.call_tool(
                    "ping", meta={"openai/session": key}
                )
                assert result.data == "pong"
            missing = await client.call_tool("ping")
            assert missing.data == "pong"
            diag = await client.call_tool(
                "diagnose", meta={"openai/session": "ChatGPT-dialogue-A"}
            )
            assert diag.data["openai_session_present"] is True
            assert diag.data["anonymous_id"] is not None
            unknown = await client.call_tool("diagnose")
            assert unknown.data["openai_session_present"] is False
            assert unknown.data["anonymous_id"] is None

    try:
        asyncio.run(scenario())
        summary = conv.automatic_conversation_summary(
            tmp_path / "events.sqlite3", runtime_started_at=start,
        )
        assert summary["identified_count"] == 2
        assert summary["observed_calls"] == 6
        assert summary["unattributed_calls"] == 2
        assert sum(c["tool_calls"] for c in summary["conversations"]) == 4
        assert summary["method"] == "openai_session"
        events = store.query(event_types=["conversation.tool_started"])["events"]
        assert len(events) == 6
        assert "ChatGPT-dialogue" not in str(events)
        assert all("arguments" not in event["payload"] for event in events)
    finally:
        store.close()


def test_conversation_numbers_are_stable_and_isolated_across_concurrent_calls(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conv, "EVENT_STORE", store)
    monkeypatch.setattr(activity, "EVENT_STORE", store)

    async def scenario():
        server = _server()
        async with Client(server) as client_a, Client(server) as client_b:
            a1, b1, a2, unknown = await asyncio.gather(
                client_a.call_tool("whoami", meta={"openai/session": "conversation-A"}),
                client_b.call_tool("whoami", meta={"openai/session": "conversation-B"}),
                client_a.call_tool("whoami", meta={"openai/session": "conversation-A"}),
                client_b.call_tool("whoami"),
            )
            return a1.data, b1.data, a2.data, unknown.data

    try:
        a1, b1, a2, unknown = asyncio.run(scenario())
        assert a1 == a2
        assert a1 != b1
        assert unknown == "?"
        assert {int(a1), int(b1)} == {1, 2}

        starts = store.query(event_types=["conversation.tool_started"])["events"]
        numbers_by_fingerprint = {
            event["payload"]["fingerprint"]: event["payload"].get("conversation_number")
            for event in starts if event["payload"].get("fingerprint")
        }
        assert set(numbers_by_fingerprint.values()) == {1, 2}
        activity_rows = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert {row["conversation_number"] for row in activity_rows} == {1, 2, None}

        summary = conv.automatic_conversation_summary(
            tmp_path / "events.sqlite3",
            runtime_started_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        )
        assert {item["conversation_number"] for item in summary["conversations"]} == {1, 2}

        first_fingerprint = next(
            fingerprint for fingerprint, number in numbers_by_fingerprint.items()
            if number == int(a1)
        )
        store.close()
        reopened = EventStore(tmp_path / "events.sqlite3")
        try:
            assert reopened.register_conversation(first_fingerprint) == int(a1)
            assert reopened.register_conversation("f" * 64) == 3
        finally:
            reopened.close()
    finally:
        try:
            store.close()
        except Exception:
            pass


def test_desktop_management_polling_is_not_unknown_chat_activity(tmp_path, monkeypatch):
    """Idle UI polls must not inflate the unknown count from 0 to 12 or 20."""
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conv, "EVENT_STORE", store)
    start = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

    async def scenario():
        async with Client(_server()) as client:
            for _ in range(10):
                # Reproduce the Desktop's 10-second status polling workload.
                for tool in ("ping", "diagnose"):
                    result = await client.call_tool(
                        tool, meta=conv.DESKTOP_MANAGEMENT_META
                    )
                    assert not result.is_error
            # A real missing-metadata request must still be visible.
            unknown = await client.call_tool("ping")
            assert unknown.data == "pong"
            # An actual ChatGPT identity takes precedence over an origin hint.
            both = await client.call_tool(
                "ping", meta={
                    **conv.DESKTOP_MANAGEMENT_META,
                    "openai/session": "chatgpt-session-1",
                },
            )
            assert both.data == "pong"

    try:
        asyncio.run(scenario())
        summary = conv.automatic_conversation_summary(
            tmp_path / "events.sqlite3", runtime_started_at=start
        )
        assert summary["identified_count"] == 1
        assert summary["unattributed_calls"] == 1
        assert summary["observed_calls"] == 2
        events = store.query(event_types=["conversation.tool_started"])["events"]
        assert len(events) == 2
        assert all("request_origin" not in str(event) for event in events)
    finally:
        store.close()


def test_expired_calls_and_legacy_checkins_are_not_automatically_counted(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    base = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    events = []
    try:
        for kind, fingerprint in (
            ("conversation.tool_started", "a" * 64),
            ("conversation.tool_finished", "a" * 64),
            ("conversation.tool_started", "b" * 64),
            ("conversation.tool_started", None),
            ("conversation.checkin", "c" * 64),
        ):
            data = {"fingerprint": fingerprint} if fingerprint is not None else {"identity": "missing"}
            event = store.emit(
                kind,
                source="agent_declared_conversation" if kind == "conversation.checkin"
                       else "conversation_transport",
                subject="ping",
                correlation_id="call-A" if fingerprint == "a" * 64 else kind + str(fingerprint)[:4],
                payload=data,
            )
            events.append(event["event_id"])
    finally:
        store.close()

    with sqlite3.connect(tmp_path / "events.sqlite3") as db:
        for event_id, when in zip(events, (
            base - timedelta(seconds=61),  # expired start
            base - timedelta(seconds=10),  # renewed by finish
            base - timedelta(seconds=60),  # exact boundary => expired
            base - timedelta(seconds=15),  # unknown source
            base - timedelta(seconds=3),   # legacy check-in must not count
        )):
            db.execute(
                "UPDATE runtime_events SET timestamp = ? WHERE event_id = ?",
                (when.isoformat(), event_id),
            )

    start = (base - timedelta(hours=1)).isoformat()
    live = conv.automatic_conversation_summary(
        tmp_path / "events.sqlite3", runtime_started_at=start, now=base
    )
    assert live["identified_count"] == 1
    assert live["unattributed_calls"] == 1
    assert live["conversations"][0]["anonymous_id"] == "a" * 10
    assert live["conversations"][0]["last_tool"] == "ping"
    later = conv.automatic_conversation_summary(
        tmp_path / "events.sqlite3", runtime_started_at=start,
        now=base + timedelta(seconds=51),
    )
    assert later["identified_count"] == 0


def test_recently_stopped_grace_period_and_reactivation(tmp_path):
    """An idle session remains visible for 60s after leaving the active window."""
    store = EventStore(tmp_path / "events.sqlite3")
    base = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    events = []
    try:
        for fingerprint, number in (
            ("a" * 64, 1), ("b" * 64, 1000), ("c" * 64, 3),
            ("d" * 64, 4), ("e" * 64, 5), (None, None),
        ):
            event = store.emit(
                "conversation.tool_started", source="conversation_transport",
                subject="ping", correlation_id=number and f"call-{number}" or "unknown",
                payload=({"fingerprint": fingerprint, "conversation_number": number}
                         if fingerprint is not None else {"identity": "missing"}),
            )
            events.append(event["event_id"])
    finally:
        store.close()
    with sqlite3.connect(tmp_path / "events.sqlite3") as db:
        for event_id, age in zip(events, (30, 60, 90, 120, 15, 75)):
            db.execute("UPDATE runtime_events SET timestamp = ? WHERE event_id = ?",
                       ((base - timedelta(seconds=age)).isoformat(), event_id))
    start = (base - timedelta(hours=1)).isoformat()
    summary = conv.automatic_conversation_summary(
        tmp_path / "events.sqlite3", runtime_started_at=start, now=base,
    )
    assert summary["identified_count"] == 2
    assert {item["conversation_number"] for item in summary["conversations"]} == {1, 5}
    assert summary["recently_stopped_count"] == 2
    assert {item["conversation_number"] for item in summary["recently_stopped"]} == {1000, 3}
    assert summary["recently_stopped_window_seconds"] == 60
    assert summary["unattributed_calls"] == 0
    assert summary["observed_calls"] == 2
    assert all(item["conversation_number"] != 4 for item in summary["recently_stopped"])

    # A resumed conversation moves back to active with its original number.
    reopened = EventStore(tmp_path / "events.sqlite3")
    try:
        new_event = reopened.emit(
            "conversation.tool_started", source="conversation_transport",
            subject="ping", correlation_id="resume-b",
            payload={"fingerprint": "b" * 64, "conversation_number": 1000},
        )
    finally:
        reopened.close()
    with sqlite3.connect(tmp_path / "events.sqlite3") as db:
        db.execute("UPDATE runtime_events SET timestamp = ? WHERE event_id = ?",
                   ((base - timedelta(seconds=5)).isoformat(), new_event["event_id"]))
    resumed = conv.automatic_conversation_summary(
        tmp_path / "events.sqlite3", runtime_started_at=start, now=base,
    )
    assert resumed["identified_count"] == 3
    assert {item["conversation_number"] for item in resumed["conversations"]} == {1, 5, 1000}
    assert {item["conversation_number"] for item in resumed["recently_stopped"]} == {3}

    # At 120 seconds the inactive session has disappeared completely.
    expired = conv.automatic_conversation_summary(
        tmp_path / "events.sqlite3", runtime_started_at=start,
        now=base + timedelta(seconds=31),
    )
    assert {item["conversation_number"] for item in expired["recently_stopped"]} == {1}
    assert {item["conversation_number"] for item in expired["conversations"]} == {1000, 5}


def test_metadata_and_tool_failures_do_not_break_attribution(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conv, "EVENT_STORE", store)

    async def scenario():
        async with Client(_server()) as client:
            try:
                await client.call_tool(
                    "explode", meta={"openai/session": "known-A"}
                )
            except Exception:
                pass

    try:
        asyncio.run(scenario())
        assert len(store.query(event_types=["conversation.tool_failed"])["events"]) == 1
        assert conv.session_identity(None)["available"] is False
        assert conv.safe_tool_name("../../unsafe") == "unknown_mcp_tool"
    finally:
        store.close()


def test_unavailable_logging_does_not_prevent_tool_execution(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(conv, "EVENT_STORE", store)
    store.close()

    async def scenario():
        async with Client(_server()) as client:
            result = await client.call_tool(
                "ping", meta={"openai/session": "conversation"}
            )
            assert result.data == "pong"

    asyncio.run(scenario())



def test_desktop_manager_marks_every_local_management_mcp_call(tmp_path, monkeypatch):
    """UI refresh, browser readiness, and startup probes all carry the hint."""
    from types import SimpleNamespace
    from desktop_runtime.manager import Manager

    manager = Manager(tmp_path / "data", tmp_path / "resources")

    class Alive:
        def poll(self):
            return None

    manager.children["runtime"] = Alive()
    observed = []

    class FakeClient:
        def __init__(self, url, timeout=4):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def list_tools(self):
            return [
                SimpleNamespace(name="diagnose_client"),
                SimpleNamespace(name="browser_snapshot"),
            ]

        async def call_tool(self, name, arguments, **kwargs):
            observed.append((name, arguments, kwargs))
            if name == "capability_describe":
                return SimpleNamespace(is_error=False, data={"available": True})
            return SimpleNamespace(is_error=False, data={"ok": True})

    monkeypatch.setattr("fastmcp.Client", FakeClient)

    assert asyncio.run(manager._probe(verify=True)) == {"ok": True}
    assert asyncio.run(manager._management_mcp(
        "capability_invoke",
        {"capability_id": "runtime.provider_catalog", "arguments": {}},
    )) == {"ok": True}
    assert asyncio.run(manager._browser_probe()) in {
        "provider_ready", "edge_missing"
    }
    assert [name for name, _args, _opts in observed] == [
        "diagnose_client", "capability_invoke", "capability_describe"
    ]
    assert all(opts.get("meta") == conv.DESKTOP_MANAGEMENT_META
               for _name, _args, opts in observed)
