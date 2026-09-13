"""Capability Broker controls for the independent Browser Runtime."""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from browser_runtime import (
    browser_runtime_diagnostics,
    browser_runtime_status,
    start_browser_runtime,
    stop_browser_runtime,
)


_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}


def browser_runtime_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            id="runtime.browser_status",
            provider_id="runtime",
            remote_name="browser_status",
            title="Browser Runtime Status",
            description=(
                "Read the independent Playwright MCP browser runtime status, "
                "loopback endpoint, owned PID and managed profile identity."
            ),
            input_schema=_OBJECT,
            output_schema={"type": "object"},
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "browser", "lifecycle", "status"),
        ),
        CapabilityDescriptor(
            id="runtime.browser_diagnostics",
            provider_id="runtime",
            remote_name="browser_diagnostics",
            title="Browser Runtime Diagnostics",
            description=(
                "Read a bounded redacted tail of the fixed Playwright MCP runtime "
                "log together with current Browser Runtime status."
            ),
            input_schema=_OBJECT,
            output_schema={"type": "object"},
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "browser", "lifecycle", "diagnostics"),
        ),
        CapabilityDescriptor(
            id="runtime.browser_start",
            provider_id="runtime",
            remote_name="browser_start",
            title="Start Browser Runtime",
            description=(
                "Start the fixed reviewed Playwright MCP browser service on "
                "127.0.0.1:8931. No arbitrary command, port or package is accepted."
            ),
            input_schema=_OBJECT,
            output_schema={"type": "object"},
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "browser", "lifecycle", "start"),
        ),
        CapabilityDescriptor(
            id="runtime.browser_stop",
            provider_id="runtime",
            remote_name="browser_stop",
            title="Stop Browser Runtime",
            description=(
                "Stop only the browser runtime process whose PID and creation "
                "marker match PLA durable state."
            ),
            input_schema=_OBJECT,
            output_schema={"type": "object"},
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "browser", "lifecycle", "stop"),
        ),
    )


def register_browser_runtime_handlers(broker: CapabilityBroker) -> None:
    broker.register_internal_handler(
        "runtime.browser_status",
        lambda _args: browser_runtime_status(),
    )
    broker.register_internal_handler(
        "runtime.browser_diagnostics",
        lambda _args: browser_runtime_diagnostics(),
    )
    broker.register_internal_handler(
        "runtime.browser_start",
        lambda _args: start_browser_runtime(),
    )
    broker.register_internal_handler(
        "runtime.browser_stop",
        lambda _args: stop_browser_runtime(),
    )
