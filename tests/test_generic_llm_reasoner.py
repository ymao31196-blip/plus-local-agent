import asyncio

import pytest

from decision_parser import DecisionParseError, parse_decision
from fake_model_backend import FakeModelBackend
from generic_llm_reasoner import GenericLLMReasoner


TOOLS = [
    {
        "name": "read_text",
        "description": "Read a text file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "run_process",
        "description": "Run an allowed process",
        "input_schema": {
            "type": "object",
            "properties": {
                "program": {"type": "string"},
                "args": {"type": "array"},
            },
            "required": ["program"],
        },
    },
]


def test_parse_valid_decision():
    assert parse_decision(
        '{"action":"read_text","arguments":{"path":"x.py"}}'
    ) == {"action": "read_text", "arguments": {"path": "x.py"}}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ("", "empty"),
        ("not json", "not valid JSON"),
        (
            '```json\n{"action":"finish","arguments":{}}\n```',
            "not valid JSON",
        ),
        ('["finish", {}]', "must be an object"),
        ('{"arguments":{}}', "missing fields"),
        ('{"action":1,"arguments":{}}', "must be a string"),
        ('{"action":"finish"}', "missing fields"),
        ('{"action":"finish","arguments":[]}', "must be an object"),
        (
            '{"thought":"done","action":"finish","arguments":{}}',
            "unknown fields",
        ),
    ],
)
def test_parse_rejects_invalid_decisions(response, message):
    with pytest.raises(DecisionParseError, match=message):
        parse_decision(response)


def test_fake_model_records_complete_prompt():
    backend = FakeModelBackend(
        responses=['{"action":"finish","arguments":{}}']
    )
    reasoner = GenericLLMReasoner(backend)

    decision = asyncio.run(
        reasoner.decide(
            task="Inspect and test the project",
            observations=[{"step": 1, "result": "prior observation"}],
            tools=TOOLS,
        )
    )

    assert decision == {"action": "finish", "arguments": {}}
    assert len(backend.received_prompts) == 1
    prompt = backend.received_prompts[0]
    assert "Inspect and test the project" in prompt
    assert "prior observation" in prompt
    assert "read_text" in prompt
    assert "run_process" in prompt
    assert "input_schema" in prompt
    assert '"required"' in prompt
    assert "Do not use a Markdown code block" in prompt
