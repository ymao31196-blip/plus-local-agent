"""Confirmation-gated execution surfaces for selected high-risk local mutations."""
from __future__ import annotations

from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_models import CapabilityDescriptor


_OBJECT_OUTPUT = {"type": "object"}
_PROCESS_INPUT = {
    "type": "object",
    "properties": {
        "program": {"type": "string", "minLength": 1},
        "args": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "cwd": {"type": "string", "default": "."},
        "env": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
        },
        "stdin": {"type": ["string", "null"]},
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": 300,
            "default": 120,
        },
        "root": {
            "type": "string",
            "minLength": 1,
            "default": "workspace",
        },
    },
    "required": ["program"],
    "additionalProperties": False,
}


def local_mutation_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            id="runtime.environment_process",
            provider_id="runtime",
            remote_name="environment_process",
            title="Confirmed Environment Mutation",
            description=(
                "Run one allow-listed command only when deterministic semantics classify "
                "it as a local environment change such as Python pip, ensurepip, or venv. "
                "Invocation requires explicit INVOKE confirmation and preserves the normal "
                "PLA root, timeout, environment, and execution-observation policy."
            ),
            input_schema=_PROCESS_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="write_local",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "process", "environment-change", "confirmation"),
        ),
        CapabilityDescriptor(
            id="runtime.local_mutation_process",
            provider_id="runtime",
            remote_name="local_mutation_process",
            title="Confirmed High-Risk Local Mutation",
            description=(
                "Run one allow-listed command only when deterministic semantics classify "
                "it as an explicitly governed high-risk local Git mutation. Invocation "
                "requires explicit INVOKE confirmation and preserves existing PLA execution "
                "boundaries."
            ),
            input_schema=_PROCESS_INPUT,
            output_schema=_OBJECT_OUTPUT,
            risk_level="write_local",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "process", "local-mutation", "git", "confirmation"),
        ),
    )


def register_local_mutation_handlers(broker: CapabilityBroker) -> None:
    def _environment(arguments: dict):
        from tooling import local_tools
        return local_tools.run_confirmed_environment_process(
            program=arguments["program"],
            args=arguments.get("args"),
            cwd=arguments.get("cwd", "."),
            env=arguments.get("env"),
            stdin=arguments.get("stdin"),
            timeout=arguments.get("timeout", 120),
            root=arguments.get("root", "workspace"),
        )

    def _local_mutation(arguments: dict):
        from tooling import local_tools
        return local_tools.run_confirmed_local_mutation_process(
            program=arguments["program"],
            args=arguments.get("args"),
            cwd=arguments.get("cwd", "."),
            env=arguments.get("env"),
            stdin=arguments.get("stdin"),
            timeout=arguments.get("timeout", 120),
            root=arguments.get("root", "workspace"),
        )

    broker.register_internal_handler("runtime.environment_process", _environment)
    broker.register_internal_handler("runtime.local_mutation_process", _local_mutation)
