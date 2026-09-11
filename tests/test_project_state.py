from pathlib import Path

import pytest
import yaml

import local_tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(local_tools, "WORKSPACE", root)
    return root


def test_project_state_absent_then_init_update_and_checkpoint(workspace):
    absent = local_tools.project_state_get()
    assert absent["status"] == "absent"

    created = local_tools.project_state_init(
        project_name="Example Project",
        objective="Make project progress durable across chats.",
    )
    assert created["status"] == "completed"
    assert created["state"]["revision"] == 1
    assert created["state"]["lifecycle"] == "candidate"
    assert (workspace / ".project-agent" / "STATE.yaml").is_file()
    assert (workspace / ".project-agent" / "DECISIONS.md").is_file()
    assert (workspace / ".project-agent" / "EVIDENCE.jsonl").is_file()
    assert (workspace / ".project-agent" / "CHECKPOINTS").is_dir()

    updated = local_tools.project_state_update(
        1,
        current_phase="implementation",
        next_action="run acceptance tests",
        blockers=["none"],
    )
    assert updated["state"]["revision"] == 2
    assert updated["state"]["current_phase"] == "implementation"

    checkpoint = local_tools.project_checkpoint(
        "runtime-baseline",
        "Implementation checkpoint after local tests.",
        2,
        checks=["pytest completed"],
    )
    assert checkpoint["status"] == "completed"
    assert checkpoint["verification_status"] == "unverified"
    checkpoint_path = workspace / checkpoint["checkpoint"]
    assert checkpoint_path.is_file()
    payload = yaml.safe_load(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["state_revision"] == 2
    assert payload["state"]["next_action"] == "run acceptance tests"
    assert payload["verification_status"] == "unverified"

    current = local_tools.project_state_get()
    assert current["state"]["revision"] == 2
    assert checkpoint_path.name in current["checkpoints"]


def test_project_state_revision_cas_and_verified_reserved(workspace):
    local_tools.project_state_init(project_name="CAS")
    local_tools.project_state_update(1, current_phase="phase one")

    with pytest.raises(ValueError, match="changed since inspection"):
        local_tools.project_state_update(1, next_action="stale write")

    with pytest.raises(ValueError, match="independent verifier"):
        local_tools.project_state_update(2, lifecycle="verified")

    assert local_tools.project_state_get()["state"]["revision"] == 2


def test_project_checkpoint_rejects_stale_revision_without_file(workspace):
    local_tools.project_state_init(project_name="Checkpoint CAS")
    local_tools.project_state_update(1, current_phase="changed")
    checkpoint_dir = workspace / ".project-agent" / "CHECKPOINTS"

    with pytest.raises(ValueError, match="changed since inspection"):
        local_tools.project_checkpoint("stale", "must not be written", 1)

    assert list(checkpoint_dir.iterdir()) == []


def test_project_state_init_does_not_overwrite_existing_state(workspace):
    local_tools.project_state_init(project_name="Existing")
    state_path = workspace / ".project-agent" / "STATE.yaml"
    before = state_path.read_bytes()

    with pytest.raises(ValueError, match="already exists"):
        local_tools.project_state_init(project_name="Replacement")

    assert state_path.read_bytes() == before


def test_project_decision_record_is_append_only_and_revision_bound(workspace):
    local_tools.project_state_init(project_name="Decisions")
    before = local_tools.project_state_get()
    recorded = local_tools.project_decision_record(
        "Keep transaction boundary",
        "Use apply_changeset for one logical edit spanning multiple existing files.",
        "Avoid partial multi-file mutations.",
        1,
    )

    assert recorded["status"] == "completed"
    assert recorded["state_revision"] == 1
    assert local_tools.project_state_get()["state"]["revision"] == 1
    decisions = local_tools.project_decisions_get()
    assert "Keep transaction boundary" in decisions["content"]
    assert "Avoid partial multi-file mutations." in decisions["content"]

    path = workspace / ".project-agent" / "DECISIONS.md"
    stable = path.read_bytes()
    local_tools.project_state_update(1, current_phase="changed")
    with pytest.raises(ValueError, match="changed since inspection"):
        local_tools.project_decision_record("stale", "must not append", "", 1)
    assert path.read_bytes() == stable


def test_project_evidence_record_and_filters_are_unverified(workspace):
    local_tools.project_state_init(project_name="Evidence")
    first = local_tools.project_evidence_record(
        "test", "pass", "Project-state tests passed.", "pytest tests/test_project_state.py", 1,
        details="4 passed",
        artifact_sha256="A" * 64,
    )
    second = local_tools.project_evidence_record(
        "command", "info", "Schema inspected.", "live MCP schema", 1,
    )

    assert first["evidence"]["verification_status"] == "unverified"
    assert first["evidence"]["artifact_sha256"] == "a" * 64
    assert second["evidence"]["state_revision"] == 1
    assert local_tools.project_state_get()["state"]["revision"] == 1

    tests = local_tools.project_evidence_get(kind="test")
    assert tests["total_count"] == 1
    assert tests["records"][0]["status"] == "pass"
    passing = local_tools.project_evidence_get(status="pass")
    assert passing["records"][0]["kind"] == "test"


def test_project_evidence_rejects_stale_or_invalid_without_append(workspace):
    local_tools.project_state_init(project_name="Evidence Guard")
    evidence_path = workspace / ".project-agent" / "EVIDENCE.jsonl"

    with pytest.raises(ValueError, match="Unsupported evidence kind"):
        local_tools.project_evidence_record(
            "verified", "pass", "invalid", "manual", 1,
        )
    assert evidence_path.read_bytes() == b""

    local_tools.project_state_update(1, current_phase="advanced")
    with pytest.raises(ValueError, match="changed since inspection"):
        local_tools.project_evidence_record(
            "test", "pass", "stale", "pytest", 1,
        )
    assert evidence_path.read_bytes() == b""
