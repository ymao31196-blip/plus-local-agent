import asyncio
from pathlib import Path
from types import SimpleNamespace

import mcp_types
import pytest
from fastmcp import Client

from mcp_sampling_backend import MCPSamplingBackend, SamplingRequired
from server import mcp


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CALCULATOR = PROJECT_ROOT / "workspace" / "agent_test" / "calculator.py"
SERVER_PATH = PROJECT_ROOT / "server.py"


class FakeSamplingHandler:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.params = []

    async def __call__(self, messages, params, context):
        self.prompts.append(messages[0].content.text)
        self.params.append(params)
        if not self.responses:
            raise RuntimeError("Fake sampling response queue exhausted")
        return self.responses.pop(0)


async def call_agent(handler=None, *, task="Test task", max_steps=10, transport=mcp):
    client = Client(
        transport,
        sampling_handler=handler,
        sampling_capabilities=mcp_types.SamplingCapability() if handler else None,
        mode="2026-07-28",
        input_required_max_rounds=12,
    )
    async with client:
        result = await client.call_tool_mcp(
            "run_agent_task", {"task": task, "max_steps": max_steps}
        )
        return result.structured_content


async def call_tool(name, arguments, handler=None):
    client = Client(
        mcp,
        sampling_handler=handler,
        sampling_capabilities=mcp_types.SamplingCapability() if handler else None,
        mode="2026-07-28",
        input_required_max_rounds=12,
    )
    async with client:
        result = await client.call_tool_mcp(name, arguments)
        return result.structured_content


def test_sampling_backend_builds_current_mrtr_request_and_extracts_text():
    response = mcp_types.CreateMessageResult(
        role="assistant",
        model="fake",
        content=mcp_types.TextContent(text='{"action":"finish","arguments":{}}'),
    )
    ctx = SimpleNamespace(
        session=SimpleNamespace(
            client_capabilities=mcp_types.ClientCapabilities(
                sampling=mcp_types.SamplingCapability()
            )
        ),
        request_context=SimpleNamespace(protocol_version="2026-07-28"),
        input_responses={"agent_sampling": response},
    )
    backend = MCPSamplingBackend(ctx)
    assert asyncio.run(backend.generate("prompt")) == '{"action":"finish","arguments":{}}'

    ctx.input_responses = None
    backend = MCPSamplingBackend(ctx)
    with pytest.raises(SamplingRequired) as raised:
        asyncio.run(backend.generate("prompt"))
    params = raised.value.request.params
    assert params.messages[0].content.text == "prompt"
    assert params.temperature == 0
    assert params.tools is None
    assert params.tool_choice is None


def test_fake_sampling_client_single_round_finish():
    handler = FakeSamplingHandler(['{"action":"finish","arguments":{}}'])
    result = asyncio.run(call_agent(handler, transport=SERVER_PATH))
    assert result["status"] == "completed"
    assert result["steps"] == 1
    assert result["sampling_calls"] == 1
    assert len(handler.prompts) == 1


def test_diagnose_client_reports_capability_without_assuming_mrtr_ready():
    async def inspect():
        client = Client(
            mcp,
            sampling_handler=FakeSamplingHandler([]),
            sampling_capabilities=mcp_types.SamplingCapability(),
            mode="2026-07-28",
        )
        async with client:
            result = await client.call_tool_mcp("diagnose_client", {})
            return result.structured_content

    result = asyncio.run(inspect())
    assert result == {
        "fastmcp_version": "4.0.3",
        "mcp_version": "2.2.0",
        "protocol_mode": "mrtr",
        "sampling_supported": True,
        "agent_sampling_ready": False,
        "run_agent_task_available": True,
        "run_agent_task_experimental": True,
        "mainline": "chatgpt_native_agent_loop",
    }


def test_probe_sampling_single_round_is_authoritative_mrtr_check():
    async def probe_then_diagnose():
        client = Client(
            mcp,
            sampling_handler=FakeSamplingHandler(
                ['{"action":"finish","arguments":{}}']
            ),
            sampling_capabilities=mcp_types.SamplingCapability(),
            mode="2026-07-28",
            input_required_max_rounds=2,
        )
        async with client:
            probe = await client.call_tool_mcp("probe_sampling", {})
            diagnose = await client.call_tool_mcp("diagnose_client", {})
            return probe.structured_content, diagnose.structured_content

    probe, diagnose = asyncio.run(probe_then_diagnose())
    assert probe == {
        "status": "success",
        "sampling_calls": 1,
        "decision": {"action": "finish", "arguments": {}},
    }
    # A separate ordinary MRTR call cannot prove that the client will resume
    # input_required; the successful probe result above is that proof.
    assert diagnose["agent_sampling_ready"] is False


def test_probe_sampling_without_client_capability_is_explicit():
    result = asyncio.run(call_tool("probe_sampling", {}))
    assert result["status"] == "error"
    assert result["sampling_calls"] == 0
    assert result["error"]["type"] == "SamplingUnsupported"


