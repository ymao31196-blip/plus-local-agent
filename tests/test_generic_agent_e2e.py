import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_loop import run_agent
from fake_model_backend import FakeModelBackend, FakeModelResponsesExhausted
from generic_llm_reasoner import GenericLLMReasoner


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CALCULATOR = PROJECT_ROOT / "workspace" / "agent_test" / "calculator.py"
SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(PROJECT_ROOT / "server.py")],
    cwd=str(PROJECT_ROOT),
)


async def run_with_backend(backend, *, max_steps=10):
    SERVER_PARAMS.env = {"AGENT_TASK_DB": os.environ["AGENT_TASK_DB"]}
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await run_agent(
                session=session,
                reasoner=GenericLLMReasoner(backend),
                task="Fix the failing calculator test",
                max_steps=max_steps,
            )


def test_fake_model_agent_completes_full_mcp_loop():
    original = CALCULATOR.read_text(encoding="utf-8")
    CALCULATOR.write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    backend = FakeModelBackend(
        responses=[
            '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
            '{"action":"run_process","arguments":{"program":"pytest","args":["agent_test"]}}',
            '{"action":"replace_text","arguments":{"path":"agent_test/calculator.py","old":"return a - b","new":"return a + b"}}',
            '{"action":"run_process","arguments":{"program":"pytest","args":["agent_test"]}}',
            '{"action":"finish","arguments":{}}',
        ]
    )

    try:
        result = asyncio.run(run_with_backend(backend))
        final_code = CALCULATOR.read_text(encoding="utf-8")
    finally:
        CALCULATOR.write_text(original, encoding="utf-8")

    assert result.status == "completed"
    assert result.steps == 5
    assert [item["action"] for item in result.history] == [
        "read_text",
        "run_process",
        "replace_text",
        "run_process",
    ]
    first_pytest = result.history[1]["result"]["structured_content"]
    second_pytest = result.history[3]["result"]["structured_content"]
    assert first_pytest["returncode"] != 0
    assert second_pytest["returncode"] == 0
    assert "return a + b" in final_code


def test_unknown_tool_is_rejected_before_execution():
    backend = FakeModelBackend(
        responses=['{"action":"delete_everything","arguments":{}}']
    )
    with pytest.raises(ValueError, match="Unknown action"):
        asyncio.run(run_with_fake_session(backend))


def test_step_limit_stops_model_that_never_finishes():
    backend = FakeModelBackend(
        responses=[
            '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
            '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}',
        ]
    )
    result = asyncio.run(run_with_backend(backend, max_steps=2))

    assert result.status == "step_limit"
    assert result.steps == 2
    assert len(result.history) == 2


def test_tool_failure_becomes_observation_and_loop_continues():
    backend = FakeModelBackend(
        responses=[
            '{"action":"read_text","arguments":{"path":"missing.txt"}}',
            '{"action":"finish","arguments":{}}',
        ]
    )
    result = asyncio.run(run_with_backend(backend))

    assert result.status == "completed"
    assert result.steps == 2
    assert result.history[0]["result"]["is_error"] is True
    assert "File does not exist" in str(result.history[0]["result"])
    assert "missing.txt" in backend.received_prompts[1]


def test_fake_response_exhaustion_is_explicit():
    backend = FakeModelBackend(
        responses=[
            '{"action":"read_text","arguments":{"path":"agent_test/calculator.py"}}'
        ]
    )
    with pytest.raises(
        FakeModelResponsesExhausted,
        match="response queue is exhausted",
    ):
        asyncio.run(run_with_fake_session(backend))


class FakeSession:
    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            is_error=False,
            structured_content={"name": name, "arguments": arguments},
            content=[],
        )


async def run_with_fake_session(backend):
    tools = [
        {
            "name": "read_text",
            "description": "Read text",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    return await run_agent(
        session=FakeSession(),
        reasoner=GenericLLMReasoner(backend),
        task="Test the loop",
        tools=tools,
    )
