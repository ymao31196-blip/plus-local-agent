from __future__ import annotations

import json
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from importlib.metadata import version
from dataclasses import asdict
from typing import Annotated, Literal

import mcp_types
from fastmcp import Context, FastMCP
from pydantic import Field

from agent_service import AgentState, run_agent
from decision_parser import DecisionParseError, parse_decision
from e2e_debug import E2EDebugTrace, protocol_mode
from generic_llm_reasoner import GenericLLMReasoner
from internal_tool_executor import (
    INTERNAL_TOOL_SCHEMAS,
    ActionRequest,
    InternalToolExecutor,
    execute_actions_request,
)
from local_tools import (
    AcceptanceBindingRequest,
    AcceptanceCheckRequest,
    GitStageRequest,
    VerificationSpec,
    ALLOWED_PROGRAMS,
    WORKSPACE,
    available_roots,
    apply_patch as internal_apply_patch,
    git_commit as internal_git_commit,
    git_diff as internal_git_diff,
    git_log as internal_git_log,
    git_show as internal_git_show,
    git_stage as internal_git_stage,
    git_status as internal_git_status,
    git_tag as internal_git_tag,
    git_push as internal_git_push,
    project_acceptance_evaluate as internal_project_acceptance_evaluate,
    project_acceptance_evaluations_get as internal_project_acceptance_evaluations_get,
    project_acceptance_get as internal_project_acceptance_get,
    project_acceptance_set as internal_project_acceptance_set,
    project_checkpoint as internal_project_checkpoint,
    project_decision_record as internal_project_decision_record,
    project_decisions_get as internal_project_decisions_get,
    project_evidence_get as internal_project_evidence_get,
    project_evidence_record as internal_project_evidence_record,
    project_state_get as internal_project_state_get,
    project_state_init as internal_project_state_init,
    project_state_update as internal_project_state_update,
    project_verifications_get as internal_project_verifications_get,
    project_verify_acceptance as internal_project_verify_acceptance,
    list_directory as internal_list_directory,
    read_text as internal_read_text,
    extract_document_text as internal_extract_document_text,
    replace_text as internal_replace_text,
    run_process as internal_run_process,
    run_powershell as internal_run_powershell,
    safe_path,
    search_text as internal_search_text,
    write_text as internal_write_text,
)
from mcp_sampling_backend import (
    MCPSamplingBackend,
    SAMPLING_KEY,
    SamplingRequired,
    SamplingUnsupported,
)
from task_store import TASK_STORE
from transaction_runtime import TRANSACTION_STORE
from transaction_action_envelope import invoke_capability_in_transaction
from changeset_manager import ChangeRequest, apply_changeset as internal_apply_changeset
from artifact_bridge import (
    artifact_gc as internal_artifact_gc,
    artifact_metadata as internal_artifact_metadata,
    export_artifact as internal_export_artifact,
    prepare_chunked_artifact as internal_prepare_chunked_artifact,
    read_artifact_bytes as internal_read_artifact_bytes,
    read_artifact_chunk as internal_read_artifact_chunk,
    revoke_artifact as internal_revoke_artifact,
    verify_artifact_provenance as internal_verify_artifact_provenance,
)
from capability_registry import CapabilityRegistry
from event_runtime import EVENT_STORE
from observer_hook_runtime import OBSERVER_HOOK_RUNTIME
from computer_use_indicator import computer_use_indicator_observer
from gate_hook_runtime import GATE_HOOK_RUNTIME
from mcp_client_manager import MCPClientManager
from capability_broker import CapabilityBroker
from core_capabilities import register_core_transaction_capabilities
from external_provider_runtime import ExternalProviderRuntime
from external_observer_runtime import ExternalObserverRuntime
from provider_runtime_capabilities import register_provider_runtime_capabilities
from windows_action_capabilities import register_windows_action_capabilities
from provider_doctor import provider_doctor as run_provider_doctor


