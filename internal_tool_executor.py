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
    "list_directory", "read_text", "write_text", "replace_text", "search_text",
    "run_process",
})


class ActionRequest(TypedDict):
    tool: str
    arguments: dict[str, Any]


INTERNAL_TOOL_SCHEMAS = [
    {"name": "list_directory", "description": "List files inside the workspace.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}}},
    {"name": "read_text", "description": "Read selected lines from a UTF-8 text file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer", "default": 1},
         "end_line": {"type": "integer", "default": 400}}, "required": ["path"]}},
    {"name": "write_text", "description": "Create or overwrite a UTF-8 text file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"},
         "expected_sha256": {"type": ["string", "null"]}},
         "required": ["path", "content"]}},
    {"name": "replace_text", "description": "Replace exact text in an existing file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "old": {"type": "string"},
         "new": {"type": "string"}, "count": {"type": "integer", "default": 1},
         "expected_sha256": {"type": ["string", "null"]}},
         "required": ["path", "old", "new"]}},
    {"name": "search_text", "description": "Search bounded text matches inside the workspace.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "path": {"type": "string", "default": "."},
         "glob": {"type": ["string", "null"]},
         "case_sensitive": {"type": "boolean", "default": False},
         "max_results": {"type": "integer", "default": 100}},
         "required": ["query"]}},
    {"name": "run_process", "description": "Run an allow-listed process inside the workspace.",
     "input_schema": {"type": "object", "properties": {
         "program": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
         "cwd": {"type": "string", "default": "."},
         "workdir": {"type": ["string", "null"]},
         "timeout": {"type": "integer", "default": 120},
         "env": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
         "stdin": {"type": ["string", "null"]}}, "required": ["program"]}},
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
