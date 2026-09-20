import asyncio

import pytest

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
import core_capabilities
from core_capabilities import register_core_transaction_capabilities
from event_runtime import EventStore
from mcp_client_manager import MCPClientManager
from observer_hook_runtime import (
    HookInvocationStore,
    ObserverHookRuntime,
    audit_observer,
)
from transaction_runtime import ActionTransactionStore


def _runtime():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    broker = CapabilityBroker(registry, manager)
    store = ActionTransactionStore()
    register_core_transaction_capabilities(registry, broker, store)
    return registry, broker, store


def _fixture_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="fixture.write",
        provider_id="fixture",
        remote_name="write",
        title="Fixture Write",
        description="Fixture transaction-gated write capability.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        risk_level="write_local",
        requires_transaction=True,
        tags=("fixture", "write"),
    )


def test_core_transaction_capabilities_are_registered_on_stable_surface():
    registry, _broker, store = _runtime()
    try:
        result = registry.search(
            "transaction",
            provider_id="core",
            include_unavailable=True,
            limit=20,
        )

        assert result["match_count"] == 6
        assert {item["id"] for item in result["capabilities"]} == {
            "core.transaction_create",
            "core.transaction_get",
            "core.transaction_checkpoint",
            "core.transaction_finalize",
            "core.transaction_invoke",
            "core.transaction_complete_external",
        }
    finally:
        store.close()






def test_core_transaction_create_get_and_finalize_roundtrip():
    _registry, broker, store = _runtime()
    try:
        created = asyncio.run(
            broker.invoke(
                "core.transaction_create",
                {
                    "goal": "core gateway test",
                    "steps": [
                        {
                            "step_id": "act",
                            "title": "Act",
                            "kind": "action",
                        }
                    ],
                    "metadata": {"source": "test"},
                },
            )
        )
        record = created["data"]

        fetched = asyncio.run(
            broker.invoke(
                "core.transaction_get",
                {"transaction_id": record["transaction_id"]},
            )
        )
        assert fetched["data"]["revision"] == 1

        checkpointed = asyncio.run(
            broker.invoke(
                "core.transaction_checkpoint",
                {
                    "transaction_id": record["transaction_id"],
                    "expected_revision": 1,
                    "step_id": "act",
                    "outcome": "succeeded",
                    "summary": "completed",
                    "evidence": {"ok": True},
                },
            )
        )
        assert checkpointed["data"]["steps"][0]["state"] == "succeeded"

        finalized = asyncio.run(
            broker.invoke(
                "core.transaction_finalize",
                {
                    "transaction_id": record["transaction_id"],
                    "expected_revision": checkpointed["data"]["revision"],
                    "decision": "commit",
                    "summary": "done",
                },
            )
        )
        assert finalized["data"]["status"] == "committed"
    finally:
        store.close()






def test_core_transaction_invoke_preserves_transaction_gate_and_audit():
    registry, broker, store = _runtime()
    registry.register_provider("fixture", [_fixture_descriptor()], enabled=True)
    broker.register_internal_handler(
        "fixture.write",
        lambda args: {
            "status": "completed",
            "written_value": args["value"],
        },
    )

    try:
        with pytest.raises(PermissionError, match="requires a transaction context"):
            asyncio.run(
                broker.invoke(
                    "fixture.write",
                    {"value": 7},
                )
            )

        created = asyncio.run(
            broker.invoke(
                "core.transaction_create",
                {
                    "goal": "invoke gated target",
                    "steps": [
                        {
                            "step_id": "write",
                            "title": "Write",
                            "kind": "action",
                        }
                    ],
                },
            )
        )["data"]

        invoked = asyncio.run(
            broker.invoke(
                "core.transaction_invoke",
                {
                    "transaction_id": created["transaction_id"],
                    "expected_revision": created["revision"],
                    "step_id": "write",
                    "capability_id": "fixture.write",
                    "arguments": {"value": 7},
                },
            )
        )
        envelope = invoked["data"]

        assert envelope["status"] == "completed"
        assert envelope["result"]["data"]["written_value"] == 7
        assert envelope["transaction"]["steps"][0]["state"] == "succeeded"
        evidence = envelope["transaction"]["steps"][0]["evidence"]
        assert evidence["capability_id"] == "fixture.write"
        assert evidence["provider_id"] == "fixture"
        assert evidence["risk_level"] == "write_local"
        assert isinstance(evidence["arguments_sha256"], str)
        assert len(evidence["arguments_sha256"]) == 64
    finally:
        store.close()






