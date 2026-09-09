import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tool_schema import normalize_mcp_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def discover_tools():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "server.py")],
        cwd=str(PROJECT_ROOT),
        env={"AGENT_TASK_DB": os.environ["AGENT_TASK_DB"]},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return normalize_mcp_tools(await session.list_tools())


def test_normalize_mcp_tools_from_live_server():
    tools = asyncio.run(discover_tools())

    assert isinstance(tools, list)
    assert tools
    assert all(tool.get("name") for tool in tools)
    assert all(isinstance(tool.get("input_schema"), dict) for tool in tools)

    tools_by_name = {tool["name"]: tool for tool in tools}
    assert {
        "read_text", "replace_text", "search_text", "run_process",
        "run_powershell", "apply_patch",
    } <= tools_by_name.keys()
    process_properties = tools_by_name["run_process"]["input_schema"]["properties"]
    assert {"workdir", "env", "stdin"} <= process_properties.keys()
    powershell_schema = tools_by_name["run_powershell"]["input_schema"]
    assert "script" not in powershell_schema["properties"]
    assert powershell_schema["required"] == ["command"]
    agent_schema = tools_by_name["run_agent_task"]["input_schema"]
    assert set(agent_schema["properties"]) == {
        "task", "max_steps", "allowed_actions"
    }
    assert agent_schema["required"] == ["task"]

    assert {"diagnose_client", "probe_sampling"} <= tools_by_name.keys()

    for tool in tools:
        print(f"{tool['name']}: {tool['input_schema']}")
