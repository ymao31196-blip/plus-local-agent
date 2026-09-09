from collections.abc import Mapping
from typing import Any


def _as_plain_dict(value: Any) -> dict[str, Any]:
    """Convert an MCP/Pydantic schema value to a regular Python dict."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)

    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        if isinstance(dumped, Mapping):
            return dict(dumped)

    raise TypeError(f"Tool input schema must be mapping-like, got {type(value)!r}")


def normalize_mcp_tools(tool_result: Any) -> list[dict[str, Any]]:
    """Normalize a result from ``ClientSession.list_tools()``."""
    tools = getattr(tool_result, "tools", tool_result)
    normalized = []

    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("MCP tool is missing a valid name")

        input_schema = getattr(tool, "input_schema", None)
        if input_schema is None:
            input_schema = getattr(tool, "inputSchema", None)

        normalized.append(
            {
                "name": name,
                "description": getattr(tool, "description", None) or "",
                "input_schema": _as_plain_dict(input_schema),
            }
        )

    return normalized
