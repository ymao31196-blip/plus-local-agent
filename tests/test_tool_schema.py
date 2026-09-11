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
        "artifact_verify", "artifact_gc",
        "read_text", "extract_document_text", "replace_text", "search_text", "run_process",
        "run_powershell", "apply_patch", "git_status", "git_diff", "git_log", "git_show", "git_stage", "git_commit",
        "project_state_init", "project_state_get", "project_state_update", "project_checkpoint",
        "project_decision_record", "project_decisions_get", "project_evidence_record", "project_evidence_get",
        "project_acceptance_set", "project_acceptance_get", "project_acceptance_evaluate", "project_acceptance_evaluations_get",
        "project_verify_acceptance", "project_verifications_get",
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
    artifact_verify = tools_by_name["artifact_verify"]["input_schema"]
    assert artifact_verify["required"] == ["artifact_id"]
    assert set(artifact_verify["properties"]) == {"artifact_id", "recursive"}
    assert artifact_verify["properties"]["recursive"]["default"] is True
    artifact_gc = tools_by_name["artifact_gc"]["input_schema"]
    assert artifact_gc.get("required", []) == []
    assert set(artifact_gc["properties"]) == {"dry_run", "confirmation"}
    assert artifact_gc["properties"]["dry_run"]["default"] is True

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
        "apply_changeset", "git_status", "git_diff", "git_log", "git_show", "git_stage", "git_commit",
        "project_state_init", "project_state_get", "project_state_update", "project_checkpoint",
        "project_decision_record", "project_decisions_get", "project_evidence_record", "project_evidence_get",
        "project_acceptance_set", "project_acceptance_get", "project_acceptance_evaluate", "project_acceptance_evaluations_get",
        "project_verify_acceptance", "project_verifications_get",
    ):
        root_schema = tools_by_name[name]["input_schema"]["properties"]["root"]
        assert root_schema["default"] == "workspace"
    assert tools_by_name["git_log"]["input_schema"]["properties"]["limit"]["maximum"] == 100
    stage_schema = tools_by_name["git_stage"]["input_schema"]
    assert set(stage_schema["required"]) == {"changes", "expected_head"}
    assert set(stage_schema["properties"]) == {"changes", "expected_head", "cwd", "root"}
    stage_item = stage_schema["properties"]["changes"]["items"]
    assert set(stage_item["required"]) == {"path", "expected_sha256"}
    commit_schema = tools_by_name["git_commit"]["input_schema"]
    assert set(commit_schema["required"]) == {"message", "paths", "expected_head"}
    assert set(commit_schema["properties"]) == {"message", "paths", "expected_head", "cwd", "root"}
    state_update_schema = tools_by_name["project_state_update"]["input_schema"]
    assert state_update_schema["required"] == ["expected_revision"]
    assert state_update_schema["properties"]["expected_revision"]["minimum"] == 1
    checkpoint_schema = tools_by_name["project_checkpoint"]["input_schema"]
    assert set(checkpoint_schema["required"]) == {"label", "summary", "expected_revision"}
    decision_schema = tools_by_name["project_decision_record"]["input_schema"]
    assert set(decision_schema["required"]) == {"title", "decision", "rationale", "expected_revision"}
    evidence_schema = tools_by_name["project_evidence_record"]["input_schema"]
    assert set(evidence_schema["required"]) == {"kind", "status", "summary", "source", "expected_revision"}
    assert tools_by_name["project_evidence_get"]["input_schema"]["properties"]["limit"]["maximum"] == 100
    assert "verification" in evidence_schema["properties"]
    acceptance_set = tools_by_name["project_acceptance_set"]["input_schema"]
    assert set(acceptance_set["required"]) == {"title", "checks", "expected_state_revision"}
    check_item = acceptance_set["properties"]["checks"]["items"]
    assert set(check_item["required"]) == {"id", "description", "evidence_kinds"}
    acceptance_eval = tools_by_name["project_acceptance_evaluate"]["input_schema"]
    assert set(acceptance_eval["required"]) == {"bindings", "expected_state_revision", "expected_contract_revision"}
    binding_item = acceptance_eval["properties"]["bindings"]["items"]
    assert set(binding_item["required"]) == {"check_id", "evidence_ids"}
    assert tools_by_name["project_acceptance_evaluations_get"]["input_schema"]["properties"]["limit"]["maximum"] == 100
    verifier_schema = tools_by_name["project_verify_acceptance"]["input_schema"]
    assert set(verifier_schema["required"]) == {
        "evaluation_id", "expected_evaluation_sha256",
        "expected_state_revision", "expected_contract_revision",
    }
    assert tools_by_name["project_verifications_get"]["input_schema"]["properties"]["limit"]["maximum"] == 100
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
