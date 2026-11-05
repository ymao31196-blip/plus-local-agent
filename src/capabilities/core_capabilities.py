"""Built-in core capabilities exposed through the stable Capability Broker surface.

These descriptors are execution/governance adapters only. They never select a
capability, create follow-up plans, or bypass target capability policy.
"""

from __future__ import annotations

import asyncio

from artifacts.artifact_bridge import (
    artifact_gc as controlled_artifact_gc,
    artifact_metadata as controlled_artifact_metadata,
    materialize_artifact as controlled_materialize_artifact,
    revoke_artifact as controlled_revoke_artifact,
    verify_artifact_provenance as controlled_verify_artifact_provenance,
)
from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_models import CapabilityDescriptor
from capabilities.capability_registry import CapabilityRegistry
from routing.capability_steering import (
    route_generic_request,
    routing_catalog,
    steering_rules,
)
from routing.routing_audit import audit_routing
from runtime.event_runtime import EventStore
from hooks.observer_hook_runtime import ObserverHookRuntime
from hooks.gate_hook_runtime import GateHookRuntime
from tooling.internal_tool_executor import INTERNAL_TOOL_SCHEMAS
from tooling.workspace_index import (
    index_init as controlled_index_init,
    index_refresh as controlled_index_refresh,
    index_status as controlled_index_status,
    index_search as controlled_index_search,
)
from tooling.network_operations import (
    http_probe as controlled_http_probe,
    http_download as controlled_http_download,
    network_diagnose as controlled_network_diagnose,
)
from tooling.local_tools import (
    LOCAL_TOOL_FUNCTIONS,
    git_push as controlled_git_push,
    git_refresh_index as controlled_git_refresh_index,
    git_tag as controlled_git_tag,
    workspace_root_remove as controlled_workspace_root_remove,
    workspace_root_upsert as controlled_workspace_root_upsert,
    workspace_roots_get as controlled_workspace_roots_get,
)
from transactions.transaction_action_envelope import (
    complete_external_capability_in_transaction,
    invoke_capability_in_transaction,
)
from transactions.transaction_runtime import ActionTransactionStore


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


_INTERNAL_SCHEMA_BY_NAME = {
    item["name"]: item
    for item in INTERNAL_TOOL_SCHEMAS
}


_CORE_LOCAL_TOOL_POLICIES = {
    "file_operation": ("write_local", ("file", "folder", "copy", "move", "rename", "zip", "archive", "文件整理", "压缩", "解压", "重命名", "复制")),
    "git_log": ("read", ("git", "history", "read")),
    "git_show": ("read", ("git", "history", "read")),
    "git_stage": ("write_local", ("git", "stage", "write")),
    "git_remove": ("write_local", ("git", "remove", "write")),
    "git_commit": ("write_local", ("git", "commit", "write")),
    "project_state_init": ("write_local", ("project", "state", "governance")),
    "project_state_get": ("read", ("project", "state", "governance")),
    "project_state_update": ("write_local", ("project", "state", "governance")),
    "project_checkpoint": ("write_local", ("project", "checkpoint", "governance")),
    "project_decision_record": ("write_local", ("project", "decision", "governance")),
    "project_decisions_get": ("read", ("project", "decision", "governance")),
    "project_evidence_record": ("write_local", ("project", "evidence", "governance")),
    "project_evidence_get": ("read", ("project", "evidence", "governance")),
    "project_acceptance_set": ("write_local", ("project", "acceptance", "governance")),
    "project_acceptance_get": ("read", ("project", "acceptance", "governance")),
    "project_acceptance_evaluate": ("write_local", ("project", "acceptance", "governance")),
    "project_acceptance_evaluations_get": ("read", ("project", "acceptance", "governance")),
    "project_verify_acceptance": ("write_local", ("project", "verification", "governance")),
    "project_verifications_get": ("read", ("project", "verification", "governance")),
}


