"""Built-in provider hot-plug capabilities on the stable broker surface."""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from external_provider_runtime import ExternalProviderRuntime
from external_observer_runtime import ExternalObserverRuntime
from observer_runtime_capabilities import (
    external_observer_runtime_descriptors,
    register_external_observer_handlers,
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
    tags: tuple[str, ...],
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
        tags=tags,
    )


def provider_runtime_descriptors() -> tuple[CapabilityDescriptor, ...]:
    provider_id_schema = {
        "type": "object",
        "properties": {
            "provider_id": {
                "type": "string",
                "minLength": 1,
            }
        },
        "required": ["provider_id"],
        "additionalProperties": False,
    }
    return (
        _descriptor(
            "runtime.provider_status",
            "provider_status",
            "Provider Runtime Status",
            (
                "Read active manifest-backed providers, temporary enable/disable "
                "overrides, and current lifecycle state."
            ),
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="read",
            requires_confirmation=False,
            tags=("provider", "runtime", "hotplug", "status"),
        ),
        _descriptor(
            "runtime.provider_rescan",
            "provider_rescan",
            "Rescan Provider Manifests",
            (
                "Re-read validated provider manifests and hot-apply selected "
                "provider additions, removals, and changes without restarting PLA HTTP."
            ),
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="privileged",
            requires_confirmation=True,
            tags=("provider", "runtime", "hotplug", "rescan"),
        ),
        _descriptor(
            "runtime.provider_reload",
            "provider_reload",
            "Reload Provider",
            (
                "Reload one selected/configured provider from its current manifest "
                "and rediscover its allowlisted tools."
            ),
            provider_id_schema,
            risk_level="privileged",
            requires_confirmation=True,
            tags=("provider", "runtime", "hotplug", "reload"),
        ),
        _descriptor(
            "runtime.provider_enable",
            "provider_enable",
            "Enable Provider",
            (
                "Temporarily enable one manifest-backed provider for the current "
                "PLA process and rediscover its allowlisted tools."
            ),
            provider_id_schema,
            risk_level="privileged",
            requires_confirmation=True,
            tags=("provider", "runtime", "hotplug", "enable"),
        ),
        _descriptor(
            "runtime.provider_disable",
            "provider_disable",
            "Disable Provider",
            (
                "Temporarily disable one active provider for the current PLA "
                "process without modifying its manifest."
            ),
            provider_id_schema,
            risk_level="write_local",
            requires_confirmation=True,
            tags=("provider", "runtime", "hotplug", "disable"),
        ),
    )


def register_provider_runtime_capabilities(
    registry: CapabilityRegistry,
    broker: CapabilityBroker,
    runtime: ExternalProviderRuntime,
    observer_runtime: ExternalObserverRuntime | None = None,
) -> None:
    descriptors = list(provider_runtime_descriptors())
    if observer_runtime is not None:
        descriptors.extend(external_observer_runtime_descriptors())
    registry.register_provider(
        "runtime",
        descriptors,
        enabled=True,
    )
    broker.register_internal_handler(
        "runtime.provider_status",
        lambda _args: runtime.status(),
    )
    broker.register_internal_handler(
        "runtime.provider_rescan",
        lambda _args: runtime.rescan(),
    )
    broker.register_internal_handler(
        "runtime.provider_reload",
        lambda args: runtime.reload(args["provider_id"]),
    )
    broker.register_internal_handler(
        "runtime.provider_enable",
        lambda args: runtime.enable(args["provider_id"]),
    )
    broker.register_internal_handler(
        "runtime.provider_disable",
        lambda args: runtime.disable(args["provider_id"]),
    )
    if observer_runtime is not None:
        register_external_observer_handlers(
            broker,
            observer_runtime,
        )
