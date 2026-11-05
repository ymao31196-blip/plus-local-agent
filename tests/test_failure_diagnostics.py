"""Regression tests for bounded, privacy-safe failure classification."""
import json

import pytest

from runtime import activity_runtime as activity
from runtime.event_runtime import EventStore
from runtime.failure_diagnostics import (
    exception_diagnostic, failure_projection, result_diagnostic,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("Program not allowed: SECRET.exe"),
         {"failure_class": "policy_denied", "policy_reason": "program_not_allowed"}),
        (ValueError("Root 'D' does not allow write access"),
         {"failure_class": "policy_denied", "policy_reason": "root_access_denied"}),
        (ValueError("Protected path is not available through root 'pla': secret.key"),
         {"failure_class": "policy_denied", "policy_reason": "protected_path"}),
        (ValueError("Path outside workspace is not allowed: C:\\SECRET"),
         {"failure_class": "policy_denied", "policy_reason": "outside_root"}),
        (PermissionError("PRIVATE USER FILE"),
         {"failure_class": "policy_denied", "policy_reason": "policy_denied_other"}),
        (ValueError("bad root name SECRET"),
         {"failure_class": "validation_error"}),
        (ValueError("Path does not exist: SECRET"),
         {"failure_class": "missing_resource"}),
        (RuntimeError("@oai/artifact-tool is not available in the SECRET environment"),
         {"failure_class": "dependency_error"}),
        (PermissionError("Skill is disabled"),
         {"failure_class": "policy_denied", "policy_reason": "component_disabled"}),
        (RuntimeError("Error calling tool 'skill_read': PermissionError: Skill is disabled"),
         {"failure_class": "policy_denied", "policy_reason": "component_disabled"}),
        (UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid"),
         {"failure_class": "decode_error"}),
        (FileNotFoundError("SECRET"),
         {"failure_class": "missing_resource"}),
        (TimeoutError("SECRET"),
         {"failure_class": "timeout"}),
        (ModuleNotFoundError("SECRET"),
         {"failure_class": "dependency_error"}),
        (RuntimeError("SECRET"),
         {"failure_class": "execution_error"}),
    ],
)
def test_exception_classification_is_closed_vocabulary(error, expected):
    assert exception_diagnostic(error) == expected
    assert "SECRET" not in str(exception_diagnostic(error))


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"returncode": 17, "stderr": "PRIVATE"},
         {"failure_class": "process_exit", "exit_code": 17}),
        ({"returncode": -9, "stdout": "PRIVATE"},
         {"failure_class": "process_exit", "exit_code": -9}),
        ({"status": "blocked", "reason": "specialized_capability_required", "message": "PRIVATE"},
         {"failure_class": "policy_denied", "policy_reason": "specialized_capability_required"}),
        ({"error": {"type": "ProcessExitError", "message": "PRIVATE"},
          "returncode": 3},
         {"failure_class": "process_exit", "exit_code": 3}),
        ({"error": {"type": "PermissionError", "message": "PRIVATE"}},
         {"failure_class": "policy_denied", "policy_reason": "policy_denied_other"}),
        ({"timeout": True, "stderr": "PRIVATE"}, {"failure_class": "timeout"}),
        ({"status": "cancelled"}, {"failure_class": "cancelled"}),
        ({"is_error": True, "content": ["PRIVATE"]}, {"failure_class": "provider_error"}),
        ({"data": {"returncode": 44, "stderr": "PRIVATE"}, "status": "completed"},
         {"failure_class": "process_exit", "exit_code": 44}),
        ({"returncode": True, "is_error": True},
         {"failure_class": "provider_error"}),
        ({"returncode": 2**80}, {"failure_class": "unknown_failure"}),
    ],
)
def test_structured_result_classification_redacts_content(payload, expected):
    assert result_diagnostic(payload) == expected


def test_projection_rejects_unknown_or_malicious_metadata():
    assert failure_projection({
        "failure_class": "process_exit", "exit_code": True,
        "stdout": "SECRET", "policy_reason": "blocked",
    }) == {"failure_class": "process_exit"}
    assert failure_projection({
        "failure_class": ["process_exit"], "policy_reason": {"SECRET": 1},
    }) == {}
    assert failure_projection({"failure_class": "custom_PRIVATE_category"}) == {}


def test_native_activity_records_class_and_code_without_private_text(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)

    @activity.record_native_activity
    def run_process(program, args=None):
        return {"returncode": 23, "stderr": "SECRET_STDERR"}

    @activity.record_native_activity
    def read_text():
        raise ValueError("Program not allowed: SECRET_PROGRAM")

    try:
        assert run_process("python", ["SECRET_ARGUMENT"])["returncode"] == 23
        with pytest.raises(ValueError, match="Program not allowed"):
            read_text()
        events = store.query()["events"]
        failed = [e["payload"] for e in events if e["event_type"] == "native_tool.failed"]
        assert failed == [
            {"failure_class": "process_exit", "exit_code": 23},
            {"failure_class": "policy_denied", "policy_reason": "program_not_allowed"},
        ]
        assert "SECRET" not in str(events)
        items = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        assert items[0]["policy_reason"] == "program_not_allowed"
        assert items[1]["exit_code"] == 23
    finally:
        store.close()


def test_internal_action_records_class_without_error_message(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr(activity, "EVENT_STORE", store)
    try:
        call = activity.record_internal_action_start("run_process", {"program": "python"})
        activity.record_internal_action_finish(
            "run_process", call, False, result={"returncode": 13, "stderr": "SECRET"},
            error={"type": "ProcessExitError", "message": "SECRET"},
        )
        item = activity.recent_activity(tmp_path / "events.sqlite3")["activities"][0]
        assert (item["failure_class"], item["exit_code"]) == ("process_exit", 13)
        events = store.query()["events"]
        assert events[-1]["payload"] == {"failure_class": "process_exit", "exit_code": 13}
        assert "SECRET" not in str(events)
    finally:
        store.close()


def test_capability_denial_projection_handles_legacy_and_new_events(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        store.emit(
            "capability.gate_denied", source="capability_broker",
            subject="test.write", correlation_id="one",
            payload={"failure_class": "policy_denied", "policy_reason": "gate_hook_denied"},
        )
        store.emit(
            "capability.policy_denied", source="capability_broker",
            subject="test.write", correlation_id="two",
            payload={"failure_class": "policy_denied",
                     "policy_reason": "confirmation_required"},
        )
        store.emit(
            "capability.before_invoke", source="capability_broker",
            subject="test.old", correlation_id="three",
        )
        store.emit(
            "capability.failed", source="capability_broker",
            subject="test.old", correlation_id="three",
            payload={"exception_type": "ValueError", "message_sha256": "f" * 64},
        )
        items = activity.recent_activity(tmp_path / "events.sqlite3")["activities"]
        by_id = {item["id"]: item for item in items}
        assert by_id["two"]["policy_reason"] == "confirmation_required"
        assert by_id["one"]["policy_reason"] == "gate_hook_denied"
        assert "failure_class" not in by_id["three"]
    finally:
        store.close()
