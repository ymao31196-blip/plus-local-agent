"""Built-in core capabilities exposed through the stable Capability Broker surface.

These descriptors are execution/governance adapters only. They never select a
capability, create follow-up plans, or bypass target capability policy.
"""

from __future__ import annotations

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
from observer_hook_runtime import ObserverHookRuntime
from gate_hook_runtime import GateHookRuntime
from local_tools import (
    git_push as controlled_git_push,
    git_tag as controlled_git_tag,
    workspace_root_remove as controlled_workspace_root_remove,
    workspace_root_upsert as controlled_workspace_root_upsert,
    workspace_roots_get as controlled_workspace_roots_get,
)
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
    requires_confirmation: bool = False,
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
        requires_confirmation=requires_confirmation,
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


def core_event_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.event_query",
            "event_query",
            "Query Runtime Events",
            (
                "Read append-only runtime events by cursor and optional filters. "
                "This control capability is excluded from event self-recording."
            ),
            {
                "type": "object",
                "properties": {
                    "after_sequence": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                    "event_types": {
                        "type": ["array", "null"],
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {"type": "string"},
                        "default": None,
                    },
                    "correlation_id": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                    "capability_id": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                    "provider_id": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                    "transaction_id": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                },
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("event", "runtime", "audit", "event-control"),
        ),
    )


def core_hook_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.hook_status",
            "hook_status",
            "Observer Hook Status",
            (
                "Read registered observer hooks. Hook-control capabilities are "
                "excluded from event recording and observer dispatch."
            ),
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("hook", "observer", "runtime", "hook-control"),
        ),
        _descriptor(
            "core.hook_invocation_query",
            "hook_invocation_query",
            "Query Observer Hook Invocations",
            (
                "Read durable observer-hook invocation records by cursor and "
                "optional filters without creating recursive hook observations."
            ),
            {
                "type": "object",
                "properties": {
                    "after_sequence": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                    "hook_id": {"type": ["string", "null"], "default": None},
                    "event_id": {"type": ["string", "null"], "default": None},
                    "event_type": {"type": ["string", "null"], "default": None},
                    "correlation_id": {
                        "type": ["string", "null"],
                        "default": None,
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["completed", "failed", None],
                        "default": None,
                    },
                },
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("hook", "observer", "runtime", "audit", "hook-control"),
        ),
    )


def core_gate_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.gate_status",
            "gate_status",
            "Gate Hook Status",
            "Read registered pre-invocation Gate Hooks without entering Gate recursion.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            tags=("hook", "gate", "runtime", "gate-control"),
        ),
        _descriptor(
            "core.gate_decision_query",
            "gate_decision_query",
            "Query Gate Hook Decisions",
            "Read durable Gate Hook ALLOW/DENY records by cursor and optional filters.",
            {
                "type": "object",
                "properties": {
                    "after_sequence": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
                    "hook_id": {"type": ["string", "null"], "default": None},
                    "correlation_id": {"type": ["string", "null"], "default": None},
                    "capability_id": {"type": ["string", "null"], "default": None},
                    "provider_id": {"type": ["string", "null"], "default": None},
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["completed", "failed", None],
                        "default": None,
                    },
                    "decision": {
                        "type": ["string", "null"],
                        "enum": ["allow", "deny", None],
                        "default": None,
                    },
                },
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("hook", "gate", "runtime", "audit", "gate-control"),
        ),
    )


def core_release_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.git_tag",
            "git_tag",
            "Create Release Tag",
            (
                "Atomically create one lightweight Git tag at the exact clean "
                "expected HEAD. Existing tags are never overwritten."
            ),
            {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "expected_head": {"type": "string"},
                    "cwd": {"type": "string", "default": "."},
                },
                "required": ["tag", "expected_head"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("git", "release", "tag"),
            requires_confirmation=True,
        ),
        _descriptor(
            "core.git_push",
            "git_push",
            "Push Release Refs",
            (
                "Atomically push the exact current branch and explicit local "
                "lightweight tags to an existing named remote, without force, "
                "then verify remote refs."
            ),
            {
                "type": "object",
                "properties": {
                    "remote": {"type": "string"},
                    "branch": {"type": "string"},
                    "expected_head": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "cwd": {"type": "string", "default": "."},
                },
                "required": ["remote", "branch", "expected_head"],
                "additionalProperties": False,
            },
            risk_level="write_external",
            tags=("git", "release", "push"),
            requires_confirmation=True,
        ),
    )


