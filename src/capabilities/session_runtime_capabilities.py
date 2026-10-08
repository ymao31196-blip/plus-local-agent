"""Dynamic runtime capability for bounded persistent process sessions."""
from __future__ import annotations

from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_models import CapabilityDescriptor
from runtime.session_runtime import (
    DEFAULT_READ_OUTPUT_BYTES,
    MAX_INPUT_BYTES,
    MAX_INPUT_CHARACTERS,
    MAX_READ_OUTPUT_BYTES,
    MAX_WAIT_SECONDS,
    confirmed_session_write_request,
    process_session_request,
)


_OBJECT_OUTPUT = {"type": "object"}


def session_runtime_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            id="runtime.process_session",
            provider_id="runtime",
            remote_name="process_session",
            title="Persistent Process Session",
            description=(
                "Open, write, close stdin, read, resize, terminate, or close one "
                "bounded persistent allow-listed local process session. The stable "
                "default backend is pipe; native Windows ConPTY can be requested explicitly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "open",
                            "write",
                            "close_stdin",
                            "read",
                            "resize",
                            "terminate",
                            "close",
                        ],
                    },
                    "session_id": {"type": ["string", "null"]},
                    "program": {"type": ["string", "null"]},
                    "args": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "cwd": {"type": "string", "default": "."},
                    "env": {
                        "type": ["object", "null"],
                        "additionalProperties": {"type": "string"},
                    },
                    "root": {
                        "type": "string",
                        "minLength": 1,
                        "default": "workspace",
                    },
                    "terminal_mode": {
                        "type": "string",
                        "enum": ["pipe", "conpty"],
                        "default": "pipe",
                    },
                    "columns": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 32767,
                        "default": 120,
                    },
                    "rows": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 32767,
                        "default": 30,
                    },
                    "input": {
                        "type": ["string", "null"],
                        "maxLength": MAX_INPUT_CHARACTERS,
                    },
                    "input_base64": {
                        "type": ["string", "null"],
                        "maxLength": ((MAX_INPUT_BYTES + 2) // 3) * 4,
                    },
                    "cursor": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                    },
                    "wait_seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": MAX_WAIT_SECONDS,
                        "default": 0,
                    },
                    "max_output_bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_READ_OUTPUT_BYTES,
                        "default": DEFAULT_READ_OUTPUT_BYTES,
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            output_schema=_OBJECT_OUTPUT,
            risk_level="write_local",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("runtime", "process", "session", "interactive"),
        ),
        CapabilityDescriptor(
            id="runtime.confirmed_session_write",
            provider_id="runtime",
            remote_name="confirmed_session_write",
            title="Confirmed Session Input",
            description=(
                "Write stdin only to a persistent session whose inherited input policy "
                "requires approval, such as an interactive Python interpreter. The "
                "session policy is re-evaluated after explicit INVOKE confirmation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "minLength": 1},
                    "input": {
                        "type": ["string", "null"],
                        "maxLength": MAX_INPUT_CHARACTERS,
                    },
                    "input_base64": {
                        "type": ["string", "null"],
                        "maxLength": ((MAX_INPUT_BYTES + 2) // 3) * 4,
                    },
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
            output_schema=_OBJECT_OUTPUT,
            risk_level="write_local",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("runtime", "process", "session", "interactive", "confirmation"),
        ),
    )


def register_session_runtime_handlers(broker: CapabilityBroker) -> None:
    broker.register_internal_handler(
        "runtime.process_session",
        process_session_request,
    )
    broker.register_internal_handler(
        "runtime.confirmed_session_write",
        confirmed_session_write_request,
    )
