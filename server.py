from __future__ import annotations

import json
import argparse
from importlib.metadata import version
from dataclasses import asdict

import mcp_types
from fastmcp import Context, FastMCP

from agent_service import AgentState, run_agent
from decision_parser import DecisionParseError, parse_decision
from e2e_debug import E2EDebugTrace, protocol_mode
from generic_llm_reasoner import GenericLLMReasoner
from internal_tool_executor import (
    INTERNAL_TOOL_SCHEMAS,
    ActionRequest,
    InternalToolExecutor,
    execute_actions_request,
)
from local_tools import (
    ALLOWED_PROGRAMS,
    WORKSPACE,
    list_directory as internal_list_directory,
    read_text as internal_read_text,
    replace_text as internal_replace_text,
    run_process as internal_run_process,
    safe_path,
    write_text as internal_write_text,
)
from mcp_sampling_backend import (
    MCPSamplingBackend,
    SAMPLING_KEY,
    SamplingRequired,
    SamplingUnsupported,
)
from task_store import TASK_STORE


mcp = FastMCP("Local Agent Tools", version="0.7")


@mcp.tool
def list_directory(path: str = ".") -> list[str]:
    """列出 workspace 内指定目录的文件和子目录。"""
    return internal_list_directory(path)


@mcp.tool
def read_text(path: str, start_line: int = 1, end_line: int = 400) -> dict:
    """读取 workspace 内 UTF-8 文本文件的指定行。"""
    return internal_read_text(path, start_line, end_line)


@mcp.tool
def write_text(path: str, content: str) -> dict:
    """创建或覆盖 workspace 中的文本文件。"""
    return internal_write_text(path, content)


@mcp.tool
def replace_text(path: str, old: str, new: str, count: int = 1) -> dict:
    """在已有文本文件中精确替换内容。"""
    return internal_replace_text(path, old, new, count)


@mcp.tool
def run_process(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 120,
) -> dict:
    """在 workspace 内运行受允许的程序。"""
    return internal_run_process(program, args, cwd, timeout)


@mcp.tool
def execute_actions(
    actions: list[ActionRequest], stop_on_error: bool = True,
) -> dict:
    """顺序执行一组明确的本地 Action；默认遇到失败即停止。"""
    return execute_actions_request(actions, stop_on_error)


@mcp.tool
def submit_task(
    actions: list[ActionRequest] | None = None,
    tool: str | None = None,
    arguments: dict | None = None,
    stop_on_error: bool = True,
) -> dict:
    """提交结构化本地执行任务到进程内后台 worker。"""
    has_actions = actions is not None
    has_tool = tool is not None
    if has_actions == has_tool:
        raise ValueError("Provide exactly one of actions or tool")
    if not isinstance(stop_on_error, bool):
        raise TypeError("stop_on_error must be a boolean")
    if has_actions:
        # Validate the complete batch before accepting background work. This
        # deliberately performs no local action.
        if not isinstance(actions, list):
            raise TypeError("actions must be a list")
        if not actions:
            raise ValueError("actions cannot be empty")
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise TypeError(f"actions[{index}] must be an object")
            if not isinstance(action.get("tool"), str) or not action["tool"]:
                raise ValueError(f"actions[{index}].tool must be a non-empty string")
            if not isinstance(action.get("arguments"), dict):
                raise TypeError(f"actions[{index}].arguments must be an object")
        request = {"actions": actions, "stop_on_error": stop_on_error}
    else:
        if not isinstance(tool, str) or not tool:
            raise ValueError("tool must be a non-empty string")
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        request = {"tool": tool, "arguments": arguments}
    return TASK_STORE.submit(request)


@mcp.tool
def task_result(task_id: str) -> dict:
    """读取后台结构化执行任务的当前状态和结果。"""
    return TASK_STORE.get(task_id)


def _load_state(raw_state: str | None) -> AgentState:
    if raw_state is None:
        return AgentState()
    value = json.loads(raw_state)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Invalid agent request state")
    return AgentState.from_dict(value["agent"])


def _dump_state(state: AgentState) -> str:
    return json.dumps(
        {"version": 1, "agent": state.to_dict()},
        ensure_ascii=False, separators=(",", ":"),
    )


