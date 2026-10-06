"""Capability Broker lifecycle controls for PLA's production Execution Runner."""
from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from execution_runner_runtime import (
    execution_runner_result,
    execution_runner_probe,
    execution_runner_status,
    start_execution_runner,
    stop_execution_runner,
)


_EMPTY_INPUT = {"type": "object", "properties": {}, "additionalProperties": False}
_OBJECT_OUTPUT = {"type": "object"}
_RESULT_INPUT = {
    "type": "object",
    "properties": {
        "execution_request_id": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
    },
    "required": ["execution_request_id"],
    "additionalProperties": False,
}


def execution_runner_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            id="runtime.execution_runner_status",
            provider_id="runtime",
            remote_name="execution_runner_status",
            title="Execution Runner Status",
            description=(
                "Read status and verified health of the production out-of-process "
                "Execution Runner used by default generic one-shot execution."
            ),
            input_schema=_EMPTY_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "execution", "runner", "production", "status"),
        ),
        CapabilityDescriptor(
            id="runtime.execution_runner_probe",
            provider_id="runtime",
            remote_name="execution_runner_probe",
            title="Probe Execution Runner",
            description=(
                "Run one fixed harmless child-process probe through the production "
                "out-of-process Execution Runner. No command, cwd, env, or stdin is accepted."
            ),
            input_schema=_EMPTY_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "execution", "runner", "production", "probe"),
        ),
        CapabilityDescriptor(
            id="runtime.execution_runner_result",
            provider_id="runtime",
            remote_name="execution_runner_result",
            title="Execution Runner Result",
            description=(
                "Read one retained Runner execution result by its exact "
                "execution_request_id without re-executing the command."
            ),
            input_schema=_RESULT_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "execution", "runner", "production", "result"),
        ),
        CapabilityDescriptor(
            id="runtime.execution_runner_start",
            provider_id="runtime",
            remote_name="execution_runner_start",
            title="Start Execution Runner",
            description=(
                "Start the authenticated named-pipe production Execution Runner as an "
                "independent local process. Normal PLA startup ensures this lifecycle automatically."
            ),
            input_schema=_EMPTY_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "execution", "runner", "production", "start"),
        ),
        CapabilityDescriptor(
            id="runtime.execution_runner_stop",
            provider_id="runtime",
            remote_name="execution_runner_stop",
            title="Stop Execution Runner",
            description=(
                "Stop the authenticated production Execution Runner through its fixed "
                "named-pipe lifecycle protocol."
            ),
            input_schema=_EMPTY_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "execution", "runner", "production", "stop"),
        ),
    )


def register_execution_runner_handlers(broker: CapabilityBroker) -> None:
    broker.register_internal_handler(
        "runtime.execution_runner_status",
        lambda _args: execution_runner_status(),
    )
    broker.register_internal_handler(
        "runtime.execution_runner_probe",
        lambda _args: execution_runner_probe(),
    )
    broker.register_internal_handler(
        "runtime.execution_runner_result",
        lambda args: execution_runner_result(args["execution_request_id"]),
    )
    broker.register_internal_handler(
        "runtime.execution_runner_start",
        lambda _args: start_execution_runner(),
    )
    broker.register_internal_handler(
        "runtime.execution_runner_stop",
        lambda _args: stop_execution_runner(),
    )
