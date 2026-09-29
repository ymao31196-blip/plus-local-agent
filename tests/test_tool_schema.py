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
        "capability_search", "capability_describe", "capability_invoke", "provider_doctor",
        "read_text", "extract_document_text", "replace_text", "search_text", "run_process",
        "run_powershell", "apply_patch", "git_status", "git_diff",
    } <= tools_by_name.keys()
    capability_search = tools_by_name["capability_search"]["input_schema"]
    assert capability_search["required"] == ["query"]
    assert set(capability_search["properties"]) == {
        "query", "provider_id", "include_unavailable", "limit"
    }
    assert capability_search["properties"]["limit"]["default"] == 20
    assert capability_search["properties"]["limit"]["maximum"] == 100
    capability_describe = tools_by_name["capability_describe"]["input_schema"]
    assert capability_describe["required"] == ["capability_id"]
    capability_invoke = tools_by_name["capability_invoke"]["input_schema"]
    assert set(capability_invoke["required"]) == {"capability_id", "arguments"}
    assert set(capability_invoke["properties"]) == {
        "capability_id", "arguments", "confirmation"
    }
    provider_doctor = tools_by_name["provider_doctor"]["input_schema"]
    assert provider_doctor.get("required", []) == []
    assert set(provider_doctor["properties"]) == {
        "provider_id", "live_probe", "force"
    }
    assert provider_doctor["properties"]["live_probe"]["default"] is False
    assert provider_doctor["properties"]["force"]["default"] is False
    document_properties = tools_by_name["extract_document_text"]["input_schema"]["properties"]
    assert {"path", "start", "end", "max_chars", "root"} <= document_properties.keys()
    assert document_properties["max_chars"]["default"] == 20000
    process_properties = tools_by_name["run_process"]["input_schema"]["properties"]
    assert {"workdir", "env", "stdin", "root"} <= process_properties.keys()
    diff_properties = tools_by_name["git_diff"]["input_schema"]["properties"]
    assert {"cwd", "staged", "path", "root"} <= diff_properties.keys()
    for name in (
        "list_directory", "read_text", "extract_document_text", "write_text", "replace_text",
        "search_text", "run_process", "run_powershell", "apply_patch",
        "apply_changeset", "git_status", "git_diff",
    ):
        root_schema = tools_by_name[name]["input_schema"]["properties"]["root"]
        assert root_schema["default"] == "workspace"
    assert {
        "probe_artifact_resource_link",
        "artifact_metadata", "artifact_verify", "artifact_gc", "revoke_artifact",
        "git_log", "git_show", "git_stage", "git_remove", "git_commit", "git_tag", "git_push",
        "project_state_init", "project_state_get", "project_state_update", "project_checkpoint",
        "project_decision_record", "project_decisions_get", "project_evidence_record", "project_evidence_get",
        "project_acceptance_set", "project_acceptance_get", "project_acceptance_evaluate",
        "project_acceptance_evaluations_get", "project_verify_acceptance", "project_verifications_get",
        "transaction_create", "transaction_get", "transaction_checkpoint", "transaction_finalize",
        "transaction_invoke_capability",
    }.isdisjoint(tools_by_name)
    powershell_schema = tools_by_name["run_powershell"]["input_schema"]
    assert "script" not in powershell_schema["properties"]
    assert powershell_schema["required"] == ["command"]
    assert "diagnose_client" in tools_by_name
    assert {"probe_sampling", "run_agent_task"}.isdisjoint(tools_by_name)

    for tool in tools:
        print(f"{tool['name']}: {tool['input_schema']}")