OBSERVER_HOOK_RUNTIME.register(
    "computer-use-indicator",
    (
        "capability.before_invoke",
        "capability.succeeded",
        "capability.failed",
    ),
    computer_use_indicator_observer,
)
CAPABILITY_REGISTRY = CapabilityRegistry()
MCP_CLIENT_MANAGER = MCPClientManager(CAPABILITY_REGISTRY)
CAPABILITY_BROKER = CapabilityBroker(
    CAPABILITY_REGISTRY,
    MCP_CLIENT_MANAGER,
    EVENT_STORE,
    OBSERVER_HOOK_RUNTIME,
    GATE_HOOK_RUNTIME,
)
register_core_transaction_capabilities(
    CAPABILITY_REGISTRY,
    CAPABILITY_BROKER,
    TRANSACTION_STORE,
    EVENT_STORE,
    OBSERVER_HOOK_RUNTIME,
    GATE_HOOK_RUNTIME,
)
EXTERNAL_PROVIDER_RUNTIME = ExternalProviderRuntime(
    MCP_CLIENT_MANAGER,
    Path(__file__).resolve().parent,
)
EXTERNAL_PROVIDER_CONFIG = EXTERNAL_PROVIDER_RUNTIME.configure_initial()
EXTERNAL_OBSERVER_RUNTIME = ExternalObserverRuntime(
    OBSERVER_HOOK_RUNTIME,
    Path(__file__).resolve().parent,
)
EXTERNAL_OBSERVER_CONFIG = EXTERNAL_OBSERVER_RUNTIME.configure_initial()
register_provider_runtime_capabilities(
    CAPABILITY_REGISTRY,
    CAPABILITY_BROKER,
    EXTERNAL_PROVIDER_RUNTIME,
    EXTERNAL_OBSERVER_RUNTIME,
)
register_windows_action_capabilities(
    CAPABILITY_REGISTRY,
    CAPABILITY_BROKER,
)


@asynccontextmanager
async def _runtime_lifespan(_server):
    await MCP_CLIENT_MANAGER.discover_all()
    try:
        yield
    finally:
        await MCP_CLIENT_MANAGER.close_all_persistent_sessions()


mcp = FastMCP(
    "Local Agent Tools",
    version="1.6.0",
    lifespan=_runtime_lifespan,
)


