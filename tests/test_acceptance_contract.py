import pytest

import local_tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(local_tools, "WORKSPACE", root)
    return root


def _checks():
    return [
        {
            "id": "tests",
            "description": "Required automated tests pass.",
            "evidence_kinds": ["test"],
        },
        {
            "id": "artifact",
            "description": "Required artifact is present and inspected.",
            "evidence_kinds": ["artifact"],
        },
    ]


def test_acceptance_contract_absent_create_update_and_cas(workspace):
    local_tools.project_state_init(project_name="Acceptance")

    absent = local_tools.project_acceptance_get()
    assert absent["status"] == "absent"

    created = local_tools.project_acceptance_set(
        "Runtime acceptance",
        _checks(),
        expected_state_revision=1,
        expected_contract_revision=0,
    )
    assert created["contract"]["contract_revision"] == 1
    assert local_tools.project_state_get()["state"]["revision"] == 1

    updated = local_tools.project_acceptance_set(
        "Runtime acceptance v2",
        _checks(),
        expected_state_revision=1,
        expected_contract_revision=1,
    )
    assert updated["contract"]["contract_revision"] == 2

    with pytest.raises(ValueError, match="Acceptance contract changed since inspection"):
        local_tools.project_acceptance_set(
            "stale",
            _checks(),
            expected_state_revision=1,
            expected_contract_revision=1,
        )


def test_acceptance_evaluation_pass_is_still_unverified(workspace):
    local_tools.project_state_init(project_name="Acceptance Eval")
    local_tools.project_acceptance_set(
        "Acceptance",
        _checks(),
        expected_state_revision=1,
        expected_contract_revision=0,
    )
    test_evidence = local_tools.project_evidence_record(
        "test", "pass", "All tests passed.", "pytest -q", 1,
    )["evidence"]
    artifact_evidence = local_tools.project_evidence_record(
        "artifact", "pass", "Artifact hash inspected.", "artifact.bin", 1,
        artifact_sha256="a" * 64,
    )["evidence"]

    evaluation = local_tools.project_acceptance_evaluate(
        [
            {"check_id": "tests", "evidence_ids": [test_evidence["evidence_id"]]},
            {"check_id": "artifact", "evidence_ids": [artifact_evidence["evidence_id"]]},
        ],
        expected_state_revision=1,
        expected_contract_revision=1,
    )

    assert evaluation["evaluation"]["acceptance_status"] == "pass"
    assert evaluation["evaluation"]["verification_status"] == "unverified"
    assert local_tools.project_state_get()["state"]["lifecycle"] == "candidate"

    history = local_tools.project_acceptance_evaluations_get()
    assert history["total_count"] == 1
    assert history["evaluations"][0]["acceptance_status"] == "pass"
    assert history["evaluations"][0]["verification_status"] == "unverified"


def test_acceptance_evaluation_computes_incomplete_and_fail(workspace):
    local_tools.project_state_init(project_name="Acceptance Status")
    local_tools.project_acceptance_set(
        "Acceptance",
        _checks(),
        expected_state_revision=1,
        expected_contract_revision=0,
    )
    test_pass = local_tools.project_evidence_record(
        "test", "pass", "Tests passed.", "pytest", 1,
    )["evidence"]
    artifact_info = local_tools.project_evidence_record(
        "artifact", "info", "Artifact exists.", "artifact.bin", 1,
    )["evidence"]

    incomplete = local_tools.project_acceptance_evaluate(
        [
            {"check_id": "tests", "evidence_ids": [test_pass["evidence_id"]]},
            {"check_id": "artifact", "evidence_ids": [artifact_info["evidence_id"]]},
        ],
        1,
        1,
    )
    assert incomplete["evaluation"]["acceptance_status"] == "incomplete"

    artifact_fail = local_tools.project_evidence_record(
        "artifact", "fail", "Artifact checksum mismatch.", "artifact.bin", 1,
    )["evidence"]
    failed = local_tools.project_acceptance_evaluate(
        [
            {"check_id": "tests", "evidence_ids": [test_pass["evidence_id"]]},
            {"check_id": "artifact", "evidence_ids": [artifact_fail["evidence_id"]]},
        ],
        1,
        1,
    )
    assert failed["evaluation"]["acceptance_status"] == "fail"


def test_acceptance_rejects_stale_state_evidence(workspace):
    local_tools.project_state_init(project_name="Acceptance Stale")
    local_tools.project_acceptance_set(
        "Acceptance",
        [{"id": "tests", "description": "Tests pass.", "evidence_kinds": ["test"]}],
        1,
        0,
    )
    evidence = local_tools.project_evidence_record(
        "test", "pass", "Old tests passed.", "pytest", 1,
    )["evidence"]
    local_tools.project_state_update(1, current_phase="changed")

    with pytest.raises(ValueError, match="does not belong to the current project-state revision"):
        local_tools.project_acceptance_evaluate(
            [{"check_id": "tests", "evidence_ids": [evidence["evidence_id"]]}],
            2,
            1,
        )