def _sampling_supported(ctx: Context) -> bool | str:
    session = getattr(ctx, "session", None)
    capabilities = getattr(session, "client_capabilities", None)
    if capabilities is None:
        return "unknown"
    return getattr(capabilities, "sampling", None) is not None


@mcp.tool
async def diagnose_client(ctx: Context) -> dict:
    """Return read-only MCP protocol and client sampling diagnostics."""
    trace = E2EDebugTrace.start(ctx, "diagnose_client")
    sampling_supported = _sampling_supported(ctx)
    mode = protocol_mode(ctx)
    result = {
        "fastmcp_version": version("fastmcp"),
        "mcp_version": version("mcp"),
        "protocol_mode": mode,
        "sampling_supported": sampling_supported,
        # Legacy sampling can be inferred from its declared back-channel. In
        # MRTR mode, a normal tool call cannot prove that input_required will
        # be resumed, so probe_sampling is the authoritative readiness check.
        "agent_sampling_ready": bool(
            sampling_supported is True and mode == "backchannel"
        ),
        "run_agent_task_available": True,
        "run_agent_task_experimental": True,
        "mainline": "chatgpt_native_agent_loop",
    }
    trace.emit("final_status", status="completed")
    return result


PROBE_PROMPT = """Return exactly this JSON object and nothing else:
{"action":"finish","arguments":{}}"""


@mcp.tool
async def probe_sampling(ctx: Context) -> dict | mcp_types.InputRequiredResult:
    """Perform one client-provided sampling round without local tool access."""
    trace = E2EDebugTrace.start(ctx, "probe_sampling")
    if ctx.request_state is not None:
        trace.emit("request_state_resumed")
    backend = MCPSamplingBackend(ctx, max_tokens=100, trace=trace)
    try:
        raw = await backend.generate(PROBE_PROMPT)
        decision = parse_decision(raw)
        if decision != {"action": "finish", "arguments": {}}:
            raise DecisionParseError(
                "Probe decision must be exactly the requested finish decision"
            )
        result = {"status": "success", "sampling_calls": 1, "decision": decision}
        trace.emit("final_status", status="success")
        return result
    except SamplingRequired as required:
        trace.emit("input_required", sampling_sequence=1)
        return mcp_types.InputRequiredResult(
            input_requests={SAMPLING_KEY: required.request},
            request_state=json.dumps(
                {"version": 1, "kind": "probe_sampling"}, separators=(",", ":")
            ),
        )
    except Exception as exc:
        trace.emit("final_status", status="error", error_type=type(exc).__name__)
        return {
            "status": "error",
            "sampling_calls": 0 if isinstance(exc, SamplingUnsupported) else 1,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


@mcp.tool
async def run_agent_task(
    task: str,
    ctx: Context,
    max_steps: int = 10,
    allowed_actions: list[str] | None = None,
) -> dict | mcp_types.InputRequiredResult:
    """Run the local agent using only model sampling supplied by this MCP client."""
    trace = E2EDebugTrace.start(ctx, "run_agent_task")
    state = _load_state(ctx.request_state)
    if ctx.request_state is not None:
        trace.emit("request_state_resumed")
    tools = INTERNAL_TOOL_SCHEMAS
    if allowed_actions is not None:
        known = {tool["name"] for tool in INTERNAL_TOOL_SCHEMAS}
        unknown = sorted(set(allowed_actions) - known)
        if unknown:
            raise ValueError(f"Unknown allowed_actions: {unknown}")
        allowed = set(allowed_actions)
        tools = [tool for tool in INTERNAL_TOOL_SCHEMAS if tool["name"] in allowed]
    backend = MCPSamplingBackend(
        ctx,
        trace=trace,
        sampling_sequence=state.sampling_calls + 1,
    )
    reasoner = GenericLLMReasoner(backend)
    try:
        result = await run_agent(
            task=task,
            reasoner=reasoner,
            session=InternalToolExecutor(trace),
            tools=tools,
            max_steps=max_steps,
            state=state,
            capture_errors=True,
        )
        trace.emit("final_status", status=result.status)
        return asdict(result)
    except SamplingRequired as required:
        trace.emit("input_required", sampling_sequence=state.sampling_calls + 1)
        return mcp_types.InputRequiredResult(
            input_requests={SAMPLING_KEY: required.request},
            request_state=_dump_state(state),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Agent MCP server")
    parser.add_argument("--http", action="store_true", help="Use Streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port, path=args.path)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
