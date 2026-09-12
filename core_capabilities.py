"""Built-in core capabilities exposed through the stable Capability Broker surface.

These descriptors are execution/governance adapters only. They never select a
capability, create follow-up plans, or bypass target capability policy.
"""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from transaction_action_envelope import invoke_capability_in_transaction
from transaction_runtime import ActionTransactionStore


_OBJECT_OUTPUT = {"type": "object"}

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "step_id": {"type": "string"},
        "title": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["action", "verify", "rollback"],
        },
        "rollback_step_id": {"type": ["string", "null"]},
    },
    "required": ["step_id", "title"],
    "additionalProperties": False,
}


def _descriptor(
    capability_id: str,
    remote_name: str,
    title: str,
    description: str,
    input_schema: dict,
    *,
    risk_level: str,
    tags: tuple[str, ...],
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        provider_id="core",
        remote_name=remote_name,
        title=title,
        description=description,
        input_schema=input_schema,
        output_schema=_OBJECT_OUTPUT,
        risk_level=risk_level,
        requires_confirmation=False,
        requires_transaction=False,
        tags=tags,
    )


def core_transaction_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.transaction_create",
            "transaction_create",
            "Create Transaction",
            "Create a durable transaction plan without executing any action.",
            {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": _STEP_SCHEMA,
                    },
                    "metadata": {"type": ["object", "null"]},
                },
                "required": ["goal", "steps"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("transaction", "governance", "create"),
        ),
        _descriptor(
            "core.transaction_get",
            "transaction_get",
            "Get Transaction",
            "Read one durable action transaction and its bounded event history.",
            {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                },
                "required": ["transaction_id"],
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("transaction", "governance", "read"),
        ),
        _descriptor(
            "core.transaction_checkpoint",
            "transaction_checkpoint",
            "Checkpoint Transaction",
            "Record one explicit transaction step outcome using optimistic revision concurrency.",
            {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "step_id": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": [
                            "started",
                            "succeeded",
                            "failed",
                            "verified",
                            "rolled_back",
                            "skipped",
                        ],
                    },
                    "summary": {"type": "string"},
                    "evidence": {"type": ["object", "null"]},
                },
                "required": [
                    "transaction_id",
                    "expected_revision",
                    "step_id",
                    "outcome",
                    "summary",
                ],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("transaction", "governance", "checkpoint"),
        ),
        _descriptor(
            "core.transaction_finalize",
            "transaction_finalize",
            "Finalize Transaction",
            "Commit, abort, or close a fully rolled-back transaction after state checks.",
            {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "decision": {
                        "type": "string",
                        "enum": ["commit", "abort", "rolled_back"],
                    },
                    "summary": {"type": "string"},
                },
                "required": [
                    "transaction_id",
                    "expected_revision",
                    "decision",
                    "summary",
                ],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("transaction", "governance", "finalize"),
        ),
        _descriptor(
            "core.transaction_invoke",
            "transaction_invoke_capability",
            "Invoke Capability In Transaction",
            (
                "Invoke exactly one caller-selected capability inside one pending "
                "transaction step. Target confirmation, schema, transaction policy, "
                "semantic failure handling, and audit evidence remain enforced by "
                "the existing Transaction Envelope and Capability Broker."
            ),
            {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "step_id": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                    "confirmation": {
                        "type": ["string", "null"],
                        "enum": ["INVOKE", None],
                    },
                },
                "required": [
                    "transaction_id",
                    "expected_revision",
                    "step_id",
                    "capability_id",
                    "arguments",
                ],
                "additionalProperties": False,
            },
            risk_level="privileged",
            tags=("transaction", "governance", "invoke", "capability"),
        ),
    )


def register_core_transaction_capabilities(
    registry: CapabilityRegistry,
    broker: CapabilityBroker,
    transaction_store: ActionTransactionStore,
) -> None:
    """Register stable core transaction capabilities and their in-process handlers."""

    registry.register_provider(
        "core",
        core_transaction_descriptors(),
        enabled=True,
    )

    broker.register_internal_handler(
        "core.transaction_create",
        lambda args: transaction_store.create(
            args["goal"],
            args["steps"],
            args.get("metadata"),
        ),
    )
    broker.register_internal_handler(
        "core.transaction_get",
        lambda args: transaction_store.get(args["transaction_id"]),
    )
    broker.register_internal_handler(
        "core.transaction_checkpoint",
        lambda args: transaction_store.checkpoint(
            args["transaction_id"],
            args["expected_revision"],
            args["step_id"],
            args["outcome"],
            args["summary"],
            args.get("evidence"),
        ),
    )
    broker.register_internal_handler(
        "core.transaction_finalize",
        lambda args: transaction_store.finalize(
            args["transaction_id"],
            args["expected_revision"],
            args["decision"],
            args["summary"],
        ),
    )

    async def invoke_handler(args: dict) -> dict:
        return await invoke_capability_in_transaction(
            transaction_store,
            broker,
            transaction_id=args["transaction_id"],
            expected_revision=args["expected_revision"],
            step_id=args["step_id"],
            capability_id=args["capability_id"],
            arguments=args["arguments"],
            confirmation=args.get("confirmation"),
        )

    broker.register_internal_handler(
        "core.transaction_invoke",
        invoke_handler,
    )
