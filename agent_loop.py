"""CLI adapter and backward-compatible exports for the agent service."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_service import (
    MAX_STEPS, AgentResult, AgentState, normalize_tool_result, run_agent,
    validate_decision,
)
from rule_reasoner import RuleReasoner


PROJECT_ROOT = Path(__file__).resolve().parent
server_params = StdioServerParameters(
    command=sys.executable, args=[str(PROJECT_ROOT / "server.py")], cwd=str(PROJECT_ROOT)
)


async def call_tool(session: Any, name: str, arguments: dict[str, Any]) -> Any:
    return await session.call_tool(name, arguments)


async def main() -> None:
    print("Agent started")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await run_agent(
                session=session, reasoner=RuleReasoner(),
                task="Finish when no rule-based action is required",
            )
            print(result)


if __name__ == "__main__":
    asyncio.run(main())


__all__ = [
    "MAX_STEPS", "AgentResult", "AgentState", "call_tool",
    "normalize_tool_result", "run_agent", "validate_decision",
]
