import asyncio

import pytest

from transaction_action_envelope import (
    complete_external_capability_in_transaction,
    invoke_capability_in_transaction,
)
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
        transaction_id=None,
    ):
        assert transaction_context is True
        assert transaction_id
        self.calls.append((capability_id, arguments, confirmation, transaction_id))
        if self.error is not None:
            raise self.error
        return dict(self.result)


class _CompletionBroker:
    def __init__(self, action_result, verifier_result, verifier_descriptor=None):
        self.descriptors = {
            "fixture.write": _descriptor(),
            "fixture.status": (
                verifier_descriptor
                or _descriptor(
                    id="fixture.status",
                    risk_level="read",
                    requires_confirmation=False,
                    requires_transaction=False,
                )
            ),
        }
        self.action_result = action_result
        self.verifier_result = verifier_result
        self.calls = []

    def validate_invocation(
        self,
        capability_id,
        arguments,
        *,
        confirmation=None,
        transaction_context=False,
    ):
        descriptor = dict(self.descriptors[capability_id])
        if capability_id == "fixture.write":
            assert transaction_context is True
        else:
            assert transaction_context is False
        if descriptor["requires_confirmation"] and confirmation != "INVOKE":
            raise PermissionError(
                f"Capability {capability_id} requires confirmation='INVOKE'"
            )
        if descriptor["requires_transaction"] and not transaction_context:
            raise PermissionError(
                f"Capability {capability_id} requires a transaction context"
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
        transaction_id=None,
    ):
        self.calls.append(
            (
                capability_id,
                dict(arguments),
                confirmation,
                transaction_context,
                transaction_id,
            )
        )
        if capability_id == "fixture.write":
            assert transaction_context is True
            assert transaction_id
            return dict(self.action_result)
        assert transaction_context is False
        assert transaction_id is None
        return dict(self.verifier_result)


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
        assert broker.calls == [
            (
                "fixture.write",
                {"path": "x"},
                None,
                record["transaction_id"],
            )
        ]
    finally:
        store.close()


def test_external_completion_contract_persists_and_blocks_manual_success():
    store, record = _store_with_step()
    broker = _CompletionBroker(
        action_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "external_pending",
                "launch_id": "a" * 32,
                "completion": {
                    "capability_id": "fixture.status",
                    "arguments": {"launch_id": "a" * 32},
                },
            },
        },
        verifier_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.status",
            "artifacts": [],
            "is_error": False,
            "data": {"status": "external_pending"},
        },
    )
    try:
        pending = asyncio.run(
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

        assert pending["status"] == "external_interaction_pending"
        assert pending["checkpoint_required"] is False
        assert pending["completion_required"] is True
        step = pending["transaction"]["steps"][0]
        assert step["state"] == "running"
        assert step["evidence"]["external_completion_required"] is True
        assert step["evidence"]["completion_contract"] == {
            "capability_id": "fixture.status",
            "arguments": {"launch_id": "a" * 32},
        }

        with pytest.raises(ValueError, match="completion gate"):
            store.checkpoint(
                record["transaction_id"],
                pending["transaction"]["revision"],
                "step",
                "succeeded",
                "manual bypass",
                {},
            )
    finally:
        store.close()


def test_external_completion_gate_uses_recorded_read_verifier_and_completes():
    store, record = _store_with_step()
    broker = _CompletionBroker(
        action_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "external_pending",
                "completion": {
                    "capability_id": "fixture.status",
                    "arguments": {"launch_id": "b" * 32},
                },
            },
        },
        verifier_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.status",
            "artifacts": [],
            "is_error": False,
            "data": {"status": "completed", "verified": True},
        },
    )
    try:
        pending = asyncio.run(
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
        completed = asyncio.run(
            complete_external_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=pending["transaction"]["revision"],
                step_id="step",
            )
        )

        assert completed["status"] == "completed"
        assert completed["transaction"]["steps"][0]["state"] == "succeeded"
        assert broker.calls[-1] == (
            "fixture.status",
            {"launch_id": "b" * 32},
            None,
            False,
            None,
        )
        evidence = completed["transaction"]["steps"][0]["evidence"]
        assert evidence["completion_verifier_capability_id"] == "fixture.status"
        assert evidence["completion_verifier_status"] == "completed"
    finally:
        store.close()


def test_external_completion_gate_keeps_running_while_verifier_is_pending():
    store, record = _store_with_step()
    broker = _CompletionBroker(
        action_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "external_pending",
                "completion": {
                    "capability_id": "fixture.status",
                    "arguments": {"launch_id": "c" * 32},
                },
            },
        },
        verifier_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.status",
            "artifacts": [],
            "is_error": False,
            "data": {"status": "external_pending"},
        },
    )
    try:
        pending = asyncio.run(
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
        still_pending = asyncio.run(
            complete_external_capability_in_transaction(
                store,
                broker,
                transaction_id=record["transaction_id"],
                expected_revision=pending["transaction"]["revision"],
                step_id="step",
            )
        )

        assert still_pending["status"] == "external_interaction_pending"
        assert still_pending["transaction"]["revision"] == pending["transaction"]["revision"]
        assert still_pending["transaction"]["steps"][0]["state"] == "running"
    finally:
        store.close()


def test_external_completion_gate_rejects_non_read_verifier():
    store, record = _store_with_step()
    broker = _CompletionBroker(
        action_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.write",
            "artifacts": [],
            "is_error": False,
            "data": {
                "status": "external_pending",
                "completion": {
                    "capability_id": "fixture.status",
                    "arguments": {},
                },
            },
        },
        verifier_result={
            "status": "completed",
            "provider_id": "fixture",
            "capability_id": "fixture.status",
            "artifacts": [],
            "is_error": False,
            "data": {"status": "completed"},
        },
        verifier_descriptor=_descriptor(
            id="fixture.status",
            risk_level="write_local",
            requires_confirmation=False,
            requires_transaction=False,
        ),
    )
    try:
        rejected = asyncio.run(
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
        assert rejected["status"] == "failed"
        assert rejected["error"]["type"] == "ValueError"
        assert "read-only capability" in rejected["error"]["message"]
        assert rejected["transaction"]["steps"][0]["state"] == "failed"
        evidence = rejected["transaction"]["steps"][0]["evidence"]
        assert evidence["completion_contract_error"] == "ValueError"
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