def core_local_tool_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Mirror low-frequency local tools behind the dynamic core capability surface."""

    descriptors = []
    for tool_name, (risk_level, tags) in _CORE_LOCAL_TOOL_POLICIES.items():
        schema = _INTERNAL_SCHEMA_BY_NAME[tool_name]
        descriptors.append(
            _descriptor(
                f"core.{tool_name}",
                tool_name,
                tool_name.replace("_", " ").title(),
                schema["description"],
                schema["input_schema"],
                risk_level=risk_level,
                tags=tags,
            )
        )
    return tuple(descriptors)


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
    routing_authority: str = "recommendation",
    routing: dict | None = None,
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
        routing_authority=routing_authority,
        routing=dict(routing or {}),
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
        _descriptor(
            "core.transaction_complete_external",
            "transaction_complete_external",
            "Complete External Transaction Action",
            (
                "Invoke the read-only completion verifier already persisted on one "
                "running external-pending action step. The caller cannot replace the "
                "recorded verifier or its arguments."
            ),
            {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "step_id": {"type": "string"},
                },
                "required": [
                    "transaction_id",
                    "expected_revision",
                    "step_id",
                ],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("transaction", "governance", "external", "complete"),
        ),
    )


def core_routing_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "core.capability_route",
            "capability_route",
            "Route Capability Request",
            (
                "Evaluate one proposed run_process, run_powershell, or "
                "capability_invoke route against deterministic steering rules "
                "without invoking the proposed action or any suggested replacement."
            ),
            {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": [
                            "run_process",
                            "run_powershell",
                            "capability_invoke",
                        ],
                    },
                    "arguments": {"type": "object"},
                },
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("routing", "governance", "capability", "read"),
        ),
        _descriptor(
            "core.routing_audit",
            "routing_audit",
            "Audit Capability Routing Coverage",
            (
                "Read the complete capability registry and routing catalog, check "
                "catalog health, and surface conservative cross-provider overlap "
                "candidates for review. This never changes routing rules."
            ),
            {
                "type": "object",
                "properties": {
                    "include_unavailable": {
                        "type": "boolean",
                        "default": True,
                    },
                },
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("routing", "governance", "audit", "capability", "read"),
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
            "core.git_refresh_index",
            "git_refresh_index",
            "Refresh Git Index Metadata",
            "Repair stale Git file-status metadata for explicit tracked files only. "
            "Each file must have an exact expected SHA-256, the Git tree must "
            "remain unchanged, and content changes are never staged.",
            {
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array", "minItems": 1, "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "expected_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                            },
                            "required": ["path", "expected_sha256"],
                            "additionalProperties": False,
                        },
                    },
                    "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
                    "cwd": {"type": "string", "default": "."},
                },
                "required": ["changes", "expected_head"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("git", "index", "refresh", "maintenance"),
            requires_confirmation=True,
        ),
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


def core_artifact_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Artifact persistence capabilities exposed through the broker surface."""

    sha_schema = {
        "anyOf": [
            {"type": "string", "minLength": 64, "maxLength": 64},
            {"type": "null"},
        ]
    }
    return (
        _descriptor(
            "core.artifact_materialize",
            "artifact_materialize",
            "Materialize Artifact",
            (
                "Persist one live immutable artifact into a writable PLA root. "
                "Existing destinations are protected by default; replacement "
                "requires overwrite=true and the current destination SHA-256."
            ),
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1},
                    "destination_path": {"type": "string", "minLength": 1},
                    "root": {"type": "string", "default": "workspace"},
                    "overwrite": {"type": "boolean", "default": False},
                    "expected_sha256": sha_schema,
                },
                "required": ["artifact_id", "destination_path"],
                "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("artifact", "materialize", "persistence", "local-write"),
        ),
        _descriptor(
            "core.artifact_metadata",
            "artifact_metadata",
            "Artifact Metadata",
            "Read metadata for one live exported artifact without returning payload bytes.",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("artifact", "metadata", "read"),
        ),
        _descriptor(
            "core.artifact_verify",
            "artifact_verify",
            "Verify Artifact",
            "Verify artifact payload integrity and recorded provenance without returning bytes.",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1},
                    "recursive": {"type": "boolean", "default": True},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("artifact", "integrity", "verify", "read"),
        ),
        _descriptor(
            "core.artifact_gc_preview",
            "artifact_gc_preview",
            "Preview Artifact Cleanup",
            "Inspect expired artifact cleanup candidates without deleting anything.",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="read",
            tags=("artifact", "gc", "preview", "read"),
        ),
        _descriptor(
            "core.artifact_gc",
            "artifact_gc",
            "Cleanup Expired Artifacts",
            "Delete expired artifact candidates that are not protected by live descendants.",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level="destructive",
            tags=("artifact", "gc", "cleanup", "destructive"),
            requires_confirmation=True,
        ),
        _descriptor(
            "core.artifact_revoke",
            "artifact_revoke",
            "Revoke Artifact",
            "Revoke one exported artifact and delete its immutable snapshot.",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            risk_level="destructive",
            tags=("artifact", "revoke", "destructive"),
            requires_confirmation=True,
        ),
    )