@mcp.tool
def capability_search(
    query: str,
    provider_id: str | None = None,
    include_unavailable: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Search the runtime capability registry without invoking any capability."""
    return CAPABILITY_REGISTRY.search(
        query,
        provider_id=provider_id,
        include_unavailable=include_unavailable,
        limit=limit,
    )


@mcp.tool
def capability_describe(capability_id: str) -> dict:
    """Return the full descriptor and schemas for one registered capability."""
    return CAPABILITY_REGISTRY.describe(capability_id)


@mcp.tool
async def capability_invoke(
    capability_id: str,
    arguments: dict,
    confirmation: Literal["INVOKE"] | None = None,
) -> dict:
    """Invoke one registered capability after availability, policy, and schema checks."""
    return await CAPABILITY_BROKER.invoke(
        capability_id,
        arguments,
        confirmation=confirmation,
    )


@mcp.tool
async def provider_doctor(
    provider_id: str | None = None,
    live_probe: bool = False,
    force: bool = False,
) -> dict:
    """Inspect provider lifecycle, isolated environment, pinned versions, and optional live MCP health."""
    return await run_provider_doctor(
        MCP_CLIENT_MANAGER,
        Path(__file__).resolve().parent,
        provider_id=provider_id,
        live_probe=live_probe,
        force=force,
    )


# Experimental v0.9 Phase 0B transport probe only. This deliberately avoids
# introducing the Artifact Manager before the current ChatGPT host proves that
# MCP ResourceLink outputs are usable as host-managed artifacts.
TRANSPORT_PROBE_URI = "artifact://transport-probe/phase0b"
TRANSPORT_PROBE_PATH = "transport_probe/phase0b.txt"


@mcp.resource(
    TRANSPORT_PROBE_URI,
    name="PLA v0.9 Phase 0B transport probe",
    mime_type="text/plain",
)
def transport_probe_resource() -> bytes:
    target = safe_path(TRANSPORT_PROBE_PATH, "workspace")
    if not target.is_file():
        raise ValueError("Transport probe artifact is missing")
    return target.read_bytes()


@mcp.tool
def probe_artifact_resource_link() -> mcp_types.ResourceLink:
    """Experimental: return a ResourceLink for the v0.9 Phase 0B host handoff probe."""
    target = safe_path(TRANSPORT_PROBE_PATH, "workspace")
    if not target.is_file():
        raise ValueError("Transport probe artifact is missing")
    return mcp_types.ResourceLink(
        name=target.name, uri=TRANSPORT_PROBE_URI, mimeType="text/plain",
        size=target.stat().st_size,
    )


@mcp.resource(
    "artifact://bridge/{artifact_id}",
    name="PLA exported artifact",
    mime_type="application/octet-stream",
)
def artifact_bridge_resource(artifact_id: str) -> bytes:
    """Read a live immutable artifact snapshot after TTL and integrity checks."""
    return internal_read_artifact_bytes(artifact_id)


@mcp.resource(
    "artifact://bridge/{artifact_id}/chunk/{part_index}",
    name="PLA exported artifact chunk",
    mime_type="application/octet-stream",
)
def artifact_bridge_chunk_resource(artifact_id: str, part_index: int) -> bytes:
    """Read one bounded chunk from a live immutable artifact snapshot."""
    return internal_read_artifact_chunk(artifact_id, part_index)


@mcp.tool
def export_artifact_chunks(
    path: str,
    root: str = "workspace",
    mime_type: str | None = None,
    ttl_seconds: Annotated[int, Field(ge=1, le=3600)] = 600,
    chunk_bytes: Annotated[int, Field(ge=1, le=6_500_000)] = 6_000_000,
) -> list[mcp_types.ResourceLink]:
    """Experimental: export a large file as bounded ordered chunks for host-side reassembly."""
    metadata = internal_prepare_chunked_artifact(
        path, root, mime_type, ttl_seconds, chunk_bytes,
    )
    part_count = metadata["part_count"]
    stem = metadata["name"]
    return [
        mcp_types.ResourceLink(
            name=f"{stem}.part{index + 1:03d}of{part_count:03d}",
            uri=f'artifact://bridge/{metadata["artifact_id"]}/chunk/{index}',
            description=(
                f'chunk {index + 1}/{part_count}; full SHA-256 {metadata["sha256"]}; '
                f'expires {metadata["expires_at"]}'
            ),
            mimeType="application/octet-stream",
            size=min(
                metadata["chunk_bytes"],
                metadata["size"] - index * metadata["chunk_bytes"],
            ),
        )
        for index in range(part_count)
    ]


@mcp.tool
def export_artifact(
    path: str,
    root: str = "workspace",
    mime_type: str | None = None,
    ttl_seconds: Annotated[int, Field(ge=1, le=3600)] = 600,
) -> mcp_types.ResourceLink:
    """Export a local file as an immutable short-lived MCP ResourceLink for host/plugin handoff."""
    metadata = internal_export_artifact(path, root, mime_type, ttl_seconds)
    return mcp_types.ResourceLink(
        name=metadata["name"],
        uri=f'artifact://bridge/{metadata["artifact_id"]}',
        description=(
            f'SHA-256 {metadata["sha256"]}; expires {metadata["expires_at"]}'
        ),
        mimeType=metadata["mime_type"],
        size=metadata["size"],
    )


@mcp.tool
def artifact_metadata(artifact_id: str) -> dict:
    """Return metadata for a live exported artifact without returning its bytes."""
    return internal_artifact_metadata(artifact_id)


@mcp.tool
def artifact_verify(
    artifact_id: str,
    recursive: bool = True,
) -> dict:
    """Verify artifact payload integrity and recorded provenance without returning bytes."""
    return internal_verify_artifact_provenance(
        artifact_id,
        recursive=recursive,
    )


@mcp.tool
def artifact_gc(
    dry_run: bool = True,
    confirmation: Literal["GC"] | None = None,
) -> dict:
    """Inspect expired artifact GC candidates; deletion requires dry_run=false and exact GC confirmation."""
    if not dry_run and confirmation != "GC":
        raise PermissionError(
            "artifact_gc deletion requires confirmation='GC'"
        )
    return internal_artifact_gc(dry_run=dry_run)


@mcp.tool
def revoke_artifact(artifact_id: str) -> dict:
    """Revoke an exported artifact and delete its immutable snapshot."""
    return internal_revoke_artifact(artifact_id)


@mcp.tool
def list_directory(path: str = ".", root: str = "workspace") -> list[str]:
    """列出指定受控 root 内的文件和子目录。"""
    return internal_list_directory(path, root)


@mcp.tool
def read_text(
    path: str, start_line: int = 1, end_line: int = 400,
    root: str = "workspace",
) -> dict:
    """读取指定受控 root 内 UTF-8 文本文件的指定行。"""
    return internal_read_text(path, start_line, end_line, root)


@mcp.tool
def extract_document_text(
    path: str,
    start: int = 1,
    end: int | None = None,
    max_chars: int = 20_000,
    root: str = "workspace",
) -> dict:
    """按文件类型提取本地文档文本；PDF按页、DOCX按段落、UTF-8文本按行读取，并返回结构化元数据。"""
    return internal_extract_document_text(path, start, end, max_chars, root)


@mcp.tool
def write_text(
    path: str, content: str, expected_sha256: str | None = None,
    root: str = "workspace",
) -> dict:
    """创建或覆盖单个文本文件；同一逻辑修改涉及 2 个及以上已有文件时优先使用 apply_changeset。"""
    return internal_write_text(path, content, expected_sha256, root)


@mcp.tool
def replace_text(
    path: str,
    old: str,
    new: str,
    count: int = 1,
    expected_sha256: str | None = None,
    root: str = "workspace",
) -> dict:
    """精确替换单个已有文本文件；同一逻辑修改涉及 2 个及以上已有文件时优先使用 apply_changeset。"""
    return internal_replace_text(path, old, new, count, expected_sha256, root)


@mcp.tool
def search_text(
    query: str,
    path: str = ".",
    glob: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 100,
    root: str = "workspace",
) -> dict:
    """在指定受控 root 内搜索有限数量的文本匹配。"""
    return internal_search_text(query, path, glob, case_sensitive, max_results, root)


@mcp.tool
def git_status(cwd: str = ".", root: str = "workspace") -> dict:
    """返回受控 root 内 Git 仓库的结构化只读状态。"""
    return internal_git_status(cwd, root)


@mcp.tool
def git_diff(
    cwd: str = ".", staged: bool = False, path: str | None = None,
    root: str = "workspace",
) -> dict:
    """返回受控 root 内 Git 仓库的只读 diff；禁用 external diff 与 textconv。"""
    return internal_git_diff(cwd, staged, path, root)


@mcp.tool
def git_log(
    cwd: str = ".", limit: Annotated[int, Field(ge=1, le=100)] = 20, path: str | None = None,
    root: str = "workspace",
) -> dict:
    """返回受控 root 内 Git 仓库的结构化提交历史，最多 100 条。"""
    return internal_git_log(cwd, limit, path, root)


@mcp.tool
def git_show(
    revision: str = "HEAD", cwd: str = ".", path: str | None = None,
    root: str = "workspace",
) -> dict:
    """返回一个已验证 commit 的元数据和受限 patch；禁用 external diff 与 textconv。"""
    return internal_git_show(revision, cwd, path, root)

@mcp.tool
def git_stage(
    changes: list[GitStageRequest],
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict:
    """仅stage显式普通文件（已跟踪或未忽略的新文件）的已校验当前字节；要求expected_head和文件SHA匹配，不运行clean filters。"""
    return internal_git_stage(changes, expected_head, cwd, root)


@mcp.tool
def git_commit(
    message: str,
    paths: list[str],
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict:
    """仅提交显式且已精确staged的普通文件新增/修改；要求expected_head匹配，不自动stage、不运行hooks、不amend、不push。"""
    return internal_git_commit(message, paths, expected_head, cwd, root)


@mcp.tool
def git_tag(
    tag: str,
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict:
    """在clean仓库的精确expected HEAD上原子创建一个lightweight tag；拒绝覆盖已有tag。"""
    return internal_git_tag(tag, expected_head, cwd, root)


@mcp.tool
def git_push(
    remote: str,
    branch: str,
    expected_head: str,
    tags: list[str] | None = None,
    confirmation: Literal["PUSH"] | None = None,
    cwd: str = ".",
    root: str = "workspace",
) -> dict:
    """原子推送当前精确branch与显式tags到已配置remote；禁force并要求confirmation='PUSH'。"""
    return internal_git_push(
        remote, branch, expected_head, tags, confirmation, cwd, root
    )


@mcp.tool
def project_state_init(
    project_path: str = ".",
    project_name: str | None = None,
    objective: str = "",
    root: str = "workspace",
) -> dict:
    """为已有项目目录初始化 .project-agent 持久状态；不会把任何状态标记为 verified。"""
    return internal_project_state_init(project_path, project_name, objective, root)


@mcp.tool
def project_state_get(
    project_path: str = ".", root: str = "workspace",
) -> dict:
    """读取项目持久状态和最近 checkpoint 名称，不修改项目。"""
    return internal_project_state_get(project_path, root)


@mcp.tool
def project_state_update(
    expected_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".",
    objective: str | None = None,
    lifecycle: str | None = None,
    current_phase: str | None = None,
    next_action: str | None = None,
    blockers: list[str] | None = None,
    root: str = "workspace",
) -> dict:
    """按expected_revision更新明确项目状态字段；普通更新不能直接设置verified，且会重新打开已verified状态。"""
    return internal_project_state_update(
        expected_revision, project_path, objective, lifecycle,
        current_phase, next_action, blockers, root,
    )


@mcp.tool
def project_checkpoint(
    label: str,
    summary: str,
    expected_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".",
    checks: list[str] | None = None,
    root: str = "workspace",
) -> dict:
    """为当前revision创建不可覆盖的unverified项目状态快照。"""
    return internal_project_checkpoint(label, summary, expected_revision, project_path, checks, root)


@mcp.tool
def project_decision_record(
    title: str, decision: str, rationale: str,
    expected_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".", root: str = "workspace",
) -> dict:
    """追加与expected_revision绑定的持久决策记录；决策日志本身不是验证证据。"""
    return internal_project_decision_record(title, decision, rationale, expected_revision, project_path, root)


@mcp.tool
def project_decisions_get(
    project_path: str = ".",
    max_chars: Annotated[int, Field(ge=1, le=20000)] = 20000,
    root: str = "workspace",
) -> dict:
    """读取DECISIONS.md的有界尾部，不修改项目状态。"""
    return internal_project_decisions_get(project_path, max_chars, root)


@mcp.tool
def project_evidence_record(
    kind: Literal["test", "command", "artifact", "metric", "observation", "manual"],
    status: Literal["pass", "fail", "info"],
    summary: str, source: str,
    expected_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".", details: str = "",
    artifact_sha256: str | None = None,
    verification: VerificationSpec | None = None,
    root: str = "workspace",
) -> dict:
    """追加与expected_revision绑定的结构化unverified证据；verification可声明file_sha256或pytest独立重算方式。"""
    return internal_project_evidence_record(
        kind, status, summary, source, expected_revision,
        project_path, details, artifact_sha256, verification, root,
    )


@mcp.tool
def project_evidence_get(
    project_path: str = ".",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    kind: Literal["test", "command", "artifact", "metric", "observation", "manual"] | None = None,
    status: Literal["pass", "fail", "info"] | None = None,
    root: str = "workspace",
) -> dict:
    """读取最近结构化证据，可按kind/status过滤；记录仍为unverified。"""
    return internal_project_evidence_get(project_path, limit, kind, status, root)


@mcp.tool
def project_acceptance_set(
    title: str,
    checks: list[AcceptanceCheckRequest],
    expected_state_revision: Annotated[int, Field(ge=1)],
    expected_contract_revision: Annotated[int, Field(ge=0)] = 0,
    project_path: str = ".",
    root: str = "workspace",
) -> dict:
    """创建或CAS修订Acceptance Contract；定义完成条件本身不代表项目已通过。"""
    return internal_project_acceptance_set(
        title, checks, expected_state_revision, expected_contract_revision,
        project_path, root,
    )


@mcp.tool
def project_acceptance_get(
    project_path: str = ".", root: str = "workspace",
) -> dict:
    """读取当前Acceptance Contract，不修改项目。"""
    return internal_project_acceptance_get(project_path, root)


@mcp.tool
def project_acceptance_evaluate(
    bindings: list[AcceptanceBindingRequest],
    expected_state_revision: Annotated[int, Field(ge=1)],
    expected_contract_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".",
    root: str = "workspace",
) -> dict:
    """根据当前state绑定的evidence IDs确定性计算pass/fail/incomplete；结果仍为unverified。"""
    return internal_project_acceptance_evaluate(
        bindings, expected_state_revision, expected_contract_revision,
        project_path, root,
    )


@mcp.tool
def project_acceptance_evaluations_get(
    project_path: str = ".",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    root: str = "workspace",
) -> dict:
    """读取最近Acceptance评估摘要；pass并不等于verified。"""
    return internal_project_acceptance_evaluations_get(project_path, limit, root)



@mcp.tool
def project_verify_acceptance(
    evaluation_id: str,
    expected_evaluation_sha256: str,
    expected_state_revision: Annotated[int, Field(ge=1)],
    expected_contract_revision: Annotated[int, Field(ge=1)],
    project_path: str = ".",
    root: str = "workspace",
) -> dict:
    """独立重算Acceptance所用的file SHA或pytest证据；仅成功Verifier可将frozen状态晋级为verified。"""
    return internal_project_verify_acceptance(
        evaluation_id, expected_evaluation_sha256,
        expected_state_revision, expected_contract_revision,
        project_path, root,
    )


@mcp.tool
def project_verifications_get(
    project_path: str = ".",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    root: str = "workspace",
) -> dict:
    """读取最近独立验证记录。"""
    return internal_project_verifications_get(project_path, limit, root)


@mcp.tool
def run_process(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 120,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    root: str = "workspace",
) -> dict:
    """从指定受控 root 运行受允许的程序。"""
    return internal_run_process(program, args, cwd, timeout, workdir, env, stdin, root)


@mcp.tool
def run_powershell(
    command: str,
    parameters: dict | None = None,
    workdir: str = ".",
    timeout: int = 30,
    root: str = "workspace",
) -> dict:
    """运行单个 allow-listed PowerShell cmdlet；不接受脚本文本。"""
    return internal_run_powershell(command, parameters, workdir, timeout, root)


@mcp.tool
def apply_patch(
    path: str, patch: str, expected_sha256: str | None = None,
    root: str = "workspace",
) -> dict:
    """原子应用单文件 unified text diff；多已有文件的同一逻辑修改优先使用 apply_changeset。"""
    return internal_apply_patch(path, patch, expected_sha256, root)


@mcp.tool
def apply_changeset(
    changes: list[ChangeRequest], root: str = "workspace",
) -> dict:
    """Preferred transaction for one logical edit spanning 2+ existing UTF-8 files; requires hashes and rolls back replaced files on failure."""
    return internal_apply_changeset(changes, root)


@mcp.tool
def cancel_task(task_id: str) -> dict:
    """Request cancellation of this runtime's task; no PID input is accepted."""
    return TASK_STORE.cancel(task_id)


@mcp.tool
def transaction_create(
    goal: str,
    steps: list[dict],
    metadata: dict | None = None,
) -> dict:
    """Create a durable multi-step transaction plan without executing any action."""
    return TRANSACTION_STORE.create(goal, steps, metadata)


@mcp.tool
def transaction_get(transaction_id: str) -> dict:
    """Read one durable action transaction with its bounded event history."""
    return TRANSACTION_STORE.get(transaction_id)


@mcp.tool
def transaction_checkpoint(
    transaction_id: str,
    expected_revision: int,
    step_id: str,
    outcome: Literal[
        "started", "succeeded", "failed", "verified", "rolled_back", "skipped"
    ],
    summary: str,
    evidence: dict | None = None,
) -> dict:
    """Record one transaction step outcome using optimistic revision concurrency."""
    return TRANSACTION_STORE.checkpoint(
        transaction_id,
        expected_revision,
        step_id,
        outcome,
        summary,
        evidence,
    )


@mcp.tool
def transaction_finalize(
    transaction_id: str,
    expected_revision: int,
    decision: Literal["commit", "abort", "rolled_back"],
    summary: str,
) -> dict:
    """Commit, abort, or close a fully rolled-back transaction after state checks."""
    return TRANSACTION_STORE.finalize(
        transaction_id,
        expected_revision,
        decision,
        summary,
    )


@mcp.tool
async def transaction_invoke_capability(
    transaction_id: str,
    expected_revision: int,
    step_id: str,
    capability_id: str,
    arguments: dict,
    confirmation: Literal["INVOKE"] | None = None,
) -> dict:
    """Invoke one caller-selected capability and bind its outcome to one transaction step."""
    return await invoke_capability_in_transaction(
        TRANSACTION_STORE,
        CAPABILITY_BROKER,
        transaction_id=transaction_id,
        expected_revision=expected_revision,
        step_id=step_id,
        capability_id=capability_id,
        arguments=arguments,
        confirmation=confirmation,
    )


@mcp.tool
def execute_actions(
    actions: list[ActionRequest], stop_on_error: bool = True,
) -> dict:
    """顺序执行一组明确的本地 Action；默认遇到失败即停止。"""
    return execute_actions_request(actions, stop_on_error)


@mcp.tool
def submit_task(
    actions: list[ActionRequest] | None = None,
    tool: str | None = None,
    arguments: dict | None = None,
    stop_on_error: bool = True,
) -> dict:
    """提交结构化任务到持久化 Task Store，由本地后台 worker 执行。"""
    has_actions = actions is not None
    has_tool = tool is not None
    if has_actions == has_tool:
        raise ValueError("Provide exactly one of actions or tool")
    if not isinstance(stop_on_error, bool):
        raise TypeError("stop_on_error must be a boolean")
    if has_actions:
        # Validate the complete batch before accepting background work. This
        # deliberately performs no local action.
        if not isinstance(actions, list):
            raise TypeError("actions must be a list")
        if not actions:
            raise ValueError("actions cannot be empty")
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise TypeError(f"actions[{index}] must be an object")
            if not isinstance(action.get("tool"), str) or not action["tool"]:
                raise ValueError(f"actions[{index}].tool must be a non-empty string")
            if not isinstance(action.get("arguments"), dict):
                raise TypeError(f"actions[{index}].arguments must be an object")
        request = {"actions": actions, "stop_on_error": stop_on_error}
    else:
        if not isinstance(tool, str) or not tool:
            raise ValueError("tool must be a non-empty string")
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        request = {"tool": tool, "arguments": arguments}
    return TASK_STORE.submit(request)


@mcp.tool
def task_result(
    task_id: str, cursor: int | None = None, wait_seconds: float = 0.0,
) -> dict:
    """读取任务状态；可在服务端等待新事件、终态或超时。"""
    return TASK_STORE.get(task_id, cursor, wait_seconds)


def _load_state(raw_state: str | None) -> AgentState:
    if raw_state is None:
        return AgentState()
    value = json.loads(raw_state)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Invalid agent request state")
    return AgentState.from_dict(value["agent"])


def _dump_state(state: AgentState) -> str:
    return json.dumps(
        {"version": 1, "agent": state.to_dict()},
        ensure_ascii=False, separators=(",", ":"),
    )


def _sampling_supported(ctx: Context) -> bool | str:
    session = getattr(ctx, "session", None)
    capabilities = getattr(session, "client_capabilities", None)
    if capabilities is None:
        return "unknown"
    return getattr(capabilities, "sampling", None) is not None


@mcp.tool
async def diagnose_client(ctx: Context) -> dict:
    """Return read-only MCP protocol and client sampling diagnostics."""
    trace = E2EDebugTrace.start(ctx, "diagnose_client")
    sampling_supported = _sampling_supported(ctx)
    mode = protocol_mode(ctx)
    result = {
        "fastmcp_version": version("fastmcp"),
        "mcp_version": version("mcp"),
        "protocol_mode": mode,
        "sampling_supported": sampling_supported,
        # Legacy sampling can be inferred from its declared back-channel. In
        # MRTR mode, a normal tool call cannot prove that input_required will
        # be resumed, so probe_sampling is the authoritative readiness check.
        "agent_sampling_ready": bool(
            sampling_supported is True and mode == "backchannel"
        ),
        "run_agent_task_available": True,
        "run_agent_task_experimental": True,
        "mainline": "chatgpt_native_agent_loop",
        "available_roots": available_roots(),
    }
    trace.emit("final_status", status="completed")
    return result


PROBE_PROMPT = """Return exactly this JSON object and nothing else:
{"action":"finish","arguments":{}}"""


@mcp.tool
async def probe_sampling(ctx: Context) -> dict | mcp_types.InputRequiredResult:
    """Perform one client-provided sampling round without local tool access."""
    trace = E2EDebugTrace.start(ctx, "probe_sampling")
    if ctx.request_state is not None:
        trace.emit("request_state_resumed")
    backend = MCPSamplingBackend(ctx, max_tokens=100, trace=trace)
    try:
        raw = await backend.generate(PROBE_PROMPT)
        decision = parse_decision(raw)
        if decision != {"action": "finish", "arguments": {}}:
            raise DecisionParseError(
                "Probe decision must be exactly the requested finish decision"
            )
        result = {"status": "success", "sampling_calls": 1, "decision": decision}
        trace.emit("final_status", status="success")
        return result
    except SamplingRequired as required:
        trace.emit("input_required", sampling_sequence=1)
        return mcp_types.InputRequiredResult(
            input_requests={SAMPLING_KEY: required.request},
            request_state=json.dumps(
                {"version": 1, "kind": "probe_sampling"}, separators=(",", ":")
            ),
        )
    except Exception as exc:
        trace.emit("final_status", status="error", error_type=type(exc).__name__)
        return {
            "status": "error",
            "sampling_calls": 0 if isinstance(exc, SamplingUnsupported) else 1,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


@mcp.tool
async def run_agent_task(
    task: str,
    ctx: Context,
    max_steps: int = 10,
    allowed_actions: list[str] | None = None,
) -> dict | mcp_types.InputRequiredResult:
    """Run the local agent using only model sampling supplied by this MCP client."""
    trace = E2EDebugTrace.start(ctx, "run_agent_task")
    state = _load_state(ctx.request_state)
    if ctx.request_state is not None:
        trace.emit("request_state_resumed")
    tools = INTERNAL_TOOL_SCHEMAS
    if allowed_actions is not None:
        known = {tool["name"] for tool in INTERNAL_TOOL_SCHEMAS}
        unknown = sorted(set(allowed_actions) - known)
        if unknown:
            raise ValueError(f"Unknown allowed_actions: {unknown}")
        allowed = set(allowed_actions)
        tools = [tool for tool in INTERNAL_TOOL_SCHEMAS if tool["name"] in allowed]
    backend = MCPSamplingBackend(
        ctx,
        trace=trace,
        sampling_sequence=state.sampling_calls + 1,
    )
    reasoner = GenericLLMReasoner(backend)
    try:
        result = await run_agent(
            task=task,
            reasoner=reasoner,
            session=InternalToolExecutor(trace),
            tools=tools,
            max_steps=max_steps,
            state=state,
            capture_errors=True,
        )
        trace.emit("final_status", status=result.status)
        return asdict(result)
    except SamplingRequired as required:
        trace.emit("input_required", sampling_sequence=state.sampling_calls + 1)
        return mcp_types.InputRequiredResult(
            input_requests={SAMPLING_KEY: required.request},
            request_state=_dump_state(state),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Agent MCP server")
    parser.add_argument("--http", action="store_true", help="Use Streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    # Storage failure remains available as structured task-tool errors; other
    # local capabilities can still start. Recovery never replays old requests.
    TASK_STORE.initialize()
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port, path=args.path)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
