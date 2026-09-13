"""Bind one explicit capability invocation to one durable transaction step."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from capability_broker import CapabilityBroker
from transaction_runtime import ActionTransactionStore


_SUCCESS_OUTCOME_BY_KIND = {
    "action": "succeeded",
    "rollback": "rolled_back",
}

_SEMANTIC_FAILURE_STATUSES = {
    "failed",
    "error",
    "timeout",
    "precondition_failed",
    "blocked",
    "not_ready",
}

_SEMANTIC_PENDING_STATUSES = {
    "external_pending",
    "pending_external",
}


def _semantic_status(result: dict[str, Any]) -> str | None:
    data = result.get("data")
    if isinstance(data, dict):
        status = data.get("status")
        if isinstance(status, str):
            return status.strip().lower()
    return None


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact_ids(result: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in result.get("artifacts", []) or []:
        if isinstance(item, dict):
            artifact_id = item.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                ids.append(artifact_id)
    return ids[:50]


async def invoke_capability_in_transaction(
    transaction_store: ActionTransactionStore,
    broker: CapabilityBroker,
    *,
    transaction_id: str,
    expected_revision: int,
    step_id: str,
    capability_id: str,
    arguments: dict[str, Any],
    confirmation: str | None = None,
) -> dict[str, Any]:
    """Execute exactly one caller-selected capability inside a transaction step.

    This function never selects the capability or plans a follow-up action.
    """
    record = transaction_store.get(transaction_id)
    if record["revision"] != expected_revision:
        raise ValueError(
            f"Transaction changed since inspection: expected revision "
            f"{expected_revision}, current {record['revision']}"
        )

    step = next(
        (item for item in record["steps"] if item["step_id"] == step_id),
        None,
    )
    if step is None:
        raise ValueError(f"Unknown transaction step: {step_id}")
    if step["state"] != "pending":
        raise ValueError("Transaction capability invocation requires a pending step")

    descriptor = broker.validate_invocation(
        capability_id,
        arguments,
        confirmation=confirmation,
        transaction_context=True,
    )

    success_outcome = _SUCCESS_OUTCOME_BY_KIND.get(step["kind"])
    arguments_sha256 = _sha256_json(arguments)
    started_evidence = {
        "capability_id": capability_id,
        "provider_id": descriptor["provider_id"],
        "risk_level": descriptor["risk_level"],
        "requires_confirmation": descriptor["requires_confirmation"],
        "confirmation_supplied": confirmation == "INVOKE",
        "arguments_sha256": arguments_sha256,
    }
    running = transaction_store.checkpoint(
        transaction_id,
        expected_revision,
        step_id,
        "started",
        f"Invoking capability {capability_id}",
        started_evidence,
    )

    try:
        result = await broker.invoke(
            capability_id,
            arguments,
            confirmation=confirmation,
            transaction_context=True,
            transaction_id=transaction_id,
        )
    except Exception as exc:
        failed = transaction_store.checkpoint(
            transaction_id,
            running["revision"],
            step_id,
            "failed",
            f"Capability {capability_id} raised {type(exc).__name__}: {str(exc)[:2000]}",
            {
                **started_evidence,
                "exception_type": type(exc).__name__,
                "result_recorded": False,
            },
        )
        return {
            "status": "failed",
            "capability_id": capability_id,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:20_000],
            },
            "transaction": failed,
        }

    semantic_status = _semantic_status(result)
    result_evidence = {
        **started_evidence,
        "capability_status": result.get("status"),
        "semantic_status": semantic_status,
        "provider_reported_error": bool(result.get("is_error")),
        "result_sha256": _sha256_json(result),
        "artifact_ids": _artifact_ids(result),
    }
    if (
        result.get("is_error")
        or result.get("status") == "failed"
        or semantic_status in _SEMANTIC_FAILURE_STATUSES
    ):
        completed = transaction_store.checkpoint(
            transaction_id,
            running["revision"],
            step_id,
            "failed",
            f"Capability {capability_id} reported failure.",
            result_evidence,
        )
        return {
            "status": "failed",
            "capability_id": capability_id,
            "result": result,
            "transaction": completed,
        }

    if semantic_status in _SEMANTIC_PENDING_STATUSES:
        return {
            "status": "external_interaction_pending",
            "capability_id": capability_id,
            "result": result,
            "transaction": transaction_store.get(transaction_id),
            "checkpoint_required": True,
            "checkpoint_evidence": result_evidence,
        }

    if step["kind"] == "verify":
        # A successful MCP call is only an observation. Domain verification is
        # deliberately left to ChatGPT, which must checkpoint verified/failed
        # after inspecting the returned result.
        return {
            "status": "verification_observation_ready",
            "capability_id": capability_id,
            "result": result,
            "transaction": transaction_store.get(transaction_id),
            "checkpoint_required": True,
            "checkpoint_evidence": result_evidence,
        }

    completed = transaction_store.checkpoint(
        transaction_id,
        running["revision"],
        step_id,
        success_outcome,
        f"Capability {capability_id} completed successfully.",
        result_evidence,
    )
    return {
        "status": "completed",
        "capability_id": capability_id,
        "result": result,
        "transaction": completed,
    }
