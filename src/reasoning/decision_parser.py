import json
from typing import Any


class DecisionParseError(ValueError):
    """Raised when model text does not exactly match the decision protocol."""


def parse_decision(raw_response: str) -> dict[str, Any]:
    """Parse a strict ``action``/``arguments`` JSON decision object."""
    if not isinstance(raw_response, str):
        raise DecisionParseError("Model response must be a string")
    if not raw_response.strip():
        raise DecisionParseError("Model response is empty")

    try:
        decision = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DecisionParseError(
            f"Model response is not valid JSON: {exc.msg} at line "
            f"{exc.lineno} column {exc.colno}"
        ) from exc

    if not isinstance(decision, dict):
        raise DecisionParseError("Decision JSON must be an object")

    expected_fields = {"action", "arguments"}
    actual_fields = set(decision)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unknown = sorted(actual_fields - expected_fields)
        details = []
        if missing:
            details.append(f"missing fields: {missing}")
        if unknown:
            details.append(f"unknown fields: {unknown}")
        raise DecisionParseError(
            "Decision must contain exactly 'action' and 'arguments' ("
            + "; ".join(details)
            + ")"
        )

    if not isinstance(decision["action"], str):
        raise DecisionParseError("Decision 'action' must be a string")
    if not decision["action"]:
        raise DecisionParseError("Decision 'action' must not be empty")
    if not isinstance(decision["arguments"], dict):
        raise DecisionParseError("Decision 'arguments' must be an object")

    return decision
