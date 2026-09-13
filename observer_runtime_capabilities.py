"""Descriptor/handler helpers for external Observer hot-plug controls."""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from external_observer_runtime import ExternalObserverRuntime


_OBJECT_OUTPUT = {"type": "object"}


def _descriptor(
    capability_id: str,
    remote_name: str,
    title: str,
    description: str,
    input_schema: dict,
    *,
    risk_level: str,
    requires_confirmation: bool,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        provider_id="runtime",
        remote_name=remote_name,
        title=title,
        description=description,
        input_schema=input_schema,
        output_schema=_OBJECT_OUTPUT,
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        requires_transaction=False,
        tags=("observer", "runtime", "hotplug", "hook-control"),
    )


def external_observer_runtime_descriptors() -> tuple[CapabilityDescriptor, ...]:
    observer_id_schema = {
        "type": "object",
        "properties": {
            "observer_id": {"type": "string", "minLength": 1},
        },
        "required": ["observer_id"],
        "additionalProperties": False,
    }
    return (
        _descriptor(
            "runtime.observer_status",
            "observer_status",
            "External Observer Status",
            "Read manifest-backed external Observer runtime state.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            requires_confirmation=False,
        ),
        _descriptor(
            "runtime.observer_rescan",
            "observer_rescan",
            "Rescan Observer Manifests",
            "Hot-apply validated external Observer manifest additions, removals, and changes.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="privileged",
            requires_confirmation=True,
        ),
        _descriptor(
            "runtime.observer_reload",
            "observer_reload",
            "Reload External Observer",
            "Reload one active external Observer from its current manifest.",
            observer_id_schema,
            risk_level="privileged",
            requires_confirmation=True,
        ),
        _descriptor(
            "runtime.observer_enable",
            "observer_enable",
            "Enable External Observer",
            "Temporarily enable one manifest-backed external Observer for this PLA process.",
            observer_id_schema,
            risk_level="privileged",
            requires_confirmation=True,
        ),
        _descriptor(
            "runtime.observer_disable",
            "observer_disable",
            "Disable External Observer",
            "Temporarily disable one active external Observer without changing its manifest.",
            observer_id_schema,
            risk_level="write_local",
            requires_confirmation=True,
        ),
    )


def register_external_observer_handlers(
    broker: CapabilityBroker,
    runtime: ExternalObserverRuntime,
) -> None:
    broker.register_internal_handler(
        "runtime.observer_status",
        lambda _args: runtime.status(),
    )
    broker.register_internal_handler(
        "runtime.observer_rescan",
        lambda _args: runtime.rescan(),
    )
    broker.register_internal_handler(
        "runtime.observer_reload",
        lambda args: runtime.reload(args["observer_id"]),
    )
    broker.register_internal_handler(
        "runtime.observer_enable",
        lambda args: runtime.enable(args["observer_id"]),
    )
    broker.register_internal_handler(
        "runtime.observer_disable",
        lambda args: runtime.disable(args["observer_id"]),
    )