def test_core_transaction_invoke_forwards_target_confirmation():
    registry, broker, store = _runtime()
    descriptor = CapabilityDescriptor(
        id="fixture.confirmed",
        provider_id="fixture",
        remote_name="confirmed",
        title="Confirmed Fixture",
        description="Fixture requiring confirmation and transaction context.",
        input_schema={"type": "object", "additionalProperties": False},
        risk_level="privileged",
        requires_confirmation=True,
        requires_transaction=True,
        tags=("fixture", "confirmation"),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker.register_internal_handler(
        "fixture.confirmed",
        lambda _args: {"status": "completed"},
    )

    try:
        created = asyncio.run(
            broker.invoke(
                "core.transaction_create",
                {
                    "goal": "confirmation forwarding",
                    "steps": [
                        {
                            "step_id": "confirmed",
                            "title": "Confirmed",
                            "kind": "action",
                        }
                    ],
                },
            )
        )["data"]

        invoked = asyncio.run(
            broker.invoke(
                "core.transaction_invoke",
                {
                    "transaction_id": created["transaction_id"],
                    "expected_revision": created["revision"],
                    "step_id": "confirmed",
                    "capability_id": "fixture.confirmed",
                    "arguments": {},
                    "confirmation": "INVOKE",
                },
            )
        )

        assert invoked["data"]["status"] == "completed"
        evidence = invoked["data"]["transaction"]["steps"][0]["evidence"]
        assert evidence["requires_confirmation"] is True
        assert evidence["confirmation_supplied"] is True
    finally:
        store.close()


def test_core_transaction_complete_external_uses_persisted_verifier():
    registry, broker, store = _runtime()
    action = CapabilityDescriptor(
        id="fixture.external",
        provider_id="fixture",
        remote_name="external",
        title="External Fixture",
        description="Fixture that completes asynchronously.",
        input_schema={"type": "object", "additionalProperties": False},
        risk_level="write_local",
        requires_transaction=True,
        tags=("fixture", "external"),
    )
    status = CapabilityDescriptor(
        id="fixture.external_status",
        provider_id="fixture",
        remote_name="external_status",
        title="External Fixture Status",
        description="Read-only external completion verifier.",
        input_schema={
            "type": "object",
            "properties": {"launch_id": {"type": "string"}},
            "required": ["launch_id"],
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture", "external", "status"),
    )
    registry.register_provider("fixture", [action, status], enabled=True)
    launch_id = "a" * 32
    broker.register_internal_handler(
        "fixture.external",
        lambda _args: {
            "status": "external_pending",
            "launch_id": launch_id,
            "completion": {
                "capability_id": "fixture.external_status",
                "arguments": {"launch_id": launch_id},
            },
        },
    )
    broker.register_internal_handler(
        "fixture.external_status",
        lambda args: {
            "status": "completed",
            "launch_id": args["launch_id"],
            "verified": True,
        },
    )

    try:
        created = asyncio.run(
            broker.invoke(
                "core.transaction_create",
                {
                    "goal": "complete one external action",
                    "steps": [
                        {
                            "step_id": "external",
                            "title": "External",
                            "kind": "action",
                        }
                    ],
                },
            )
        )["data"]

        pending = asyncio.run(
            broker.invoke(
                "core.transaction_invoke",
                {
                    "transaction_id": created["transaction_id"],
                    "expected_revision": created["revision"],
                    "step_id": "external",
                    "capability_id": "fixture.external",
                    "arguments": {},
                },
            )
        )["data"]
        assert pending["status"] == "external_interaction_pending"
        assert pending["completion_required"] is True
        assert pending["transaction"]["steps"][0]["state"] == "running"

        with pytest.raises(ValueError, match="completion gate"):
            asyncio.run(
                broker.invoke(
                    "core.transaction_checkpoint",
                    {
                        "transaction_id": created["transaction_id"],
                        "expected_revision": pending["transaction"]["revision"],
                        "step_id": "external",
                        "outcome": "succeeded",
                        "summary": "manual bypass",
                        "evidence": {},
                    },
                )
            )

        completed = asyncio.run(
            broker.invoke(
                "core.transaction_complete_external",
                {
                    "transaction_id": created["transaction_id"],
                    "expected_revision": pending["transaction"]["revision"],
                    "step_id": "external",
                },
            )
        )["data"]
        assert completed["status"] == "completed"
        assert completed["transaction"]["steps"][0]["state"] == "succeeded"
        evidence = completed["transaction"]["steps"][0]["evidence"]
        assert evidence["completion_verifier_capability_id"] == "fixture.external_status"
    finally:
        store.close()


def test_core_release_capabilities_are_confirmation_gated(monkeypatch):
    descriptors = {
        item.id: item for item in core_capabilities.core_release_descriptors()
    }
    assert "root" not in descriptors["core.git_tag"].input_schema["properties"]
    assert "root" not in descriptors["core.git_push"].input_schema["properties"]

    registry, broker, store = _runtime()
    calls = {}

    def fake_tag(tag, expected_head, cwd, root):
        calls["tag"] = (tag, expected_head, cwd, root)
        return {"status": "completed", "tag": tag, "head": expected_head}

    def fake_push(remote, branch, expected_head, tags, confirmation, cwd, root):
        calls["push"] = (
            remote, branch, expected_head, tags, confirmation, cwd, root
        )
        return {"status": "completed", "remote": remote, "head": expected_head}

    monkeypatch.setattr(core_capabilities, "controlled_git_tag", fake_tag)
    monkeypatch.setattr(core_capabilities, "controlled_git_push", fake_push)

    try:
        release = registry.search(
            "release",
            provider_id="core",
            include_unavailable=True,
            limit=20,
        )
        assert {item["id"] for item in release["capabilities"]} == {
            "core.git_tag",
            "core.git_push",
        }
        assert all(
            item["requires_confirmation"] is True
            for item in release["capabilities"]
        )

        with pytest.raises(PermissionError, match="requires confirmation"):
            asyncio.run(
                broker.invoke(
                    "core.git_tag",
                    {"tag": "v1.1.0", "expected_head": "a" * 40},
                )
            )

        tagged = asyncio.run(
            broker.invoke(
                "core.git_tag",
                {"tag": "v1.1.0", "expected_head": "a" * 40},
                confirmation="INVOKE",
            )
        )
        assert tagged["data"]["tag"] == "v1.1.0"
        assert calls["tag"] == ("v1.1.0", "a" * 40, ".", "pla")

        pushed = asyncio.run(
            broker.invoke(
                "core.git_push",
                {
                    "remote": "origin",
                    "branch": "master",
                    "expected_head": "a" * 40,
                    "tags": ["v1.1.0"],
                },
                confirmation="INVOKE",
            )
        )
        assert pushed["data"]["remote"] == "origin"
        assert calls["push"] == (
            "origin", "master", "a" * 40, ["v1.1.0"], "PUSH", ".", "pla"
        )
    finally:
        store.close()



def test_core_workspace_registry_controls_are_confirmation_gated(monkeypatch):
    registry, broker, store = _runtime()
    calls = {}

    def fake_get():
        calls["get"] = True
        return {"config_exists": True, "config_sha256": "a" * 64, "roots": {}}

    def fake_upsert(name, path, read, write, execute, expected_sha256):
        calls["upsert"] = (
            name, path, read, write, execute, expected_sha256
        )
        return {
            "updated_root": name,
            "config_sha256": "b" * 64,
            "roots": {name: {"path": path}},
        }

    def fake_remove(name, expected_sha256):
        calls["remove"] = (name, expected_sha256)
        return {"removed_root": name, "config_sha256": "c" * 64, "roots": {}}

    monkeypatch.setattr(
        core_capabilities, "controlled_workspace_roots_get", fake_get
    )
    monkeypatch.setattr(
        core_capabilities, "controlled_workspace_root_upsert", fake_upsert
    )
    monkeypatch.setattr(
        core_capabilities, "controlled_workspace_root_remove", fake_remove
    )

    try:
        descriptors = {
            item.id: item for item in core_capabilities.core_workspace_descriptors()
        }
        assert set(descriptors) == {
            "core.workspace_roots_get",
            "core.workspace_root_upsert",
            "core.workspace_root_remove",
        }
        assert descriptors["core.workspace_roots_get"].requires_confirmation is False
        assert descriptors["core.workspace_root_upsert"].requires_confirmation is True
        assert descriptors["core.workspace_root_remove"].requires_confirmation is True

        status = asyncio.run(
            broker.invoke("core.workspace_roots_get", {})
        )
        assert status["data"]["config_sha256"] == "a" * 64

        with pytest.raises(PermissionError, match="requires confirmation"):
            asyncio.run(
                broker.invoke(
                    "core.workspace_root_upsert",
                    {
                        "name": "desktop",
                        "path": r"C:\\Users\\example\\Desktop",
                        "expected_sha256": "a" * 64,
                    },
                )
            )

        updated = asyncio.run(
            broker.invoke(
                "core.workspace_root_upsert",
                {
                    "name": "desktop",
                    "path": r"C:\\Users\\example\\Desktop",
                    "read": True,
                    "write": True,
                    "execute": False,
                    "expected_sha256": "a" * 64,
                },
                confirmation="INVOKE",
            )
        )
        assert updated["data"]["updated_root"] == "desktop"
        assert calls["upsert"] == (
            "desktop",
            r"C:\\Users\\example\\Desktop",
            True,
            True,
            False,
            "a" * 64,
        )

        removed = asyncio.run(
            broker.invoke(
                "core.workspace_root_remove",
                {
                    "name": "desktop",
                    "expected_sha256": "b" * 64,
                },
                confirmation="INVOKE",
            )
        )
        assert removed["data"]["removed_root"] == "desktop"
        assert calls["remove"] == ("desktop", "b" * 64)
    finally:
        store.close()


def test_core_event_query_reads_events_without_self_recording(tmp_path):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    event_store = EventStore(tmp_path / "events.sqlite3")
    broker = CapabilityBroker(registry, manager, event_store)
    tx_store = ActionTransactionStore()
    register_core_transaction_capabilities(
        registry,
        broker,
        tx_store,
        event_store,
    )
    fixture = CapabilityDescriptor(
        id="fixture.read",
        provider_id="fixture",
        remote_name="read",
        title="Fixture Read",
        description="Read fixture for event-plane testing.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture", "event-test"),
    )
    registry.register_provider("fixture", [fixture], enabled=True)
    broker.register_internal_handler(
        "fixture.read",
        lambda args: {"status": "completed", "value": args["value"]},
    )

    try:
        result = asyncio.run(
            broker.invoke(
                "fixture.read",
                {"value": 7},
            )
        )
        assert result["data"]["value"] == 7
        assert event_store.query()["returned_count"] == 2

        queried = asyncio.run(
            broker.invoke(
                "core.event_query",
                {
                    "after_sequence": 0,
                    "limit": 20,
                    "capability_id": "fixture.read",
                },
            )
        )
        events = queried["data"]["events"]
        assert [event["event_type"] for event in events] == [
            "capability.before_invoke",
            "capability.succeeded",
        ]

        # event-control capabilities never record themselves.
        assert event_store.query()["returned_count"] == 2
    finally:
        tx_store.close()
        event_store.close()


def test_core_hook_status_and_invocation_query_do_not_self_observe(tmp_path):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
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
    broker = CapabilityBroker(registry, manager, event_store, hooks)
    tx_store = ActionTransactionStore()
    register_core_transaction_capabilities(
        registry,
        broker,
        tx_store,
        event_store,
        hooks,
    )
    fixture = CapabilityDescriptor(
        id="fixture.read",
        provider_id="fixture",
        remote_name="read",
        title="Fixture Read",
        description="Fixture hook-control test capability.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture", "hook-test"),
    )
    registry.register_provider("fixture", [fixture], enabled=True)
    broker.register_internal_handler(
        "fixture.read",
        lambda args: {"status": "completed", "value": args["value"]},
    )

    try:
        result = asyncio.run(broker.invoke("fixture.read", {"value": 11}))
        assert result["data"]["value"] == 11
        assert event_store.query()["returned_count"] == 2
        assert hook_store.query()["returned_count"] == 2

        status = asyncio.run(
            broker.invoke(
                "core.hook_status",
                {},
            )
        )
        assert status["data"]["hook_count"] == 1
        assert status["data"]["hooks"][0]["hook_id"] == "audit-observer"

        queried = asyncio.run(
            broker.invoke(
                "core.hook_invocation_query",
                {
                    "after_sequence": 0,
                    "limit": 20,
                    "hook_id": "audit-observer",
                },
            )
        )
        invocations = queried["data"]["invocations"]
        assert [item["event_type"] for item in invocations] == [
            "capability.before_invoke",
            "capability.succeeded",
        ]

        # hook-control capabilities create neither EventStore nor HookStore noise.
        assert event_store.query()["returned_count"] == 2
        assert hook_store.query()["returned_count"] == 2
    finally:
        tx_store.close()
        hook_store.close()
        event_store.close()