def core_workspace_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Machine-local workspace authorization controls."""

    sha_schema = {
        "anyOf": [
            {"type": "string", "minLength": 64, "maxLength": 64},
            {"type": "null"},
        ]
    }
    return (
        _descriptor(
            "core.workspace_roots_get",
            "workspace_roots_get",
            "Workspace Registry Status",
            "Read machine-local workspace roots and their access permissions.",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("workspace-registry", "governance", "local-access", "read"),
        ),
        _descriptor(
            "core.workspace_root_upsert",
            "workspace_root_upsert",
            "Add or Update Workspace Root",
            (
                "Add or update one machine-local workspace root using optimistic "
                "config SHA matching. This changes the local filesystem access boundary."
            ),
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
                    },
                    "path": {"type": "string", "minLength": 1},
                    "read": {"type": "boolean", "default": True},
                    "write": {"type": "boolean", "default": False},
                    "execute": {"type": "boolean", "default": False},
                    "expected_sha256": sha_schema,
                },
                "required": ["name", "path", "expected_sha256"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("workspace-registry", "governance", "local-access", "write"),
            requires_confirmation=True,
        ),
        _descriptor(
            "core.workspace_root_remove",
            "workspace_root_remove",
            "Remove Workspace Root",
            (
                "Remove one machine-local workspace root using optimistic config "
                "SHA matching. Built-in workspace and pla roots are not configurable."
            ),
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
                    },
                    "expected_sha256": sha_schema,
                },
                "required": ["name", "expected_sha256"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("workspace-registry", "governance", "local-access", "write"),
            requires_confirmation=True,
        ),
    )


def core_human_takeover_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Human/agent ownership controls for interactive providers."""

    return (
        _descriptor(
            "core.human_takeover_begin",
            "human_takeover_begin",
            "Begin Human Takeover",
            (
                "Pause AI access to selected Browser/Computer providers so a "
                "human can safely take control without model observation."
            ),
            {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "provider_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": ["browser", "computer"],
                        },
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("human-takeover", "governance", "pause", "control"),
        ),
        _descriptor(
            "core.human_takeover_status",
            "human_takeover_status",
            "Human Takeover Status",
            "Read durable human/agent ownership and resynchronization state.",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("human-takeover", "governance", "status", "read"),
        ),
        _descriptor(
            "core.human_takeover_resume",
            "human_takeover_resume",
            "Resume From Human Takeover",
            (
                "Confirm that the human is finished and enter observation-only "
                "resynchronization before AI control can resume."
            ),
            {
                "type": "object",
                "properties": {
                    "takeover_id": {"type": "string", "minLength": 1},
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
                "required": ["takeover_id", "expected_revision"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("human-takeover", "governance", "resume", "control"),
            requires_confirmation=True,
        ),
    )


def register_core_transaction_capabilities(
    registry: CapabilityRegistry,
    broker: CapabilityBroker,
    transaction_store: ActionTransactionStore,
    event_store: EventStore | None = None,
    observer_hooks: ObserverHookRuntime | None = None,
    gate_hooks: GateHookRuntime | None = None,
    human_takeover=None,
) -> None:
    """Register stable core governance capabilities and in-process handlers."""

    descriptors = [
        *core_transaction_descriptors(),
        *core_release_descriptors(),
        *core_workspace_descriptors(),
    ]
    if event_store is not None:
        descriptors.extend(core_event_descriptors())
    if observer_hooks is not None:
        descriptors.extend(core_hook_descriptors())
    if gate_hooks is not None:
        descriptors.extend(core_gate_descriptors())
    if human_takeover is not None:
        descriptors.extend(core_human_takeover_descriptors())

    registry.register_provider(
        "core",
        descriptors,
        enabled=True,
    )

    if human_takeover is not None:
        broker.register_internal_handler(
            "core.human_takeover_begin",
            lambda args: human_takeover.begin(
                args["reason"],
                args.get("provider_ids"),
            ),
        )
        broker.register_internal_handler(
            "core.human_takeover_status",
            lambda _args: human_takeover.status(),
        )
        broker.register_internal_handler(
            "core.human_takeover_resume",
            lambda args: human_takeover.resume(
                args["takeover_id"],
                args["expected_revision"],
                "RESUME",
            ),
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
    broker.register_internal_handler(
        "core.git_tag",
        lambda args: controlled_git_tag(
            args["tag"],
            args["expected_head"],
            args.get("cwd", "."),
            "pla",
        ),
    )
    broker.register_internal_handler(
        "core.git_push",
        lambda args: controlled_git_push(
            args["remote"],
            args["branch"],
            args["expected_head"],
            args.get("tags", []),
            "PUSH",
            args.get("cwd", "."),
            "pla",
        ),
    )
    broker.register_internal_handler(
        "core.workspace_roots_get",
        lambda _args: controlled_workspace_roots_get(),
    )
    broker.register_internal_handler(
        "core.workspace_root_upsert",
        lambda args: controlled_workspace_root_upsert(
            args["name"],
            args["path"],
            args.get("read", True),
            args.get("write", False),
            args.get("execute", False),
            args["expected_sha256"],
        ),
    )
    broker.register_internal_handler(
        "core.workspace_root_remove",
        lambda args: controlled_workspace_root_remove(
            args["name"],
            args["expected_sha256"],
        ),
    )
    if event_store is not None:
        broker.register_internal_handler(
            "core.event_query",
            lambda args: event_store.query(
                after_sequence=args.get("after_sequence", 0),
                limit=args.get("limit", 100),
                event_types=args.get("event_types"),
                correlation_id=args.get("correlation_id"),
                capability_id=args.get("capability_id"),
                provider_id=args.get("provider_id"),
                transaction_id=args.get("transaction_id"),
            ),
        )
    if observer_hooks is not None:
        broker.register_internal_handler(
            "core.hook_status",
            lambda _args: observer_hooks.status(),
        )
        broker.register_internal_handler(
            "core.hook_invocation_query",
            lambda args: observer_hooks.query_invocations(
                after_sequence=args.get("after_sequence", 0),
                limit=args.get("limit", 100),
                hook_id=args.get("hook_id"),
                event_id=args.get("event_id"),
                event_type=args.get("event_type"),
                correlation_id=args.get("correlation_id"),
                status=args.get("status"),
            ),
        )
    if gate_hooks is not None:
        broker.register_internal_handler(
            "core.gate_status",
            lambda _args: gate_hooks.status(),
        )
        broker.register_internal_handler(
            "core.gate_decision_query",
            lambda args: gate_hooks.query_decisions(
                after_sequence=args.get("after_sequence", 0),
                limit=args.get("limit", 100),
                hook_id=args.get("hook_id"),
                correlation_id=args.get("correlation_id"),
                capability_id=args.get("capability_id"),
                provider_id=args.get("provider_id"),
                status=args.get("status"),
                decision=args.get("decision"),
            ),
        )
