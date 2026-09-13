"""Stable Capability Broker controls for the Runtime Lifecycle Plane."""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from runtime_lifecycle import (
    lifecycle_status,
    request_http_restart,
    restart_request_status,
)


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
        tags=("runtime", "lifecycle"),
    )


def runtime_lifecycle_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "runtime.lifecycle_status",
            "lifecycle_status",
            "Runtime Lifecycle Status",
            "Read PLA HTTP, tunnel health, and independent Lifecycle Broker readiness.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            requires_confirmation=False,
        ),
        _descriptor(
            "runtime.restart_http",
            "restart_http",
            "Restart PLA HTTP",
            (
                "Queue one exact restart of the current PLA HTTP process through "
                "the independent Lifecycle Broker. The tunnel is not restarted."
            ),
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="privileged",
            requires_confirmation=True,
        ),
        _descriptor(
            "runtime.restart_status",
            "restart_status",
            "Runtime Restart Status",
            "Read the durable status of one previously accepted HTTP restart request.",
            {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "pattern": "^[0-9a-fA-F]{32}$",
                    }
                },
                "required": ["request_id"],
                "additionalProperties": False,
            },
            risk_level="read",
            requires_confirmation=False,
        ),
    )


def register_runtime_lifecycle_handlers(broker: CapabilityBroker) -> None:
    broker.register_internal_handler(
        "runtime.lifecycle_status",
        lambda _args: lifecycle_status(),
    )
    broker.register_internal_handler(
        "runtime.restart_http",
        lambda _args: request_http_restart(),
    )
    broker.register_internal_handler(
        "runtime.restart_status",
        lambda args: restart_request_status(args["request_id"]),
    )
