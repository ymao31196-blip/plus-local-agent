import hashlib
from pathlib import Path

import pytest

import local_tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(local_tools, "WORKSPACE", root)
    return root


def _freeze(name="Verifier"):
    local_tools.project_state_init(project_name=name)
    frozen = local_tools.project_state_update(1, lifecycle="frozen")
    assert frozen["state"]["revision"] == 2
    return 2


def _evaluate_one(kind, evidence):
    local_tools.project_acceptance_set(
        "Verify one check",
        [{
            "id": "check",
            "description": "Independent evidence must verify.",
            "evidence_kinds": [kind],
        }],
        expected_state_revision=2,
        expected_contract_revision=0,
    )
    return local_tools.project_acceptance_evaluate(
        [{"check_id": "check", "evidence_ids": [evidence["evidence_id"]]}],
        expected_state_revision=2,
        expected_contract_revision=1,
    )


def test_independent_file_sha256_verifier_promotes_frozen_state(workspace):
    revision = _freeze("Artifact verifier")
    artifact = workspace / "artifact.bin"
    artifact.write_bytes(b"verified artifact\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    evidence = local_tools.project_evidence_record(
        "artifact",
        "pass",
        "Artifact produced.",
        "artifact.bin",
        revision,
        artifact_sha256=digest,
        verification={"type": "file_sha256", "path": "artifact.bin"},
    )["evidence"]
    evaluation = _evaluate_one("artifact", evidence)

    verified = local_tools.project_verify_acceptance(
        evaluation["evaluation"]["evaluation_id"],
        evaluation["sha256"],
        expected_state_revision=2,
        expected_contract_revision=1,
    )

    assert verified["verification_status"] == "verified"
    assert verified["state"]["lifecycle"] == "verified"
    assert verified["state"]["revision"] == 3
    history = local_tools.project_verifications_get()
    assert history["total_count"] == 1
    assert history["verifications"][0]["verification_status"] == "verified"


def test_independent_pytest_verifier_reruns_test(workspace):
    revision = _freeze("Pytest verifier")
    (workspace / "test_sample.py").write_text(
        "def test_ok():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    evidence = local_tools.project_evidence_record(
        "test",
        "pass",
        "Target test passed.",
        "pytest -q test_sample.py",
        revision,
        verification={
            "type": "pytest",
            "args": ["-q", "test_sample.py"],
            "cwd": ".",
            "timeout": 30,
        },
    )["evidence"]
    evaluation = _evaluate_one("test", evidence)

    verified = local_tools.project_verify_acceptance(
        evaluation["evaluation"]["evaluation_id"],
        evaluation["sha256"],
        2,
        1,
    )

    assert verified["verification_status"] == "verified"
    attempts = verified["verification"]["checks"][0]["attempts"]
    assert attempts[0]["verification_type"] == "pytest"
    assert attempts[0]["returncode"] == 0


def test_verifier_keeps_frozen_when_evidence_is_not_independently_reproducible(workspace):
    revision = _freeze("Unsupported verifier")
    evidence = local_tools.project_evidence_record(
        "manual",
        "pass",
        "Human says it looks good.",
        "manual review",
        revision,
    )["evidence"]
    evaluation = _evaluate_one("manual", evidence)

    result = local_tools.project_verify_acceptance(
        evaluation["evaluation"]["evaluation_id"],
        evaluation["sha256"],
        2,
        1,
    )

    assert result["verification_status"] == "incomplete"
    assert result["state"]["lifecycle"] == "frozen"
    assert result["state"]["revision"] == 2


def test_verifier_detects_changed_artifact_and_does_not_promote(workspace):
    revision = _freeze("Artifact mismatch")
    artifact = workspace / "artifact.bin"
    artifact.write_bytes(b"original\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    evidence = local_tools.project_evidence_record(
        "artifact",
        "pass",
        "Artifact produced.",
        "artifact.bin",
        revision,
        artifact_sha256=digest,
        verification={"type": "file_sha256", "path": "artifact.bin"},
    )["evidence"]
    evaluation = _evaluate_one("artifact", evidence)
    artifact.write_bytes(b"changed later\n")

    result = local_tools.project_verify_acceptance(
        evaluation["evaluation"]["evaluation_id"],
        evaluation["sha256"],
        2,
        1,
    )

    assert result["verification_status"] == "failed"
    assert result["state"]["lifecycle"] == "frozen"
    assert result["verification"]["checks"][0]["attempts"][0]["reason"] == "sha256_mismatch"


def test_verified_state_reopens_on_normal_update_and_contract_revision_requires_reopen(workspace):
    revision = _freeze("Reopen verified")
    artifact = workspace / "artifact.bin"
    artifact.write_bytes(b"x")
    digest = hashlib.sha256(b"x").hexdigest()
    evidence = local_tools.project_evidence_record(
        "artifact", "pass", "Artifact.", "artifact.bin", revision,
        artifact_sha256=digest,
        verification={"type": "file_sha256", "path": "artifact.bin"},
    )["evidence"]
    evaluation = _evaluate_one("artifact", evidence)
    verified = local_tools.project_verify_acceptance(
        evaluation["evaluation"]["evaluation_id"], evaluation["sha256"], 2, 1,
    )
    assert verified["state"]["lifecycle"] == "verified"

    with pytest.raises(ValueError, match="must be reopened"):
        local_tools.project_acceptance_set(
            "changed contract",
            [{
                "id": "check",
                "description": "changed",
                "evidence_kinds": ["artifact"],
            }],
            expected_state_revision=3,
            expected_contract_revision=1,
        )

    reopened = local_tools.project_state_update(3, next_action="new work")
    assert reopened["state"]["revision"] == 4
    assert reopened["state"]["lifecycle"] == "computed"
