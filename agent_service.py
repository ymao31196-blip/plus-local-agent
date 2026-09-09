"""Reusable asynchronous agent runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from decision_parser import DecisionParseError
from model_backend import ModelInputRequired
from reasoner import BaseReasoner
from tool_schema import normalize_mcp_tools


MAX_STEPS = 10


@dataclass
class AgentState:
    next_step: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)
    final_decision: dict[str, Any] | None = None
    sampling_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentState":
        return cls(
            next_step=value.get("next_step", 1),
            history=value.get("history", []),
            final_decision=value.get("final_decision"),
            sampling_calls=value.get("sampling_calls", 0),
        )


@dataclass
class AgentResult:
    status: Literal["completed", "step_limit", "error"]
    steps: int
    final_decision: dict[str, Any] | None
    history: list[dict[str, Any]] = field(default_factory=list)
    sampling_calls: int = 0
    error: dict[str, str] | None = None


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "string": return isinstance(value, str)
    if expected == "array": return isinstance(value, list)
    if expected == "object": return isinstance(value, dict)
    if expected == "boolean": return isinstance(value, bool)
    if expected == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null": return value is None
    return True


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        raise ValueError(f"Tool arguments are missing required fields: {missing}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ValueError(f"Tool arguments contain unknown fields: {unknown}")
    for name, value in arguments.items():
        expected_type = properties.get(name, {}).get("type")
        if isinstance(expected_type, str) and not _json_type_matches(value, expected_type):
            raise TypeError(f"Tool argument {name!r} must have JSON type {expected_type}")


def validate_decision(decision: Any, tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not isinstance(decision, dict):
        raise TypeError("Reasoner decision must be a dict")
    action = decision.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError("Reasoner decision must include a non-empty string action")
    arguments = decision.get("arguments")
    if not isinstance(arguments, dict):
        raise TypeError("Reasoner decision arguments must be a dict")
    tools_by_name = {tool["name"]: tool for tool in tools}
    if action == "finish":
        if arguments:
            raise ValueError("finish action must use empty arguments")
        return action, arguments
    if action not in tools_by_name:
        raise ValueError(f"Unknown action requested by reasoner: {action}")
    _validate_arguments(arguments, tools_by_name[action].get("input_schema", {}))
    return action, arguments


def normalize_tool_result(result: Any) -> dict[str, Any]:
    content = []
    for block in getattr(result, "content", []):
        if isinstance(block, dict):
            block_type = block.get("type", "dict")
            value = block.get("text", str(block))
        else:
            block_type = getattr(block, "type", type(block).__name__)
            value = getattr(block, "text", None)
            if value is None: value = str(block)
        content.append({"type": block_type, "value": value})
    return {"is_error": bool(getattr(result, "is_error", False)),
            "structured_content": getattr(result, "structured_content", None),
            "content": content}


async def run_agent(
    session: Any, reasoner: BaseReasoner, task: str,
    tools: list[dict[str, Any]] | None = None, max_steps: int = MAX_STEPS,
    *, state: AgentState | None = None, capture_errors: bool = False,
) -> AgentResult:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    if tools is None:
        tools = normalize_mcp_tools(await session.list_tools())
    state = state or AgentState()
    for step in range(state.next_step, max_steps + 1):
        try:
            decision = await reasoner.decide(task=task, observations=state.history, tools=tools)
            state.sampling_calls += 1
            action, arguments = validate_decision(decision, tools)
            state.final_decision = decision
        except ModelInputRequired:
            raise
        except Exception as exc:
            state.sampling_calls += int(isinstance(exc, DecisionParseError))
            if not capture_errors:
                raise
            error = {"type": type(exc).__name__, "message": str(exc)}
            state.history.append({"step": step, "error": error})
            return AgentResult("error", step, state.final_decision, state.history,
                               state.sampling_calls, error)
        if action == "finish":
            return AgentResult("completed", step, decision, state.history, state.sampling_calls)
        result = await session.call_tool(action, arguments)
        state.history.append({"step": step, "action": action, "arguments": arguments,
                              "result": normalize_tool_result(result)})
        state.next_step = step + 1
    return AgentResult("step_limit", max_steps, state.final_decision, state.history,
                       state.sampling_calls)