def core_network_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Explicit public-network capabilities; no generic process or batch route."""
    return (
        _descriptor(
            "core.http_probe", "http_probe", "Public HTTPS Status Probe",
            "检查公开HTTPS的HEAD/GET状态与少量响应头；不读取正文。可显式采用本机系统HTTP代理+受验证公网IP连接。",
            {
                "type": "object", "properties": {
                    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                    "method": {"type": "string", "enum": ["HEAD", "GET"], "default": "HEAD"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                    "max_redirects": {"type": "integer", "minimum": 0, "maximum": 3, "default": 3},
                    "transport": {"type": "string", "enum": ["direct", "system_loopback_proxy"], "default": "direct"},
                }, "required": ["url"], "additionalProperties": False,
            },
            risk_level="read",
            tags=("network", "http", "https", "head", "status", "网页状态", "HTTP检查", "网络诊断"),
        ),
        _descriptor(
            "core.http_download", "http_download", "Controlled Public HTTPS Download",
            "从公网HTTPS下载有限大小文件到已授权目录；默认直连，可显式选择受限本机系统HTTP代理+TLS DoH，不允许局域网目标。需INVOKE确认。",
            {
                "type": "object", "properties": {
                    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                    "destination": {"type": "string", "minLength": 1},
                    "root": {"type": "string", "minLength": 1, "default": "workspace"},
                    "overwrite": {"type": "boolean", "default": False},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 30, "default": 20},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 52428800, "default": 52428800},
                    "max_redirects": {"type": "integer", "minimum": 0, "maximum": 3, "default": 3},
                    "transport": {"type": "string", "enum": ["direct", "system_loopback_proxy"], "default": "direct"},
                }, "required": ["url", "destination"], "additionalProperties": False,
            },
            risk_level="write_local",
            tags=("network", "https", "download", "file", "下载", "网络获取"),
            requires_confirmation=True,
        ),
        _descriptor(
            "core.network_diagnose", "network_diagnose", "Public DNS / TCP Diagnosis",
            "公共域名DNS解析可报告特殊用途IP及联网拦截状态；TCP仅连接公网地址，拒绝本机、私网和IP字面量目标。",
            {
                "type": "object", "properties": {
                    "host": {"type": "string", "minLength": 1, "maxLength": 253},
                    "kind": {"type": "string", "enum": ["dns", "tcp"], "default": "dns"},
                    "port": {"type": ["integer", "null"], "minimum": 1, "maximum": 65535, "default": None},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 30, "default": 5},
                }, "required": ["host"], "additionalProperties": False,
            },
            risk_level="read",
            tags=("network", "dns", "tcp", "connectivity", "network-diagnose", "域名解析", "网络诊断", "端口检查"),
        ),
    )


def core_index_descriptors() -> tuple[CapabilityDescriptor, ...]:
    """Optional QMD project-local Markdown index; all paths are root-governed."""
    location = {
        "root": {"type": "string", "minLength": 1, "default": "workspace"},
        "directory": {"type": "string", "minLength": 1},
    }
    return (
        _descriptor(
            "core.workspace_index_init", "workspace_index_init", "Initialize Local Workspace Index",
            "为已授权工作区创建.qmd本地Markdown索引并执行首次增量扫描；仅使用受控QMD引擎。",
            {"type": "object", "properties": location,
             "required": ["directory"], "additionalProperties": False},
            risk_level="write_local", requires_confirmation=True,
            tags=("workspace", "index", "qmd", "initialize", "建立索引"),
        ),
        _descriptor(
            "core.workspace_index_refresh", "workspace_index_refresh", "Refresh Workspace Index",
            "增量刷新已授权工作区的QMD Markdown索引；拒绝未审核的QMD配置。",
            {"type": "object", "properties": location,
             "required": ["directory"], "additionalProperties": False},
            risk_level="write_local", requires_confirmation=True,
            tags=("workspace", "index", "qmd", "refresh", "更新索引"),
        ),
        _descriptor(
            "core.workspace_index_status", "workspace_index_status", "Workspace Index Status",
            "读取指定工作区的本地QMD索引状态与覆盖范围。",
            {"type": "object", "properties": location,
             "required": ["directory"], "additionalProperties": False},
            risk_level="read",
            tags=("workspace", "index", "qmd", "status", "索引状态"),
        ),
        _descriptor(
            "core.workspace_index_search", "workspace_index_search", "Search Workspace Index",
            "基于授权工作区的本地QMD关键词索引搜索Markdown，返回校验后的相对路径和片段。",
            {"type": "object", "properties": {
                **location,
                "query": {"type": "string", "minLength": 1, "maxLength": 120},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            }, "required": ["directory", "query"], "additionalProperties": False},
            risk_level="read",
            tags=("workspace", "index", "qmd", "search", "检索索引", "文档搜索"),
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


def register_core_transaction_capabilities(
    registry: CapabilityRegistry,
    broker: CapabilityBroker,
    transaction_store: ActionTransactionStore,
    event_store: EventStore | None = None,
    observer_hooks: ObserverHookRuntime | None = None,
    gate_hooks: GateHookRuntime | None = None,
) -> None:
    """Register stable core governance capabilities and in-process handlers."""

    descriptors = [
        *core_routing_descriptors(),
        *core_transaction_descriptors(),
        *core_release_descriptors(),
        *core_artifact_descriptors(),
        *core_workspace_descriptors(),
        *core_network_descriptors(),
        *core_index_descriptors(),
        *core_local_tool_descriptors(),
    ]
    if event_store is not None:
        descriptors.extend(core_event_descriptors())
    if observer_hooks is not None:
        descriptors.extend(core_hook_descriptors())
    if gate_hooks is not None:
        descriptors.extend(core_gate_descriptors())
    registry.register_provider(
        "core",
        descriptors,
        enabled=True,
    )

    broker.register_internal_handler(
        "core.capability_route",
        lambda args: route_generic_request(
            args["tool"],
            args["arguments"],
            registry.snapshot(include_unavailable=True),
        ),
    )
    broker.register_internal_handler(
        "core.routing_audit",
        lambda args: audit_routing(
            registry.snapshot(
                include_unavailable=args.get("include_unavailable", True)
            ),
            routing_catalog(),
            steering_rules(),
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

    async def complete_external_handler(args: dict) -> dict:
        return await complete_external_capability_in_transaction(
            transaction_store,
            broker,
            transaction_id=args["transaction_id"],
            expected_revision=args["expected_revision"],
            step_id=args["step_id"],
        )

    broker.register_internal_handler(
        "core.transaction_complete_external",
        complete_external_handler,
    )
    broker.register_internal_handler(
        "core.git_refresh_index",
        lambda args: controlled_git_refresh_index(
            args["changes"],
            args["expected_head"],
            args.get("cwd", "."),
            "pla",
        ),
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
        "core.artifact_materialize",
        lambda args: controlled_materialize_artifact(
            args["artifact_id"],
            args["destination_path"],
            args.get("root", "workspace"),
            args.get("overwrite", False),
            args.get("expected_sha256"),
        ),
    )
    broker.register_internal_handler(
        "core.artifact_metadata",
        lambda args: controlled_artifact_metadata(args["artifact_id"]),
    )
    broker.register_internal_handler(
        "core.artifact_verify",
        lambda args: controlled_verify_artifact_provenance(
            args["artifact_id"],
            recursive=args.get("recursive", True),
        ),
    )
    broker.register_internal_handler(
        "core.artifact_gc_preview",
        lambda _args: controlled_artifact_gc(dry_run=True),
    )
    broker.register_internal_handler(
        "core.artifact_gc",
        lambda _args: controlled_artifact_gc(dry_run=False),
    )
    broker.register_internal_handler(
        "core.artifact_revoke",
        lambda args: controlled_revoke_artifact(args["artifact_id"]),
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
    # HTTP and TCP operations block on sockets; offload to avoid freezing
    # the MCP event loop. asyncio.to_thread also carries cancellation context.
    async def _network_probe_handler(args: dict) -> dict:
        return await asyncio.to_thread(controlled_http_probe, **args)

    async def _network_download_handler(args: dict) -> dict:
        return await asyncio.to_thread(controlled_http_download, **args)

    async def _network_diagnose_handler(args: dict) -> dict:
        return await asyncio.to_thread(controlled_network_diagnose, **args)

    broker.register_internal_handler("core.http_probe", _network_probe_handler)
    broker.register_internal_handler("core.http_download", _network_download_handler)
    broker.register_internal_handler("core.network_diagnose", _network_diagnose_handler)

    for name, function in (
        ("workspace_index_init", controlled_index_init),
        ("workspace_index_refresh", controlled_index_refresh),
        ("workspace_index_status", controlled_index_status),
        ("workspace_index_search", controlled_index_search),
    ):
        async def _index_handler(args: dict, function=function) -> dict:
            return await asyncio.to_thread(function, **args)
        broker.register_internal_handler(f"core.{name}", _index_handler)
    for tool_name in _CORE_LOCAL_TOOL_POLICIES:
        function = LOCAL_TOOL_FUNCTIONS[tool_name]
        broker.register_internal_handler(
            f"core.{tool_name}",
            lambda args, function=function: function(**args),
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
