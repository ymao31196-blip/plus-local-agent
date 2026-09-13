import asyncio
import hashlib

import pytest

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
from gate_hook_runtime import GateDecisionStore, GateHookRuntime
from mcp_client_manager import MCPClientManager
from observer_hook_runtime import HookInvocationStore, ObserverHookRuntime, audit_observer


def _context(**overrides):
    value = {
        "correlation_id": "corr-1",
        "capability_id": "fixture.write",
        "provider_id": "fixture",
        "risk_level": "write_local",
        "tags": ["fixture"],
        "requires_confirmation": False,
        "confirmation_supplied": False,
        "requires_transaction": False,
        "transaction_context": False,
        "arguments_sha256": "a" * 64,
        "argument_keys": ["value"],
        "arguments": {"value": 7},
    }
    value.update(overrides)
    return value


def _broker_runtime(tmp_path, gate_runtime):
    event_store = EventStore(tmp_path / "events.sqlite3")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="fixture.write",
        provider_id="fixture",
        remote_name="write",
        title="Fixture Write",
        description="Fixture gate-hook capability.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        risk_level="write_local",
        tags=("fixture",),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker = CapabilityBroker(
        registry,
        manager,
        event_store,
        None,
        gate_runtime,
    )
    return event_store, broker


def test_gate_decision_store_records_queries_and_survives_reopen(tmp_path):
    path = tmp_path / "gates.sqlite3"
    store = GateDecisionStore(path)
    record = store.record(
        hook_id="policy-a",
        context=_context(),
        started_at="2026-09-13T00:00:00+00:00",
        finished_at="2026-09-13T00:00:00.001000+00:00",
        duration_ms=1.0,
        status="completed",
        decision="allow",
        reason_code="baseline_allow",
        result_sha256="b" * 64,
    )
    assert record["sequence"] == 1
    store.close()

    reopened = GateDecisionStore(path)
    try:
        result = reopened.query(
            hook_id="policy-a",
            capability_id="fixture.write",
            decision="allow",
        )
        assert result["returned_count"] == 1
        assert result["decisions"][0]["reason_code"] == "baseline_allow"
    finally:
        reopened.close()


def test_gate_runtime_is_deterministic_and_deny_overrides(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)
    seen = []
    runtime.register(
        "z-allow",
        lambda context: seen.append(("z-allow", context["capability_id"]))
        or {"decision": "allow", "reason_code": "ok"},
    )
    runtime.register(
        "a-deny",
        lambda context: seen.append(("a-deny", context["capability_id"]))
        or {"decision": "deny", "reason_code": "blocked"},
    )

    try:
        result = runtime.evaluate(_context())
        assert result["decision"] == "deny"
        assert result["deny_hook_ids"] == ["a-deny"]
        assert seen == [
            ("a-deny", "fixture.write"),
            ("z-allow", "fixture.write"),
        ]
        assert [item["hook_id"] for item in result["records"]] == [
            "a-deny",
            "z-allow",
        ]
    finally:
        store.close()


def test_gate_failure_is_fail_closed_and_hides_raw_error(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)

    def broken(_context):
        raise RuntimeError("sensitive gate failure")

    runtime.register("broken-gate", broken)
    try:
        result = runtime.evaluate(_context())
        assert result["decision"] == "deny"
        record = store.query(hook_id="broken-gate")["decisions"][0]
        assert record["status"] == "failed"
        assert record["decision"] == "deny"
        assert record["reason_code"] == "gate_error"
        assert record["error_type"] == "RuntimeError"
        assert record["error_message_sha256"] == hashlib.sha256(
            b"sensitive gate failure"
        ).hexdigest()
        assert "sensitive gate failure" not in str(record)
    finally:
        store.close()


def test_gate_receives_copy_and_cannot_mutate_real_arguments(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)

    def mutating_gate(context):
        context["arguments"]["value"] = 999
        return {"decision": "allow", "reason_code": "mutated_copy_only"}

    runtime.register("mutating-gate", mutating_gate)
    event_store, broker = _broker_runtime(tmp_path, runtime)
    broker.register_internal_handler(
        "fixture.write",
        lambda args: {"status": "completed", "value": args["value"]},
    )
    try:
        result = asyncio.run(broker.invoke("fixture.write", {"value": 7}))
        assert result["data"]["value"] == 7
    finally:
        store.close()
        event_store.close()


