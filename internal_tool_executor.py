"""Direct executor for the server's local tools; no self-MCP connection."""

from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from typing import Any

from typing_extensions import TypedDict

from local_tools import LOCAL_TOOL_FUNCTIONS
from e2e_debug import E2EDebugTrace
from runtime_context import CURRENT, checkpoint, observe


EXECUTABLE_LOCAL_TOOLS = frozenset(LOCAL_TOOL_FUNCTIONS)

# Batch actions and the retained experimental agent use the conservative core
# set. Structured PowerShell and patching remain individually callable through
# the same executor (including submit_task) but cannot be composed in a batch.
ACTION_LOCAL_TOOLS = frozenset({
    "list_directory", "read_text", "extract_document_text", "write_text", "replace_text", "search_text",
    "git_status", "git_diff", "git_log", "git_show",
    "project_state_get", "project_decisions_get", "project_evidence_get",
    "project_acceptance_get", "project_acceptance_evaluations_get",
    "project_verifications_get",
    "run_process",
})


class ActionRequest(TypedDict):
    tool: str
    arguments: dict[str, Any]


INTERNAL_TOOL_SCHEMAS = [
    {"name": "list_directory", "description": "List files inside an allowed root.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "read_text", "description": "Read selected lines from a UTF-8 text file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer", "default": 1},
         "end_line": {"type": "integer", "default": 400},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["path"]}},
    {"name": "extract_document_text", "description": "Extract bounded text from PDF pages, DOCX paragraphs, or UTF-8 text lines.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "start": {"type": "integer", "default": 1, "minimum": 1},
         "end": {"type": ["integer", "null"], "default": None, "minimum": 1},
         "max_chars": {"type": "integer", "default": 20000, "minimum": 1, "maximum": 100000},
         "root": {"type": "string", "enum": ["workspace", "pla", "rerun_thesis"], "default": "workspace"}},
         "required": ["path"]}},
    {"name": "write_text", "description": "Create or overwrite one UTF-8 text file; use apply_changeset instead for one logical edit spanning 2+ existing files.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"},
         "expected_sha256": {"type": ["string", "null"]},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["path", "content"]}},
    {"name": "replace_text", "description": "Replace exact text in one existing file; use apply_changeset instead for one logical edit spanning 2+ existing files.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "old": {"type": "string"},
         "new": {"type": "string"}, "count": {"type": "integer", "default": 1},
         "expected_sha256": {"type": ["string", "null"]},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["path", "old", "new"]}},
    {"name": "apply_changeset", "description": "Transactionally apply one logical edit to existing UTF-8 files; prefer this for 2+ existing files. Every file requires its expected SHA-256.",
     "input_schema": {"type": "object", "properties": {
         "changes": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
             "type": "object", "properties": {
                 "path": {"type": "string"},
                 "expected_sha256": {"type": "string"},
                 "patch": {"type": "string"},
             },
             "required": ["path", "expected_sha256", "patch"],
             "additionalProperties": False,
         }},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["changes"]}},
    {"name": "search_text", "description": "Search bounded text matches inside an allowed root.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "path": {"type": "string", "default": "."},
         "glob": {"type": ["string", "null"]},
         "case_sensitive": {"type": "boolean", "default": False},
         "max_results": {"type": "integer", "default": 100},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["query"]}},
    {"name": "git_status", "description": "Return structured read-only Git status for a repository contained by an allowed root.",
     "input_schema": {"type": "object", "properties": {
         "cwd": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "git_diff", "description": "Return bounded read-only Git diff; external diff and textconv execution are disabled.",
     "input_schema": {"type": "object", "properties": {
         "cwd": {"type": "string", "default": "."},
         "staged": {"type": "boolean", "default": False},
         "path": {"type": ["string", "null"], "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "git_log", "description": "Return structured recent Git commit history for a repository contained by an allowed root.",
     "input_schema": {"type": "object", "properties": {
         "cwd": {"type": "string", "default": "."},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "path": {"type": ["string", "null"], "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "git_show", "description": "Return metadata and bounded patch for one verified Git commit; external diff and textconv execution are disabled.",
     "input_schema": {"type": "object", "properties": {
         "revision": {"type": "string", "default": "HEAD"},
         "cwd": {"type": "string", "default": "."},
         "path": {"type": ["string", "null"], "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "git_stage", "description": "Stage exact current bytes for explicit already-tracked regular files. Requires full expected HEAD and SHA-256 for every file; repository clean filters are disabled.",
     "input_schema": {"type": "object", "properties": {
         "changes": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
             "type": "object", "properties": {
                 "path": {"type": "string"},
                 "expected_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
             },
             "required": ["path", "expected_sha256"],
             "additionalProperties": False,
         }},
         "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
         "cwd": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["changes", "expected_head"]},
    },
    {"name": "git_commit", "description": "Create one structured commit from an exact already-staged set of explicit regular files. Requires the full expected HEAD and never stages files, runs hooks, amends, merges, or pushes.",
     "input_schema": {"type": "object", "properties": {
         "message": {"type": "string"},
         "paths": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "string"}},
         "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
         "cwd": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["message", "paths", "expected_head"]}},
    {"name": "git_tag", "description": "Atomically create one lightweight Git tag at the exact clean expected HEAD; existing tags are never overwritten.",
     "input_schema": {"type": "object", "properties": {
         "tag": {"type": "string"},
         "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
         "cwd": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["tag", "expected_head"]}},
    {"name": "git_push", "description": "Atomically push the exact current branch and explicit lightweight tags to one existing named remote. Force is unavailable and confirmation='PUSH' is required.",
     "input_schema": {"type": "object", "properties": {
         "remote": {"type": "string"},
         "branch": {"type": "string"},
         "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
         "tags": {"type": ["array", "null"], "maxItems": 8, "items": {"type": "string"}, "default": None},
         "confirmation": {"type": ["string", "null"], "enum": ["PUSH", None], "default": None},
         "cwd": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["remote", "branch", "expected_head"]}},
    {"name": "project_state_init", "description": "Initialize durable .project-agent state for an existing project directory; does not mark anything verified.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "project_name": {"type": ["string", "null"], "default": None},
         "objective": {"type": "string", "default": ""},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_state_get", "description": "Read durable project state and recent checkpoint names without mutating the project.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_state_update", "description": "CAS-update explicit project state fields using expected_revision; verified lifecycle is intentionally unavailable.",
     "input_schema": {"type": "object", "properties": {
         "expected_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "objective": {"type": ["string", "null"], "default": None},
         "lifecycle": {"type": ["string", "null"], "enum": ["candidate", "computed", "frozen", "deprecated", None], "default": None},
         "current_phase": {"type": ["string", "null"], "default": None},
         "next_action": {"type": ["string", "null"], "default": None},
         "blockers": {"type": ["array", "null"], "items": {"type": "string"}, "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["expected_revision"]}},
    {"name": "project_checkpoint", "description": "Create an immutable unverified snapshot of the current project state at an expected revision.",
     "input_schema": {"type": "object", "properties": {
         "label": {"type": "string"},
         "summary": {"type": "string"},
         "expected_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "checks": {"type": ["array", "null"], "items": {"type": "string"}, "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["label", "summary", "expected_revision"]}},
    {"name": "project_decision_record", "description": "Append a durable decision bound to the current expected project-state revision; decisions are not verification evidence.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "decision": {"type": "string"}, "rationale": {"type": "string"},
         "expected_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["title", "decision", "rationale", "expected_revision"]}},
    {"name": "project_decisions_get", "description": "Read the bounded tail of the durable decisions log without mutating project state.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "max_chars": {"type": "integer", "default": 20000, "minimum": 1, "maximum": 20000},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_evidence_record", "description": "Append an unverified structured evidence record bound to the expected project-state revision.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["test", "command", "artifact", "metric", "observation", "manual"]},
         "status": {"type": "string", "enum": ["pass", "fail", "info"]},
         "summary": {"type": "string"}, "source": {"type": "string"},
         "expected_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "details": {"type": "string", "default": ""},
         "artifact_sha256": {"type": ["string", "null"], "default": None},
         "verification": {"anyOf": [
             {"type": "object", "properties": {
                 "type": {"const": "file_sha256"}, "path": {"type": "string"}
             }, "required": ["type", "path"], "additionalProperties": False},
             {"type": "object", "properties": {
                 "type": {"const": "pytest"},
                 "args": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
                 "cwd": {"type": "string"},
                 "timeout": {"type": "integer", "minimum": 1, "maximum": 300}
             }, "required": ["type", "args", "cwd", "timeout"], "additionalProperties": False},
             {"type": "null"}
         ], "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["kind", "status", "summary", "source", "expected_revision"]}},
    {"name": "project_evidence_get", "description": "Read recent structured evidence records with optional kind/status filters; records remain unverified.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "kind": {"type": ["string", "null"], "enum": ["test", "command", "artifact", "metric", "observation", "manual", None], "default": None},
         "status": {"type": ["string", "null"], "enum": ["pass", "fail", "info", None], "default": None},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_acceptance_set", "description": "Create or CAS-revise ACCEPTANCE.yaml. Checks define completion criteria and allowed evidence kinds; setting a contract does not verify the project.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "checks": {"type": "array", "minItems": 1, "maxItems": 50, "items": {
             "type": "object", "properties": {
                 "id": {"type": "string"}, "description": {"type": "string"},
                 "evidence_kinds": {"type": "array", "items": {"type": "string", "enum": ["test", "command", "artifact", "metric", "observation", "manual"]}}
             }, "required": ["id", "description", "evidence_kinds"], "additionalProperties": False}},
         "expected_state_revision": {"type": "integer", "minimum": 1},
         "expected_contract_revision": {"type": "integer", "minimum": 0, "default": 0},
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["title", "checks", "expected_state_revision"]}},
    {"name": "project_acceptance_get", "description": "Read the active acceptance contract without mutating project state.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_acceptance_evaluate", "description": "Deterministically assess every acceptance check from bound current-state evidence IDs and write an immutable unverified evaluation.",
     "input_schema": {"type": "object", "properties": {
         "bindings": {"type": "array", "minItems": 1, "maxItems": 50, "items": {
             "type": "object", "properties": {
                 "check_id": {"type": "string"},
                 "evidence_ids": {"type": "array", "maxItems": 20, "items": {"type": "string"}}
             }, "required": ["check_id", "evidence_ids"], "additionalProperties": False}},
         "expected_state_revision": {"type": "integer", "minimum": 1},
         "expected_contract_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["bindings", "expected_state_revision", "expected_contract_revision"]}},
    {"name": "project_acceptance_evaluations_get", "description": "Read recent immutable acceptance evaluation summaries. A pass remains unverified until a future independent verifier promotes it.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "project_verify_acceptance", "description": "Independently re-verify a pass acceptance evaluation using structured file_sha256/pytest evidence. Requires frozen state; only a successful verifier may promote lifecycle to verified.",
     "input_schema": {"type": "object", "properties": {
         "evaluation_id": {"type": "string"},
         "expected_evaluation_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
         "expected_state_revision": {"type": "integer", "minimum": 1},
         "expected_contract_revision": {"type": "integer", "minimum": 1},
         "project_path": {"type": "string", "default": "."},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["evaluation_id", "expected_evaluation_sha256", "expected_state_revision", "expected_contract_revision"]}},
    {"name": "project_verifications_get", "description": "Read recent independent verification records without mutating project state.",
     "input_schema": {"type": "object", "properties": {
         "project_path": {"type": "string", "default": "."},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}}}},
    {"name": "run_process", "description": "Run an allow-listed process inside an allowed root.",
     "input_schema": {"type": "object", "properties": {
         "program": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
         "cwd": {"type": "string", "default": "."},
         "workdir": {"type": ["string", "null"]},
         "timeout": {"type": "integer", "default": 120},
         "env": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
         "stdin": {"type": ["string", "null"]},
         "root": {"type": "string", "enum": ["workspace", "pla"], "default": "workspace"}},
         "required": ["program"]}},
]


@dataclass
class InternalToolResult:
    is_error: bool
    structured_content: Any
    content: list[Any]


@dataclass
class LocalToolResult:
    tool: str
    ok: bool
    result: Any = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _execute_local_tool(tool_name: str, arguments: dict[str, Any]) -> LocalToolResult:
    """Execute one explicitly allowed local tool through the shared boundary."""
    if tool_name not in EXECUTABLE_LOCAL_TOOLS:
        return LocalToolResult(
            tool_name, False,
            error={"type": "UnknownLocalTool", "message": f"Unknown local tool: {tool_name}"},
        )
    if not isinstance(arguments, dict):
        return LocalToolResult(
            tool_name, False,
            error={"type": "ValidationError", "message": "arguments must be an object"},
        )
    function = LOCAL_TOOL_FUNCTIONS[tool_name]
    try:
        checkpoint()
        inspect.signature(function).bind(**arguments)
        value = function(**arguments)
        if inspect.isawaitable(value):
            raise TypeError("Async local tools are not supported by the synchronous executor")
        if tool_name == "apply_changeset" and value.get("status") != "completed":
            return LocalToolResult(tool_name, False, value, value["error"])
        if tool_name in {"run_process", "run_powershell"} and isinstance(value, dict):
            if value.get("timeout") is True:
                return LocalToolResult(
                    tool_name, False, value,
                    {"type": "ProcessTimeout", "message": "Process timed out"},
                )
            if value.get("returncode", 0) != 0:
                return LocalToolResult(
                    tool_name, False, value,
                    {
                        "type": "ProcessExitError",
                        "message": f"Process exited with code {value['returncode']}",
                    },
                )
        return LocalToolResult(tool_name, True, value)
    except Exception as exc:
        return LocalToolResult(
            tool_name, False,
            error={"type": type(exc).__name__, "message": str(exc)},
        )


def execute_local_tool(tool_name: str, arguments: dict[str, Any]) -> LocalToolResult:
    observe("action_started", tool=tool_name)
    outcome = _execute_local_tool(tool_name, arguments)
    if tool_name in {"run_process", "run_powershell"} and isinstance(outcome.result, dict):
        for stream in ("stdout", "stderr"):
            value = outcome.result
            if value.get(stream) or value.get(stream + "_truncated"):
                observe(stream, tool=tool_name, content=value.get(stream, ""),
                        truncated=value.get(stream + "_truncated", False),
                        original_length=value.get(stream + "_original_length", 0))
    observe("action_completed", tool=tool_name, ok=outcome.ok, error=outcome.error)
    return outcome


def execute_actions_request(
    actions: list[ActionRequest], stop_on_error: bool = True,
) -> dict[str, Any]:
    """Validate and sequentially execute a non-empty action batch."""
    if not isinstance(actions, list):
        raise TypeError("actions must be a list")
    if len(actions) > 100:
        raise ValueError("actions cannot exceed 100 entries")
    if not actions:
        raise ValueError("actions cannot be empty")
    if not isinstance(stop_on_error, bool):
        raise TypeError("stop_on_error must be a boolean")

    validated: list[tuple[str, dict[str, Any]]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise TypeError(f"actions[{index}] must be an object")
        tool = action.get("tool")
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"actions[{index}].tool must be a non-empty string")
        arguments = action.get("arguments")
        if not isinstance(arguments, dict):
            raise TypeError(f"actions[{index}].arguments must be an object")
        validated.append((tool, arguments))

    results = []
    successful = 0
    for index, (tool, arguments) in enumerate(validated):
        context = CURRENT.get()
        if context and context.cancelled.is_set():
            break
        if tool not in ACTION_LOCAL_TOOLS:
            outcome = LocalToolResult(
                tool, False,
                error={
                    "type": "ToolNotAllowedInActions",
                    "message": f"Tool is not allowed in execute_actions: {tool}",
                },
            )
        else:
            outcome = execute_local_tool(tool, arguments)
        successful += int(outcome.ok)
        results.append({
            "index": index,
            "tool": tool,
            "arguments": arguments,
            "ok": outcome.ok,
            "result": outcome.result,
            "error": outcome.error,
        })
        if not outcome.ok and stop_on_error:
            break

    executed = len(results)
    if successful == len(validated):
        status = "completed"
    elif successful == 0:
        status = "error"
    else:
        status = "partial"
    return {
        "status": status,
        "actions_requested": len(validated),
        "actions_executed": executed,
        "results": results,
    }


class InternalToolExecutor:
    def __init__(self, trace: E2EDebugTrace | None = None) -> None:
        self.trace = trace

    async def list_tools(self) -> list[dict[str, Any]]:
        return INTERNAL_TOOL_SCHEMAS

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> InternalToolResult:
        if self.trace:
            self.trace.emit("tool_action", action=name)
        try:
            outcome = execute_local_tool(name, arguments)
            value = outcome.result
            if self.trace:
                details = {}
                if isinstance(value, dict) and "returncode" in value:
                    details["returncode"] = value["returncode"]
                self.trace.emit("tool_result", action=name, **details)
            if outcome.ok:
                return InternalToolResult(False, value, [])
            assert outcome.error is not None
            return InternalToolResult(
                True,
                value,
                [{
                    "type": "text",
                    "text": f"{outcome.error['type']}: {outcome.error['message']}",
                }],
            )
        except Exception as exc:
            if self.trace:
                self.trace.emit(
                    "tool_result", action=name, error_type=type(exc).__name__
                )
            return InternalToolResult(
                True, None,
                [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
            )
