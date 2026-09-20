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

_COMPLETION_FAILURE_STATUSES = _SEMANTIC_FAILURE_STATUSES | {
    "missing",
    "result_missing",
    "result_mismatch",
    "not_service_control",
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


def _completion_contract(result: dict[str, Any]) -> dict[str, Any] | None:
    data = result.get("data")
    if not isinstance(data, dict) or "completion" not in data:
        return None

    completion = data.get("completion")
    if not isinstance(completion, dict):
        raise ValueError("completion must be an object")
    if set(completion) != {"capability_id", "arguments"}:
        raise ValueError("completion contains unsupported fields")

    capability_id = completion.get("capability_id")
    arguments = completion.get("arguments")
    if (
        not isinstance(capability_id, str)
        or not capability_id.strip()
        or len(capability_id) > 512
        or "\x00" in capability_id
    ):
        raise ValueError("completion capability_id is invalid")
    if not isinstance(arguments, dict):
        raise ValueError("completion arguments must be an object")
    serialized = json.dumps(arguments, ensure_ascii=False, default=repr)
    if len(serialized) > 100_000:
        raise ValueError("completion arguments are too large")

    return {
        "capability_id": capability_id.strip(),
        "arguments": arguments,
    }


def _validate_completion_verifier(
    broker: CapabilityBroker,
    contract: dict[str, Any],
) -> dict[str, Any]:
    capability_id = contract["capability_id"]
    arguments = contract["arguments"]
    descriptor = broker.validate_invocation(
        capability_id,
        arguments,
        confirmation=None,
        transaction_context=False,
    )
    if (
        descriptor.get("risk_level") != "read"
        or descriptor.get("requires_confirmation") is True
        or descriptor.get("requires_transaction") is True
    ):
        raise ValueError(
            "Recorded completion verifier must be a read-only capability "
            "without confirmation or transaction requirements"
        )
    return descriptor


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
    completion_contract = None
    completion_descriptor = None
    if semantic_status in _SEMANTIC_PENDING_STATUSES:
        try:
            completion_contract = _completion_contract(result)
            if completion_contract is not None:
                completion_descriptor = _validate_completion_verifier(
                    broker,
                    completion_contract,
                )
        except Exception as exc:
            failed = transaction_store.checkpoint(
                transaction_id,
                running["revision"],
                step_id,
                "failed",
                (
                    f"Capability {capability_id} returned an invalid external "
                    f"completion contract."
                ),
                {
                    **started_evidence,
                    "semantic_status": semantic_status,
                    "completion_contract_error": type(exc).__name__,
                    "completion_contract_message": str(exc)[:2000],
                    "result_sha256": _sha256_json(result),
                },
            )
            return {
                "status": "failed",
                "capability_id": capability_id,
                "result": result,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:20_000],
                },
                "transaction": failed,
            }

    result_evidence = {
        **started_evidence,
        "capability_status": result.get("status"),
        "semantic_status": semantic_status,
        "provider_reported_error": bool(result.get("is_error")),
        "result_sha256": _sha256_json(result),
        "artifact_ids": _artifact_ids(result),
        "external_completion_required": completion_contract is not None,
        "completion_contract": completion_contract,
        "completion_verifier_provider_id": (
            completion_descriptor.get("provider_id")
            if completion_descriptor is not None
            else None
        ),
        "completion_verifier_risk_level": (
            completion_descriptor.get("risk_level")
            if completion_descriptor is not None
            else None
        ),
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
        if completion_contract is not None:
            pending = transaction_store.checkpoint(
                transaction_id,
                running["revision"],
                step_id,
                "external_pending",
                f"Capability {capability_id} is awaiting external completion.",
                result_evidence,
            )
            return {
                "status": "external_interaction_pending",
                "capability_id": capability_id,
                "result": result,
                "transaction": pending,
                "checkpoint_required": False,
                "completion_required": True,
                "completion_contract": completion_contract,
            }
        return {
            "status": "external_interaction_pending",
            "capability_id": capability_id,
            "result": result,
            "transaction": transaction_store.get(transaction_id),
            "checkpoint_required": True,
            "checkpoint_evidence": result_evidence,
            "completion_required": False,
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


async def complete_external_capability_in_transaction(
    transaction_store: ActionTransactionStore,
    broker: CapabilityBroker,
    *,
    transaction_id: str,
    expected_revision: int,
    step_id: str,
) -> dict[str, Any]:
    """Verify one recorded external completion contract and close its action step."""
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
    if step["kind"] != "action" or step["state"] != "running":
        raise ValueError(
            "External completion requires one running action step"
        )

    pending_evidence = step.get("evidence") or {}
    if pending_evidence.get("external_completion_required") is not True:
        raise ValueError("Transaction step has no required external completion")
    contract = pending_evidence.get("completion_contract")
    if not isinstance(contract, dict):
        raise ValueError("Transaction step completion contract is missing")

    capability_id = contract.get("capability_id")
    arguments = contract.get("arguments")
    if not isinstance(capability_id, str) or not isinstance(arguments, dict):
        raise ValueError("Transaction step completion contract is invalid")

    descriptor = broker.validate_invocation(
        capability_id,
        arguments,
        confirmation=None,
        transaction_context=False,
    )
    if (
        descriptor.get("risk_level") != "read"
        or descriptor.get("requires_confirmation") is True
        or descriptor.get("requires_transaction") is True
    ):
        raise ValueError(
            "Recorded completion verifier must be a read-only capability "
            "without confirmation or transaction requirements"
        )

    try:
        result = await broker.invoke(
            capability_id,
            arguments,
            confirmation=None,
            transaction_context=False,
        )
    except Exception as exc:
        return {
            "status": "external_verification_error",
            "capability_id": capability_id,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:20_000],
            },
            "transaction": transaction_store.get(transaction_id),
        }

    semantic_status = _semantic_status(result)
    verification_evidence = {
        **pending_evidence,
        "completion_verifier_capability_id": capability_id,
        "completion_verifier_arguments_sha256": _sha256_json(arguments),
        "completion_verifier_status": semantic_status,
        "completion_result_sha256": _sha256_json(result),
        "completion_provider_reported_error": bool(result.get("is_error")),
    }

    if semantic_status in _SEMANTIC_PENDING_STATUSES:
        return {
            "status": "external_interaction_pending",
            "capability_id": capability_id,
            "result": result,
            "transaction": transaction_store.get(transaction_id),
        }

    if (
        not result.get("is_error")
        and result.get("status") != "failed"
        and semantic_status == "completed"
    ):
        completed = transaction_store.checkpoint(
            transaction_id,
            expected_revision,
            step_id,
            "external_verified",
            f"External completion verified by {capability_id}.",
            verification_evidence,
        )
        return {
            "status": "completed",
            "capability_id": capability_id,
            "result": result,
            "transaction": completed,
        }

    if (
        semantic_status in _COMPLETION_FAILURE_STATUSES
        or result.get("status") == "failed"
    ):
        failed = transaction_store.checkpoint(
            transaction_id,
            expected_revision,
            step_id,
            "failed",
            f"External completion verifier {capability_id} reported failure.",
            verification_evidence,
        )
        return {
            "status": "failed",
            "capability_id": capability_id,
            "result": result,
            "transaction": failed,
        }

    return {
        "status": "external_verification_unresolved",
        "capability_id": capability_id,
        "result": result,
        "transaction": transaction_store.get(transaction_id),
    }