def test_broker_denial_blocks_provider_and_emits_gate_denied_event(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)
    runtime.register(
        "deny-gate",
        lambda _context: {"decision": "deny", "reason_code": "test_block"},
    )
    event_store, broker = _broker_runtime(tmp_path, runtime)
    called = {"value": False}

    def forbidden(_args):
        called["value"] = True
        return {"status": "completed"}

    broker.register_internal_handler("fixture.write", forbidden)
    try:
        with pytest.raises(PermissionError, match="denied by Gate Hook policy"):
            asyncio.run(broker.invoke("fixture.write", {"value": 7}))
        assert called["value"] is False
        events = event_store.query(capability_id="fixture.write")["events"]
        assert [event["event_type"] for event in events] == [
            "capability.gate_denied"
        ]
        payload = events[0]["payload"]
        assert payload["deny_hook_ids"] == ["deny-gate"]
        assert payload["reason_codes"] == ["test_block"]
        assert len(payload["arguments_sha256"]) == 64
        assert "value" not in str(payload)
    finally:
        store.close()
        event_store.close()


def test_broker_allow_preserves_normal_before_and_success_events(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)
    runtime.register(
        "allow-gate",
        lambda _context: {"decision": "allow", "reason_code": "ok"},
    )
    event_store, broker = _broker_runtime(tmp_path, runtime)
    broker.register_internal_handler(
        "fixture.write",
        lambda args: {"status": "completed", "value": args["value"]},
    )
    try:
        result = asyncio.run(broker.invoke("fixture.write", {"value": 7}))
        assert result["data"]["value"] == 7
        events = event_store.query(capability_id="fixture.write")["events"]
        assert [event["event_type"] for event in events] == [
            "capability.before_invoke",
            "capability.succeeded",
        ]
        decisions = store.query()["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "allow"
    finally:
        store.close()
        event_store.close()


def test_gate_denial_can_be_observed_after_event_persistence(tmp_path):
    gate_store = GateDecisionStore(tmp_path / "gates.sqlite3")
    gates = GateHookRuntime(gate_store)
    gates.register(
        "deny-gate",
        lambda _context: {"decision": "deny", "reason_code": "blocked"},
    )
    event_store = EventStore(tmp_path / "events.sqlite3")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    observers = ObserverHookRuntime(hook_store)
    observers.register(
        "audit-observer",
        ("capability.gate_denied",),
        audit_observer,
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="fixture.write",
        provider_id="fixture",
        remote_name="write",
        title="Fixture Write",
        description="Fixture gate observer test.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk_level="write_local",
        tags=("fixture",),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker = CapabilityBroker(registry, manager, event_store, observers, gates)
    broker.register_internal_handler(
        "fixture.write",
        lambda _args: {"status": "completed"},
    )
    try:
        with pytest.raises(PermissionError):
            asyncio.run(broker.invoke("fixture.write", {}))
        event = event_store.query(capability_id="fixture.write")["events"][0]
        invocation = hook_store.query(hook_id="audit-observer")["invocations"][0]
        assert event["event_type"] == "capability.gate_denied"
        assert invocation["event_id"] == event["event_id"]
    finally:
        gate_store.close()
        hook_store.close()
        event_store.close()


def test_no_registered_gates_preserves_existing_behavior(tmp_path):
    store = GateDecisionStore(tmp_path / "gates.sqlite3")
    runtime = GateHookRuntime(store)
    event_store, broker = _broker_runtime(tmp_path, runtime)
    broker.register_internal_handler(
        "fixture.write",
        lambda args: {"status": "completed", "value": args["value"]},
    )
    try:
        result = asyncio.run(broker.invoke("fixture.write", {"value": 3}))
        assert result["data"]["value"] == 3
        assert store.query()["returned_count"] == 0
    finally:
        store.close()
        event_store.close()


def test_gate_control_capability_bypasses_gate_and_event_recursion(tmp_path):
    gate_store = GateDecisionStore(tmp_path / "gates.sqlite3")
    gates = GateHookRuntime(gate_store)
    gates.register(
        "deny-all",
        lambda _context: {"decision": "deny", "reason_code": "deny_all"},
    )
    event_store = EventStore(tmp_path / "events.sqlite3")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="core.gate_status",
        provider_id="core",
        remote_name="gate_status",
        title="Gate Status",
        description="Gate control recursion test.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("gate", "gate-control"),
    )
    registry.register_provider("core", [descriptor], enabled=True)
    broker = CapabilityBroker(registry, manager, event_store, None, gates)
    broker.register_internal_handler(
        "core.gate_status",
        lambda _args: {"status": "ready", "hook_count": 1},
    )
    try:
        result = asyncio.run(broker.invoke("core.gate_status", {}))
        assert result["data"]["hook_count"] == 1
        assert gate_store.query()["returned_count"] == 0
        assert event_store.query()["returned_count"] == 0
    finally:
        gate_store.close()
        event_store.close()
