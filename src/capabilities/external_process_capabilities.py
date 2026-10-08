"""Confirmation-gated execution surface for semantically external-writing commands."""
from __future__ import annotations

from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_models import CapabilityDescriptor


_OBJECT_OUTPUT = {"type": "object"}


def external_process_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            id="runtime.external_process",
            provider_id="runtime",
            remote_name="external_process",
            title="Confirmed External Process",
            description=(
                "Run one allow-listed local command only when deterministic command "
                "semantics classify it as an external write. Invocation requires "
                "explicit INVOKE confirmation and preserves the normal PLA root, "
                "workspace, timeout, environment, and execution-observation policy."
            ),
            input_schema={
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
            },
            output_schema=_OBJECT_OUTPUT,
            risk_level="write_external",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "process", "external-write", "confirmation"),
        ),
    )


def register_external_process_handlers(broker: CapabilityBroker) -> None:
    def _run(arguments: dict):
        # Lazy import keeps the runtime descriptor module free of local_tools cycles.
        from tooling import local_tools
        return local_tools.run_confirmed_external_process(
            program=arguments["program"],
            args=arguments.get("args"),
            cwd=arguments.get("cwd", "."),
            env=arguments.get("env"),
            stdin=arguments.get("stdin"),
            timeout=arguments.get("timeout", 120),
            root=arguments.get("root", "workspace"),
        )

    broker.register_internal_handler("runtime.external_process", _run)
