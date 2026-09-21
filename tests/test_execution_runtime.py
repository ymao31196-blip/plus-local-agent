import asyncio
import time
from threading import Event

import local_tools
import task_store as task_store_module
from fastmcp import Client
from internal_tool_executor import (
    EXECUTABLE_LOCAL_TOOLS,
    LocalToolResult,
    execute_actions_request,
    execute_local_tool,
)
from task_store import TaskStore
from server import mcp


def _wait_finished(store: TaskStore, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = store.get(task_id)
        if result["status"] in {"completed", "failed"}:
            return result
        time.sleep(0.01)
    raise AssertionError("background task did not finish")


def test_execute_actions_runs_two_successful_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    result = execute_actions_request([
        {"tool": "write_text", "arguments": {"path": "a.txt", "content": "hello"}},
        {"tool": "read_text", "arguments": {"path": "a.txt"}},
    ])

    assert result["status"] == "completed"
    assert result["actions_requested"] == result["actions_executed"] == 2
    assert all(item["ok"] for item in result["results"])
    assert result["results"][1]["result"]["content"] == "1: hello"


def test_stop_on_error_true_stops_after_second_action(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    result = execute_actions_request([
        {"tool": "write_text", "arguments": {"path": "a.txt", "content": "ok"}},
        {"tool": "read_text", "arguments": {"path": "missing.txt"}},
        {"tool": "write_text", "arguments": {"path": "never.txt", "content": "no"}},
    ])

    assert result["status"] == "partial"
    assert result["actions_executed"] == 2
    assert not (tmp_path / "never.txt").exists()


def test_stop_on_error_false_continues_after_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    result = execute_actions_request([
        {"tool": "read_text", "arguments": {"path": "missing.txt"}},
        {"tool": "write_text", "arguments": {"path": "later.txt", "content": "yes"}},
    ], stop_on_error=False)

    assert result["status"] == "partial"
    assert result["actions_executed"] == 2
    assert (tmp_path / "later.txt").read_text(encoding="utf-8") == "yes"


def test_unknown_and_control_layer_tools_are_rejected():
    unknown = execute_local_tool("not_a_tool", {})
    recursive = execute_local_tool("execute_actions", {})

    assert unknown.error["message"] == "Unknown local tool: not_a_tool"
    assert recursive.error["message"] == "Unknown local tool: execute_actions"
    assert "run_agent_task" not in EXECUTABLE_LOCAL_TOOLS
    assert "task_result" not in EXECUTABLE_LOCAL_TOOLS


def test_safe_path_still_applies_in_unified_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    result = execute_local_tool("read_text", {"path": "../outside.txt"})

    assert result.ok is False
    assert result.error["type"] == "ValueError"
    assert "outside workspace" in result.error["message"]


def test_process_allowlist_still_applies_in_unified_executor():
    result = execute_local_tool("run_process", {"program": "powershell.exe"})

    assert result.ok is False
    assert result.error["message"] == "Program not allowed: powershell.exe"


def test_specialized_routing_block_is_error_in_unified_executor():
    result = execute_local_tool(
        "run_process",
        {"program": "git", "args": ["status"], "root": "pla"},
    )

    assert result.ok is False
    assert result.error["type"] == "SpecializedCapabilityRequired"
    assert result.result["reason"] == "specialized_capability_required"
    assert result.result["suggested_capabilities"][0]["name"] == "git_status"


def test_powershell_service_steering_is_error_in_unified_executor():
    result = execute_local_tool(
        "run_powershell",
        {"command": "Stop-Service", "parameters": {"Name": "ExampleSvc"}},
    )

    assert result.ok is False
    assert result.error["type"] == "SpecializedCapabilityRequired"
    assert result.result["domain"] == "windows_service"
    assert result.result["attempted_route"]["operation"] == "stop"
    assert result.result["suggested_capabilities"][0]["name"] == (
        "windows.service_control_preflight"
    )


def test_stdout_truncation_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    result = execute_local_tool("run_process", {
        "program": "python", "args": ["-c", "print('x' * 21000)"],
    })

    assert result.ok is True
    assert result.result["stdout_truncated"] is True
    assert result.result["stdout_original_length"] > 20_000
    assert len(result.result["stdout"]) == 20_000


def test_submit_returns_task_id_and_task_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    store = TaskStore(max_workers=1)
    submitted = store.submit({
        "tool": "write_text",
        "arguments": {"path": "background.txt", "content": "done"},
    })
    finished = _wait_finished(store, submitted["task_id"])

    assert submitted["status"] == "queued"
    assert finished["status"] == "completed"
    assert finished["created_at"] and finished["started_at"] and finished["finished_at"]
    assert finished["request"]["tool"] == "write_text"


def test_task_result_can_report_running(monkeypatch):
    started = Event()
    release = Event()

    def blocked(tool, arguments):
        started.set()
        release.wait(timeout=5)
        return LocalToolResult(tool, True, {"done": True})

    monkeypatch.setattr(task_store_module, "execute_local_tool", blocked)
    store = TaskStore(max_workers=1)
    submitted = store.submit({"tool": "read_text", "arguments": {"path": "x"}})
    assert started.wait(timeout=2)
    assert store.get(submitted["task_id"])["status"] == "running"
    release.set()
    assert _wait_finished(store, submitted["task_id"])["status"] == "completed"


def test_missing_task_id_returns_task_not_found():
    result = TaskStore(max_workers=1).get("missing")

    assert result["status"] == "error"
    assert result["error"]["type"] == "TaskNotFound"


def test_background_task_failure_is_recorded():
    store = TaskStore(max_workers=1)
    submitted = store.submit({"tool": "unknown", "arguments": {}})
    finished = _wait_finished(store, submitted["task_id"])

    assert finished["status"] == "failed"
    assert finished["error"]["type"] == "TaskExecutionError"
    assert finished["result"]["error"]["type"] == "UnknownLocalTool"


def test_action_request_validation_messages_are_clear():
    try:
        execute_actions_request([])
    except ValueError as exc:
        assert str(exc) == "actions cannot be empty"
    else:
        raise AssertionError("empty actions were accepted")

    try:
        execute_actions_request([{"tool": "read_text", "arguments": []}])
    except TypeError as exc:
        assert str(exc) == "actions[0].arguments must be an object"
    else:
        raise AssertionError("non-object arguments were accepted")


def test_fastmcp_action_schema_and_batch_call(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    async def inspect_and_call():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            result = await client.call_tool_mcp("execute_actions", {
                "actions": [{
                    "tool": "write_text",
                    "arguments": {"path": "mcp.txt", "content": "mcp"},
                }],
            })
            return tools["execute_actions"].input_schema, result.structured_content

    schema, result = asyncio.run(inspect_and_call())
    action_items = schema["properties"]["actions"]["items"]
    assert set(action_items["required"]) == {"tool", "arguments"}
    assert result["status"] == "completed"


def test_fastmcp_submit_and_task_result(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    async def submit_and_wait():
        async with Client(mcp) as client:
            submitted = (await client.call_tool_mcp("submit_task", {
                "tool": "write_text",
                "arguments": {"path": "async.txt", "content": "ok"},
            })).structured_content
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                record = (await client.call_tool_mcp("task_result", {
                    "task_id": submitted["task_id"],
                })).structured_content
                if record["status"] in {"completed", "failed"}:
                    return submitted, record
                await asyncio.sleep(0.01)
        raise AssertionError("MCP background task did not finish")

    submitted, record = asyncio.run(submit_and_wait())
    assert submitted["status"] == "queued"
    assert record["status"] == "completed"