def test_probe_sampling_rejects_non_exact_decision():
    result = asyncio.run(call_tool(
        "probe_sampling", {},
        FakeSamplingHandler(['{"action":"read_text","arguments":{}}']),
    ))
    assert result["status"] == "error"
    assert result["sampling_calls"] == 1
    assert result["error"]["type"] == "DecisionParseError"


def test_readonly_agent_policy_excludes_write_and_process_actions():
    result = asyncio.run(call_tool(
        "run_agent_task",
        {
            "task": "Inspect only",
            "allowed_actions": ["list_directory", "read_text"],
        },
        FakeSamplingHandler([
            '{"action":"write_text","arguments":{"path":"x","content":"x"}}'
        ]),
    ))
    assert result["status"] == "error"
    assert result["error"]["type"] == "ValueError"
    assert "Unknown action" in result["error"]["message"]


def test_sampling_mrtr_wire_shape_is_input_required_create_message():
    handler = FakeSamplingHandler(['{"action":"finish","arguments":{}}'])

    async def inspect_first_round():
        client = Client(
            mcp,
            sampling_handler=handler,
            sampling_capabilities=mcp_types.SamplingCapability(),
            mode="2026-07-28",
        )
        async with client:
            return await client.session.call_tool(
                "run_agent_task",
                {"task": "Finish"},
                allow_input_required=True,
            )

    result = asyncio.run(inspect_first_round())
    assert isinstance(result, mcp_types.InputRequiredResult)
    assert result.result_type == "input_required"
    assert result.input_requests["agent_sampling"].method == "sampling/createMessage"
    assert handler.prompts == []


def test_full_sampling_e2e_observations_feed_each_next_prompt():
    original = CALCULATOR.read_text(encoding="utf-8")
    CALCULATOR.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    handler = FakeSamplingHandler([
        '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
        '{"action":"run_process","arguments":{"program":"pytest","args":["agent_test"]}}',
        '{"action":"replace_text","arguments":{"path":"agent_test/calculator.py","old":"return a - b","new":"return a + b"}}',
        '{"action":"run_process","arguments":{"program":"pytest","args":["agent_test"]}}',
        '{"action":"finish","arguments":{}}',
    ])
    try:
        result = asyncio.run(call_agent(handler, task="Fix the failing calculator test"))
        final_code = CALCULATOR.read_text(encoding="utf-8")
    finally:
        CALCULATOR.write_text(original, encoding="utf-8")

    assert result["status"] == "completed"
    assert result["steps"] == 5
    assert result["sampling_calls"] == 5
    assert [item["action"] for item in result["history"]] == [
        "read_text", "run_process", "replace_text", "run_process"
    ]
    assert "return a + b" in final_code
    assert len(handler.prompts) == 5
    assert "return a - b" in handler.prompts[1]
    assert '"returncode": 1' in handler.prompts[2]
    assert '"replacements": 1' in handler.prompts[3]
    assert '"returncode": 0' in handler.prompts[4]
    assert all(params.tools is None for params in handler.params)


def test_invalid_sampling_response_is_recorded_as_decision_parse_error():
    result = asyncio.run(call_agent(FakeSamplingHandler(["not json"])))
    assert result["status"] == "error"
    assert result["error"]["type"] == "DecisionParseError"
    assert result["history"][0]["error"]["type"] == "DecisionParseError"


def test_unknown_sampled_tool_is_rejected_by_validator():
    handler = FakeSamplingHandler([
        '{"action":"delete_everything","arguments":{}}'
    ])
    result = asyncio.run(call_agent(handler))
    assert result["status"] == "error"
    assert result["error"]["type"] == "ValueError"
    assert "Unknown action" in result["error"]["message"]


def test_client_without_sampling_gets_explicit_error():
    result = asyncio.run(call_agent())
    assert result["status"] == "error"
    assert result["error"]["type"] == "SamplingUnsupported"
    assert "SamplingUnsupported" in result["error"]["message"]


def test_sampling_agent_honors_max_steps():
    handler = FakeSamplingHandler([
        '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
        '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
    ])
    result = asyncio.run(call_agent(handler, max_steps=2))
    assert result["status"] == "step_limit"
    assert result["steps"] == 2
    assert result["sampling_calls"] == 2


def test_tool_business_error_is_visible_to_next_sampling_round():
    handler = FakeSamplingHandler([
        '{"action":"read_text","arguments":{"path":"missing.txt"}}',
        '{"action":"finish","arguments":{}}',
    ])
    result = asyncio.run(call_agent(handler))
    assert result["status"] == "completed"
    assert result["history"][0]["result"]["is_error"] is True
    assert "File does not exist" in handler.prompts[1]


def test_high_level_agent_does_not_bypass_program_allowlist():
    handler = FakeSamplingHandler([
        '{"action":"run_process","arguments":{"program":"powershell.exe"}}',
        '{"action":"finish","arguments":{}}',
    ])
    result = asyncio.run(call_agent(handler))
    observation = result["history"][0]["result"]
    assert observation["is_error"] is True
    assert "Program not allowed: powershell.exe" in str(observation)
