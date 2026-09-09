import pytest

from agent_loop import validate_decision


TOOLS = [
    {
        "name": "replace_text",
        "description": "Replace text",
        "input_schema": {},
    }
]


def test_validate_decision_accepts_known_action():
    assert validate_decision(
        {"action": "replace_text", "arguments": {"path": "file.py"}},
        TOOLS,
    ) == ("replace_text", {"path": "file.py"})


def test_validate_decision_accepts_finish():
    assert validate_decision(
        {"action": "finish", "arguments": {}},
        TOOLS,
    ) == ("finish", {})


def test_validate_decision_rejects_unknown_action():
    with pytest.raises(
        ValueError,
        match="Unknown action requested by reasoner: delete_everything",
    ):
        validate_decision(
            {"action": "delete_everything", "arguments": {}},
            TOOLS,
        )


def test_validate_decision_rejects_non_dict_arguments():
    with pytest.raises(TypeError, match="arguments must be a dict"):
        validate_decision(
            {"action": "replace_text", "arguments": []},
            TOOLS,
        )
