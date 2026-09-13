import asyncio
import hashlib

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
from mcp_client_manager import MCPClientManager
from observer_hook_runtime import (
    HookInvocationStore,
    ObserverHookRuntime,
    audit_observer,
)


def _event(
    *,
    sequence=1,
    event_id="event-1",
    event_type="capability.succeeded",
    correlation_id="corr-1",
):
    return {
        "sequence": sequence,
        "schema_version": 1,
        "event_id": event_id,
        "timestamp": "2026-09-13T00:00:00+00:00",
        "event_type": event_type,
        "source": "capability_broker",
        "subject": "fixture.read",
        "correlation_id": correlation_id,
        "causation_id": None,
        "capability_id": "fixture.read",
        "provider_id": "fixture",
        "transaction_id": None,
        "task_id": None,
        "payload": {"result_sha256": "a" * 64},
        "payload_sha256": "b" * 64,
    }


def test_hook_invocation_store_records_and_queries(tmp_path):
    store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    try:
        event = _event()
        record = store.record(
            hook_id="audit-observer",
            event=event,
            started_at="2026-09-13T00:00:00+00:00",
            finished_at="2026-09-13T00:00:00.001000+00:00",
            duration_ms=1.0,
            status="completed",
            result_sha256="c" * 64,
        )
        assert record["sequence"] == 1
        assert record["event_id"] == "event-1"
        assert record["status"] == "completed"

        result = store.query(
            hook_id="audit-observer",
            correlation_id="corr-1",
        )
        assert result["returned_count"] == 1
        assert result["has_more"] is False
        assert result["invocations"][0]["result_sha256"] == "c" * 64
    finally:
        store.close()


def test_hook_invocation_store_survives_reopen(tmp_path):
    path = tmp_path / "hooks.sqlite3"
    first = HookInvocationStore(path)
    first.record(
        hook_id="audit-observer",
        event=_event(),
        started_at="2026-09-13T00:00:00+00:00",
        finished_at="2026-09-13T00:00:00.001000+00:00",
        duration_ms=1.0,
        status="completed",
        result_sha256="c" * 64,
    )
    first.close()

    reopened = HookInvocationStore(path)
    try:
        result = reopened.query()
        assert result["returned_count"] == 1
        assert result["invocations"][0]["hook_id"] == "audit-observer"
        assert result["invocations"][0]["event_sequence"] == 1
    finally:
        reopened.close()


def test_observer_runtime_dispatches_matching_hooks_deterministically(tmp_path):
    store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    runtime = ObserverHookRuntime(store)
    seen = []

    runtime.register(
        "z-last",
        ("capability.succeeded",),
        lambda event: seen.append(("z-last", event["event_id"])) or {"ok": True},
    )
    runtime.register(
        "a-first",
        ("capability.succeeded",),
        lambda event: seen.append(("a-first", event["event_id"])) or {"ok": True},
    )
    runtime.register(
        "ignored",
        ("capability.failed",),
        lambda event: seen.append(("ignored", event["event_id"])) or {"ok": True},
    )

    try:
        result = runtime.dispatch(_event())
        assert result["status"] == "completed"
        assert result["matched_count"] == 2
        assert result["completed_count"] == 2
        assert result["failed_count"] == 0
        assert seen == [("a-first", "event-1"), ("z-last", "event-1")]

        records = store.query()["invocations"]
        assert [item["hook_id"] for item in records] == ["a-first", "z-last"]
    finally:
        store.close()


def test_observer_failure_is_recorded_without_raw_error_and_does_not_raise(tmp_path):
    store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    runtime = ObserverHookRuntime(store)

    def broken(_event):
        raise RuntimeError("sensitive hook failure")

    runtime.register(
        "broken-observer",
        ("capability.succeeded",),
        broken,
    )

    try:
        result = runtime.dispatch(_event())
        assert result["status"] == "partial"
        assert result["failed_count"] == 1

        record = store.query(hook_id="broken-observer")["invocations"][0]
        assert record["status"] == "failed"
        assert record["error_type"] == "RuntimeError"
        assert record["error_message_length"] == len("sensitive hook failure")
        assert record["error_message_sha256"] == hashlib.sha256(
            b"sensitive hook failure"
        ).hexdigest()
        assert "sensitive hook failure" not in str(record)
    finally:
        store.close()


def test_builtin_audit_observer_attests_persisted_event(tmp_path):
    store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    runtime = ObserverHookRuntime(store)
    runtime.register(
        "audit-observer",
        (
            "capability.before_invoke",
            "capability.succeeded",
            "capability.failed",
        ),
        audit_observer,
    )

    try:
        event = _event()
        result = runtime.dispatch(event)
        assert result["completed_count"] == 1
        record = store.query(hook_id="audit-observer")["invocations"][0]
        assert record["event_id"] == event["event_id"]
        assert record["event_type"] == event["event_type"]
        assert len(record["result_sha256"]) == 64
    finally:
        store.close()


def test_capability_broker_dispatches_observer_after_event_persistence(tmp_path):
    event_store = EventStore(tmp_path / "events.sqlite3")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)
    hooks.register(
        "audit-observer",
        (
            "capability.before_invoke",
            "capability.succeeded",
            "capability.failed",
        ),
        audit_observer,
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="fixture.read",
        provider_id="fixture",
        remote_name="read",
        title="Fixture Read",
        description="Fixture observer-hook test capability.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture",),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker = CapabilityBroker(registry, manager, event_store, hooks)
    broker.register_internal_handler(
        "fixture.read",
        lambda args: {"status": "completed", "value": args["value"]},
    )

    try:
        result = asyncio.run(broker.invoke("fixture.read", {"value": 9}))
        assert result["data"]["value"] == 9

        events = event_store.query(capability_id="fixture.read")["events"]
        invocations = hook_store.query(hook_id="audit-observer")["invocations"]

        assert [item["event_type"] for item in events] == [
            "capability.before_invoke",
            "capability.succeeded",
        ]
        assert [item["event_id"] for item in invocations] == [
            item["event_id"] for item in events
        ]
        assert all(item["status"] == "completed" for item in invocations)
    finally:
        hook_store.close()
        event_store.close()


def test_broken_observer_does_not_change_capability_result(tmp_path):
    event_store = EventStore(tmp_path / "events.sqlite3")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)

    def broken(_event):
        raise RuntimeError("observer exploded")

    hooks.register(
        "broken-observer",
        (
            "capability.before_invoke",
            "capability.succeeded",
        ),
        broken,
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="fixture.read",
        provider_id="fixture",
        remote_name="read",
        title="Fixture Read",
        description="Fixture observer fail-open test capability.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture",),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker = CapabilityBroker(registry, manager, event_store, hooks)
    broker.register_internal_handler(
        "fixture.read",
        lambda _args: {"status": "completed", "value": 42},
    )

    try:
        result = asyncio.run(broker.invoke("fixture.read", {}))
        assert result["status"] == "completed"
        assert result["data"]["value"] == 42

        failed = hook_store.query(
            hook_id="broken-observer",
            status="failed",
        )
        assert failed["returned_count"] == 2
    finally:
        hook_store.close()
        event_store.close()
