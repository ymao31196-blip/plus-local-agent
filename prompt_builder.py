import json
from typing import Any


def build_agent_prompt(
    task: str,
    observations: Any,
    tools: list[dict[str, Any]],
) -> str:
    """Serialize generic agent state and its strict one-action protocol."""
    context = {
        "task": task,
        "history": observations,
        "tools": [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", {}),
            }
            for tool in tools
        ],
    }
    serialized_context = json.dumps(
        context,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    return f"""You are the reasoning component of a generic tool-using agent.

Choose exactly one next action from the available MCP tools, or choose finish
when the task is complete. Base the choice only on the agent context below.

AGENT CONTEXT
{serialized_context}

OUTPUT PROTOCOL
Return exactly one JSON object with exactly these two top-level fields:
{{"action":"tool_name_or_finish","arguments":{{}}}}

Rules:
- Do not use a Markdown code block.
- Do not include explanations or any text before or after the JSON object.
- action must be the name of an available tool or "finish".
- arguments must be a JSON object conforming to that tool's input_schema.
- Use an empty arguments object when action is "finish".
- Select only one action per response.
"""
