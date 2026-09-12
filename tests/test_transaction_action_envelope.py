import asyncio

import pytest

from transaction_action_envelope import invoke_capability_in_transaction
from transaction_runtime import ActionTransactionStore


class _Registry:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def describe(self, capability_id):
        assert capability_id == self.descriptor["id"]
        return dict(self.descriptor)


class _Broker:
    def __init__(self, descriptor, result=None, error=None):
        self._registry = _Registry(descriptor)
        self.result = result
        self.error = error
        self.calls = []

    def validate_invocation(
        self,
        capability_id,
        arguments,
        *,
        confirmation=None,
        transaction_context=False,
    ):
        assert transaction_context is True
        descriptor = self._registry.describe(capability_id)
        if descriptor["requires_confirmation"] and confirmation != "INVOKE":
            raise PermissionError(
                f"Capability {capability_id} requires confirmation='INVOKE'"
            )
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        return descriptor

    async def invoke(
        self,
        capability_id,
        arguments,
        *,
        confirmation=None,
        transaction_context=False,
    ):
        assert transaction_context is True
        self.calls.append((capability_id, arguments, confirmation))
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _descriptor(**updates):
    value = {
        "id": "fixture.write",
        "provider_id": "fixture",
        "risk_level": "write_local",
        "requires_confirmation": False,
        "requires_transaction": False,
        "available": True,
    }
    value.update(updates)
    return value


def _store_with_step(kind="action"):
    store = ActionTransactionStore()
    record = store.create(
        "test transaction",
        [{"step_id": "step", "title": "Do work", "kind": kind}],
    )
    return store, record


def test_action_envelope_marks_success_and_records_hash():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(),
        result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={"path": "x"},
            )
        )

        assert result["status"] == "completed"
        assert result["transaction"]["steps"][0]["state"] == "succeeded"
        evidence = result["transaction"]["steps"][0]["evidence"]
        assert evidence["capability_id"] == "fixture.write"
        assert len(evidence["arguments_sha256"]) == 64
        assert len(evidence["result_sha256"]) == 64
        assert broker.calls == [("fixture.write", {"path": "x"}, None)]
    finally:
        store.close()


def test_verify_step_returns_observation_and_requires_agent_checkpoint():
    store, record = _store_with_step("verify")
    broker = _Broker(
        _descriptor(id="fixture.verify", risk_level="read"),
        result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.verify",
            "artifacts": [],
            "is_error": False,
            "data": {"verified": False},
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.verify",
                arguments={},
            )
        )
        assert result["status"] == "verification_observation_ready"
        assert result["checkpoint_required"] is True
        assert result["transaction"]["steps"][0]["state"] == "running"
        assert result["result"]["data"]["verified"] is False
    finally:
        store.close()


def test_missing_confirmation_does_not_start_transaction_step():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(requires_confirmation=True, risk_level="destructive"),
        result={},
    )
    try:
        with pytest.raises(PermissionError, match="requires confirmation"):
            asyncio.run(
                invoke_capability_in_transaction(
                    store,
                    broker,
                    transaction_id=record["transaction_id"],
                    expected_revision=record["revision"],
                    step_id="step",
                    capability_id="fixture.write",
                    arguments={},
                )
            )

        reread = store.get(record["transaction_id"])
        assert reread["revision"] == 1
        assert reread["steps"][0]["state"] == "pending"
        assert broker.calls == []
    finally:
        store.close()


def test_provider_reported_error_marks_step_failed():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(),
        result={
            "status": "failed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": True,
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={},
            )
        )

        assert result["status"] == "failed"
        assert result["transaction"]["status"] == "blocked"
        assert result["transaction"]["steps"][0]["state"] == "failed"
    finally:
        store.close()


def test_provider_exception_is_captured_and_step_failed():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(),
        error=RuntimeError("provider exploded"),
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={},
            )
        )

        assert result["status"] == "failed"
        assert result["error"]["type"] == "RuntimeError"
        assert result["transaction"]["steps"][0]["state"] == "failed"
    finally:
        store.close()


def test_transaction_required_descriptor_is_allowed_inside_envelope():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(
            requires_confirmation=True,
            requires_transaction=True,
            risk_level="destructive",
        ),
        result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={"target": "x"},
                confirmation="INVOKE",
            )
        )
        assert result["status"] == "completed"
        assert result["transaction"]["steps"][0]["state"] == "succeeded"
    finally:
        store.close()


def test_nested_semantic_failure_marks_step_failed():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(),
        result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "precondition_failed",
                "message": "business precondition failed",
            },
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={},
            )
        )
        assert result["status"] == "failed"
        assert result["transaction"]["status"] == "blocked"
        assert result["transaction"]["steps"][0]["state"] == "failed"
        assert result["transaction"]["steps"][0]["evidence"]["semantic_status"] == "precondition_failed"
    finally:
        store.close()


def test_external_pending_keeps_step_running_for_agent_checkpoint():
    store, record = _store_with_step()
    broker = _Broker(
        _descriptor(),
        result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "external_pending",
                "launch_id": "a" * 32,
            },
        },
    )
    try:
        result = asyncio.run(
            invoke_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=record["revision"],
                step_id="step",
                capability_id="fixture.write",
                arguments={},
            )
        )

        assert result["status"] == "external_interaction_pending"
        assert result["checkpoint_required"] is True
        assert result["transaction"]["status"] == "active"
        assert result["transaction"]["steps"][0]["state"] == "running"
        assert result["checkpoint_evidence"]["semantic_status"] == "external_pending"
    finally:
        store.close()
