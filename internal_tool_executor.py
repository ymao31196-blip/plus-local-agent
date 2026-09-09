"""Direct executor for the server's local tools; no self-MCP connection."""

from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from typing import Any

from typing_extensions import TypedDict

from local_tools import LOCAL_TOOL_FUNCTIONS
from e2e_debug import E2EDebugTrace


EXECUTABLE_LOCAL_TOOLS = frozenset({
    "list_directory", "read_text", "write_text", "replace_text", "run_process",
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
         "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
    {"name": "replace_text", "description": "Replace exact text in an existing file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "old": {"type": "string"},
         "new": {"type": "string"}, "count": {"type": "integer", "default": 1}},
         "required": ["path", "old", "new"]}},
    {"name": "run_process", "description": "Run an allow-listed process inside the workspace.",
     "input_schema": {"type": "object", "properties": {
         "program": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
         "cwd": {"type": "string", "default": "."},
         "timeout": {"type": "integer", "default": 120}}, "required": ["program"]}},
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


def execute_local_tool(tool_name: str, arguments: dict[str, Any]) -> LocalToolResult:
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
        inspect.signature(function).bind(**arguments)
        value = function(**arguments)
        if inspect.isawaitable(value):
            raise TypeError("Async local tools are not supported by the synchronous executor")
        if tool_name == "run_process" and isinstance(value, dict):
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


def execute_actions_request(
    actions: list[ActionRequest], stop_on_error: bool = True,
) -> dict[str, Any]:
    """Validate and sequentially execute a non-empty action batch."""
    if not isinstance(actions, list):
        raise TypeError("actions must be a list")
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
