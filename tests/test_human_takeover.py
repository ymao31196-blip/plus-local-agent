import json

import asyncio

import pytest

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from core_capabilities import core_human_takeover_descriptors
from event_runtime import EventStore
from gate_hook_runtime import GateDecisionStore, GateHookRuntime
from human_takeover import HumanTakeoverController
from mcp_client_manager import MCPClientManager
from observer_hook_runtime import HookInvocationStore, ObserverHookRuntime


def _context(capability_id, provider_id, risk_level, tags):
    return {
        "correlation_id": "corr-1",
        "capability_id": capability_id,
        "provider_id": provider_id,
        "risk_level": risk_level,
        "tags": list(tags),
        "requires_confirmation": False,
        "confirmation_supplied": False,
        "requires_transaction": False,
        "transaction_context": False,
        "transaction_id": None,
        "arguments_sha256": "a" * 64,
        "argument_keys": [],
        "arguments": {},
    }


def _success(capability_id, provider_id):
    return {
        "event_type": "capability.succeeded",
        "provider_id": provider_id,
        "capability_id": capability_id,
    }


def test_begin_blocks_every_scoped_browser_capability(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("Enter credentials", ["browser"])

    assert started["state"] == "human"
    assert started["control_owner"] == "human"
    assert started["provider_ids"] == ["browser"]

    for capability_id, risk, tags in [
        ("browser.inspect", "read", {"observation"}),
        ("browser.screenshot", "read", {"observation"}),
        ("browser.type", "write_external", {"interaction"}),
    ]:
        result = controller.gate(
            _context(capability_id, "browser", risk, tags)
        )
        assert result == {
            "decision": "deny",
            "reason_code": "human_takeover_active",
        }

    assert controller.gate(
        _context("computer.inspect", "computer", "read", {"observation"})
    )["decision"] == "allow"


def test_resume_requires_explicit_token_and_matching_revision(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("2FA", ["browser"])

    with pytest.raises(PermissionError):
        controller.resume(
            started["takeover_id"],
            started["revision"],
            "NOPE",
        )

    with pytest.raises(ValueError, match="revision changed"):
        controller.resume(
            started["takeover_id"],
            started["revision"] + 1,
            "RESUME",
        )

    resumed = controller.resume(
        started["takeover_id"],
        started["revision"],
        "RESUME",
    )
    assert resumed["state"] == "resync_required"
    assert resumed["pending_resync_provider_ids"] == ["browser"]


def test_resync_allows_observation_but_blocks_navigation_and_control(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("Manual OAuth", ["browser"])
    controller.resume(started["takeover_id"], started["revision"], "RESUME")

    assert controller.gate(
        _context("browser.inspect", "browser", "read", {"observation"})
    )["decision"] == "allow"

    assert controller.gate(
        _context("browser.navigate", "browser", "read", {"navigation"})
    ) == {
        "decision": "deny",
        "reason_code": "human_takeover_resync_required",
    }

    assert controller.gate(
        _context("browser.click", "browser", "write_external", {"interaction"})
    )["decision"] == "deny"


def test_successful_inspect_releases_browser_control(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("Manual login", ["browser"])
    controller.resume(started["takeover_id"], started["revision"], "RESUME")

    outcome = controller.observer(_success("browser.inspect", "browser"))
    assert outcome["status"] == "completed"

    status = controller.status()
    assert status["state"] == "agent"
    assert status["active"] is False
    assert status["pending_resync_provider_ids"] == []
    assert controller.gate(
        _context("browser.click", "browser", "write_external", {"interaction"})
    )["decision"] == "allow"


def test_noncompleting_observation_does_not_release_control(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("Manual login", ["browser"])
    controller.resume(started["takeover_id"], started["revision"], "RESUME")

    outcome = controller.observer(_success("browser.console", "browser"))
    assert outcome["status"] == "ignored"
    assert controller.status()["state"] == "resync_required"


def test_multi_provider_takeover_requires_each_provider_to_resync(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    started = controller.begin("Manual cross-app sign-in")
    resumed = controller.resume(
        started["takeover_id"], started["revision"], "RESUME"
    )
    assert resumed["pending_resync_provider_ids"] == ["browser", "computer"]

    browser = controller.observer(_success("browser.inspect", "browser"))
    assert browser["status"] == "resyncing"
    assert browser["remaining_provider_ids"] == ["computer"]
    assert controller.gate(
        _context("browser.click", "browser", "write_external", {"interaction"})
    )["decision"] == "allow"
    assert controller.gate(
        _context("computer.click", "computer", "write_external", {"interaction"})
    )["decision"] == "deny"

    computer = controller.observer(_success("computer.inspect", "computer"))
    assert computer["status"] == "completed"
    assert controller.status()["state"] == "agent"


def test_state_survives_controller_reopen(tmp_path):
    path = tmp_path / "takeover.json"
    first = HumanTakeoverController(path)
    started = first.begin("Persist me", ["computer"])

    second = HumanTakeoverController(path)
    status = second.status()
    assert status["state"] == "human"
    assert status["takeover_id"] == started["takeover_id"]
    assert status["revision"] == started["revision"]


def test_corrupt_state_fails_closed_for_browser_and_computer(tmp_path):
    path = tmp_path / "takeover.json"
    path.write_text("{broken", encoding="utf-8")
    controller = HumanTakeoverController(path)

    result = controller.gate(
        _context("browser.click", "browser", "write_external", {"interaction"})
    )
    assert result == {
        "decision": "deny",
        "reason_code": "human_takeover_state_unreadable",
    }
    with pytest.raises(RuntimeError, match="unreadable"):
        controller.status()


def test_validation_rejects_unknown_provider_and_nested_takeover(tmp_path):
    controller = HumanTakeoverController(tmp_path / "takeover.json")
    with pytest.raises(ValueError, match="only supports"):
        controller.begin("bad", ["shell"])

    controller.begin("first", ["browser"])
    with pytest.raises(RuntimeError, match="already active"):
        controller.begin("second", ["browser"])


def test_persisted_shape_is_bounded_and_has_no_secret_event_dependency(tmp_path):
    path = tmp_path / "takeover.json"
    controller = HumanTakeoverController(path)
    controller.begin("Need human input", ["browser"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["state"] == "human"
    assert set(raw["provider_ids"]) == {"browser"}


def test_real_broker_path_blocks_then_resyncs_browser(tmp_path):
    event_store = EventStore(tmp_path / "events.sqlite3")
    gate_store = GateDecisionStore(tmp_path / "gates.sqlite3")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    gates = GateHookRuntime(gate_store)
    observers = ObserverHookRuntime(hook_store)
    controller = HumanTakeoverController(
        tmp_path / "takeover.json",
        event_store=event_store,
    )
    gates.register("human-takeover-control", controller.gate)
    observers.register(
        "human-takeover-resync",
        ("capability.succeeded",),
        controller.observer,
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptors = [
        CapabilityDescriptor(
            id="browser.inspect",
            provider_id="browser",
            remote_name="inspect",
            title="Inspect Browser",
            description="fixture",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            tags=("browser", "observation"),
        ),
        CapabilityDescriptor(
            id="browser.click",
            provider_id="browser",
            remote_name="click",
            title="Click Browser",
            description="fixture",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="write_external",
            tags=("browser", "interaction"),
        ),
    ]
    registry.register_provider("browser", descriptors, enabled=True)
    broker = CapabilityBroker(
        registry,
        manager,
        event_store,
        observers,
        gates,
    )
    broker.register_internal_handler(
        "browser.inspect",
        lambda _args: {"status": "completed", "snapshot": "fresh"},
    )
    broker.register_internal_handler(
        "browser.click",
        lambda _args: {"status": "completed"},
    )

    try:
        started = controller.begin("Manual login", ["browser"])

        with pytest.raises(PermissionError):
            asyncio.run(broker.invoke("browser.inspect", {}))
        with pytest.raises(PermissionError):
            asyncio.run(broker.invoke("browser.click", {}))

        resumed = controller.resume(
            started["takeover_id"],
            started["revision"],
            "RESUME",
        )
        assert resumed["state"] == "resync_required"

        with pytest.raises(PermissionError):
            asyncio.run(broker.invoke("browser.click", {}))

        inspected = asyncio.run(broker.invoke("browser.inspect", {}))
        assert inspected["data"]["snapshot"] == "fresh"
        assert controller.status()["state"] == "agent"

        clicked = asyncio.run(broker.invoke("browser.click", {}))
        assert clicked["data"]["status"] == "completed"

        decisions = gate_store.query(hook_id="human-takeover-control")["decisions"]
        reason_codes = [item["reason_code"] for item in decisions]
        assert "human_takeover_active" in reason_codes
        assert "human_takeover_resync_required" in reason_codes
        assert "human_takeover_resync_observation_allowed" in reason_codes
        assert "human_takeover_inactive" in reason_codes
    finally:
        hook_store.close()
        gate_store.close()
        event_store.close()


def test_core_takeover_catalog_requires_confirmation_for_resume():
    descriptors = {item.id: item for item in core_human_takeover_descriptors()}
    assert set(descriptors) == {
        "core.human_takeover_begin",
        "core.human_takeover_status",
        "core.human_takeover_resume",
    }
    assert descriptors["core.human_takeover_begin"].requires_confirmation is False
    assert descriptors["core.human_takeover_status"].risk_level == "read"
    assert descriptors["core.human_takeover_resume"].requires_confirmation is True


def test_state_change_callback_runs_for_begin_resume_and_resync(tmp_path):
    calls = []
    controller = HumanTakeoverController(
        tmp_path / "takeover.json",
        on_state_change=lambda: calls.append(controller.status()["state"]),
    )

    started = controller.begin("Manual login", ["browser"])
    resumed = controller.resume(
        started["takeover_id"],
        started["revision"],
        "RESUME",
    )
    controller.observer(_success("browser.inspect", "browser"))

    assert resumed["state"] == "resync_required"
    assert calls == ["human", "resync_required", "agent"]


def test_restore_visibility_restarts_overlay_for_persisted_state(tmp_path):
    path = tmp_path / "takeover.json"
    first = HumanTakeoverController(path)
    first.begin("Persisted handoff", ["browser"])

    calls = []
    reopened = HumanTakeoverController(
        path,
        on_state_change=lambda: calls.append("visible"),
    )
    assert reopened.restore_visibility() is True
    assert calls == ["visible"]

    absent = HumanTakeoverController(
        tmp_path / "absent.json",
        on_state_change=lambda: calls.append("unexpected"),
    )
    assert absent.restore_visibility() is False
    assert calls == ["visible"]
