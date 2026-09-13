import hashlib
import json

import pytest

from event_runtime import EVENT_SCHEMA_VERSION, EventStore


def test_event_store_emit_and_query_envelope(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        first = store.emit(
            "capability.before_invoke",
            source="capability_broker",
            subject="fixture.echo",
            correlation_id="corr-1",
            capability_id="fixture.echo",
            provider_id="fixture",
            payload={"arguments_sha256": "a" * 64, "argument_keys": ["text"]},
        )
        second = store.emit(
            "capability.succeeded",
            source="capability_broker",
            subject="fixture.echo",
            correlation_id="corr-1",
            causation_id=first["event_id"],
            capability_id="fixture.echo",
            provider_id="fixture",
            payload={"result_sha256": "b" * 64, "semantic_status": "completed"},
        )

        result = store.query()
        assert result["returned_count"] == 2
        assert result["has_more"] is False
        assert result["next_cursor"] == second["sequence"]

        before, succeeded = result["events"]
        assert before["schema_version"] == EVENT_SCHEMA_VERSION
        assert before["sequence"] < succeeded["sequence"]
        assert before["correlation_id"] == succeeded["correlation_id"] == "corr-1"
        assert succeeded["causation_id"] == before["event_id"]
        assert before["event_type"] == "capability.before_invoke"
        assert succeeded["event_type"] == "capability.succeeded"

        canonical = json.dumps(
            before["payload"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert before["payload_sha256"] == hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
    finally:
        store.close()


def test_event_store_query_cursor_and_filters(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        a1 = store.emit(
            "capability.before_invoke",
            source="capability_broker",
            subject="alpha.one",
            correlation_id="corr-a",
            capability_id="alpha.one",
            provider_id="alpha",
            transaction_id="tx-a",
        )
        store.emit(
            "capability.succeeded",
            source="capability_broker",
            subject="alpha.one",
            correlation_id="corr-a",
            causation_id=a1["event_id"],
            capability_id="alpha.one",
            provider_id="alpha",
            transaction_id="tx-a",
        )
        store.emit(
            "capability.failed",
            source="capability_broker",
            subject="beta.two",
            correlation_id="corr-b",
            capability_id="beta.two",
            provider_id="beta",
        )

        page = store.query(limit=1)
        assert page["returned_count"] == 1
        assert page["has_more"] is True

        next_page = store.query(after_sequence=page["next_cursor"], limit=10)
        assert next_page["returned_count"] == 2

        alpha = store.query(provider_id="alpha")
        assert {e["event_type"] for e in alpha["events"]} == {
            "capability.before_invoke",
            "capability.succeeded",
        }

        failed = store.query(event_types=["capability.failed"])
        assert [e["capability_id"] for e in failed["events"]] == ["beta.two"]

        correlated = store.query(correlation_id="corr-a")
        assert len(correlated["events"]) == 2

        transactional = store.query(transaction_id="tx-a")
        assert len(transactional["events"]) == 2
        assert {event["transaction_id"] for event in transactional["events"]} == {
            "tx-a"
        }
    finally:
        store.close()


def test_event_store_is_append_only_across_reopen(tmp_path):
    path = tmp_path / "events.sqlite3"
    first = EventStore(path)
    event = first.emit(
        "capability.before_invoke",
        source="capability_broker",
        subject="fixture.echo",
    )
    first.close()

    reopened = EventStore(path)
    try:
        result = reopened.query()
        assert result["returned_count"] == 1
        assert result["events"][0]["event_id"] == event["event_id"]
        assert result["events"][0]["sequence"] == event["sequence"]
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "event_type",
    ["invalid", "Capability.before", "capability..before", "capability.before-invoke"],
)
def test_event_store_rejects_invalid_event_types(tmp_path, event_type):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        with pytest.raises(ValueError, match="dotted lowercase"):
            store.emit(
                event_type,
                source="test",
                subject="subject",
            )
    finally:
        store.close()


def test_event_store_rejects_oversize_or_non_json_payload(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        with pytest.raises(ValueError, match="cannot exceed"):
            store.emit(
                "capability.before_invoke",
                source="test",
                subject="subject",
                payload={"data": "x" * 100_001},
            )
        with pytest.raises(ValueError, match="JSON serializable"):
            store.emit(
                "capability.before_invoke",
                source="test",
                subject="subject",
                payload={"bad": {1, 2, 3}},
            )
    finally:
        store.close()
