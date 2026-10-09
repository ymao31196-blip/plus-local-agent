"""Built-in provider hot-plug capabilities on the stable broker surface."""

from __future__ import annotations

from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_models import CapabilityDescriptor
from capabilities.capability_registry import CapabilityRegistry
from provider.external_provider_runtime import ExternalProviderRuntime
from hooks.external_observer_runtime import ExternalObserverRuntime
from capabilities.observer_runtime_capabilities import (
    external_observer_runtime_descriptors,
    register_external_observer_handlers,
)
from capabilities.runtime_lifecycle_capabilities import (
    register_runtime_lifecycle_handlers,
    runtime_lifecycle_descriptors,
)
from capabilities.browser_runtime_capabilities import (
    browser_runtime_descriptors,
    register_browser_runtime_handlers,
)
from capabilities.session_runtime_capabilities import (
    register_session_runtime_handlers,
    session_runtime_descriptors,
)
from capabilities.external_process_capabilities import (
    external_process_descriptors,
    register_external_process_handlers,
)
from capabilities.local_mutation_capabilities import (
    local_mutation_descriptors,
    register_local_mutation_handlers,
)
from execution.execution_runner_capabilities import (
    execution_runner_descriptors,
    register_execution_runner_handlers,
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
            "runtime.provider_catalog",
            "provider_catalog",
            "Provider Manifest Catalog",
            "Read all validated installed provider manifests, including disabled or unconfigured entries, with separate actual lifecycle evidence. Does not install or launch a provider.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            requires_confirmation=False,
            tags=("provider", "runtime", "catalog", "discovery"),
        ),
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
            "runtime.provider_setup",
            "provider_setup",
            "Install Reviewed Provider Dependencies",
            (
                "Install one manifest/provider's pinned reviewed dependency specs "
                "through the fixed PLA setup_providers.ps1 entrypoint."
            ),
            provider_id_schema,
            risk_level="privileged",
            requires_confirmation=True,
            tags=("provider", "runtime", "setup", "install"),
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
    descriptors.extend(runtime_lifecycle_descriptors())
    descriptors.extend(browser_runtime_descriptors())
    descriptors.extend(session_runtime_descriptors())
    descriptors.extend(external_process_descriptors())
    descriptors.extend(local_mutation_descriptors())
    descriptors.extend(execution_runner_descriptors())
    registry.register_provider(
        "runtime",
        descriptors,
        enabled=True,
    )
    broker.register_internal_handler(
        "runtime.provider_catalog",
        lambda _args: runtime.catalog(),
    )
    broker.register_internal_handler(
        "runtime.provider_status",
        lambda _args: runtime.status(),
    )
    broker.register_internal_handler(
        "runtime.provider_setup",
        lambda args: runtime.setup_dependencies(args["provider_id"]),
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
    register_runtime_lifecycle_handlers(broker)
    register_browser_runtime_handlers(broker)
    register_session_runtime_handlers(broker)
    register_external_process_handlers(broker)
    register_local_mutation_handlers(broker)
    register_execution_runner_handlers(broker)
