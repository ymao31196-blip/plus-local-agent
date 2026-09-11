"""Independent re-verification of acceptance evidence before verified promotion."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import yaml

import acceptance_contract as ac
import project_state as ps
from verification_spec import normalize_verification_spec


VERIFICATIONS_DIR = "VERIFICATIONS"
EVALUATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _local():
    import local_tools
    return local_tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_target(project: Path, relative: str, root: str, access: str) -> Path:
    local = _local()
    target = (project / relative).resolve()
    try:
        target.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError("Verification target must stay inside the project directory") from exc
    local.root_policy().resolve(root, str(target), access)
    return target


def _load_evaluation(
    evaluation_id: str,
    expected_sha256: str,
    project_path: str,
    root: str,
) -> tuple[Path, Path, Path, Path, dict[str, Any], dict[str, Any], str]:
    local = _local()
    if not isinstance(evaluation_id, str) or not EVALUATION_ID_PATTERN.fullmatch(evaluation_id):
        raise ValueError("evaluation_id is invalid")
    if not isinstance(expected_sha256, str) or not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ValueError("expected_evaluation_sha256 must be 64 hexadecimal characters")

    project, state_path, evidence_path, acceptance_path, results_dir = ac._paths(
        project_path, root, "read"
    )
    state = ps._load_state(state_path)
    contract = ac._load_contract(acceptance_path)
    target = (results_dir / f"{evaluation_id}.yaml").resolve()
    local.root_policy().resolve(root, str(target), "read")
    if not target.is_file():
        raise ValueError(f"Acceptance evaluation not found: {evaluation_id}")
    actual_sha = local._file_sha256(target)
    if actual_sha.lower() != expected_sha256.lower():
        raise ValueError(
            "Acceptance evaluation changed since inspection: "
            f"expected {expected_sha256.lower()}, current {actual_sha.lower()}"
        )
    try:
        evaluation = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read acceptance evaluation: {exc}") from exc
    required = {
        "schema_version", "evaluation_id", "created_at", "state_revision",
        "state_sha256", "contract_revision", "contract_sha256",
        "acceptance_status", "verification_status", "results",
    }
    if not isinstance(evaluation, dict) or set(evaluation) != required:
        raise ValueError("Acceptance evaluation has an unexpected schema")
    if evaluation["evaluation_id"] != evaluation_id:
        raise ValueError("Acceptance evaluation id does not match its filename")
    return (
        project, state_path, evidence_path, acceptance_path,
        state, contract, actual_sha, target, evaluation,
    )


def _verify_file(
    project: Path,
    record: dict[str, Any],
    spec: dict[str, Any],
    root: str,
) -> dict[str, Any]:
    local = _local()
    target = _project_target(project, spec["path"], root, "read")
    expected = record.get("artifact_sha256")
    result = {
        "evidence_id": record["evidence_id"],
        "verification_type": "file_sha256",
        "path": spec["path"],
        "expected_sha256": expected,
        "status": "failed",
    }
    if not target.is_file():
        result["reason"] = "artifact_missing"
        return result
    actual = local._file_sha256(target)
    result["actual_sha256"] = actual
    if isinstance(expected, str) and actual.lower() == expected.lower():
        result["status"] = "verified"
    else:
        result["reason"] = "sha256_mismatch"
    return result


def _verify_pytest(
    project: Path,
    record: dict[str, Any],
    spec: dict[str, Any],
    root: str,
) -> dict[str, Any]:
    local = _local()
    cwd_target = _project_target(project, spec["cwd"], root, "execute")
    if not cwd_target.is_dir():
        return {
            "evidence_id": record["evidence_id"],
            "verification_type": "pytest",
            "status": "failed",
            "reason": "cwd_not_directory",
        }
    cwd = local._relative_path(cwd_target, root)
    outcome = local.run_process(
        "pytest",
        list(spec["args"]),
        cwd=cwd,
        timeout=spec["timeout"],
        root=root,
    )
    result = {
        "evidence_id": record["evidence_id"],
        "verification_type": "pytest",
        "program": "pytest",
        "args": list(spec["args"]),
        "cwd": spec["cwd"],
        "timeout_seconds": spec["timeout"],
        "returncode": outcome.get("returncode"),
        "timed_out": bool(outcome.get("timeout", False)),
        "stdout": outcome.get("stdout", ""),
        "stderr": outcome.get("stderr", ""),
        "stdout_truncated": bool(outcome.get("stdout_truncated", False)),
        "stderr_truncated": bool(outcome.get("stderr_truncated", False)),
        "status": "verified" if outcome.get("returncode") == 0 else "failed",
    }
    if result["status"] != "verified":
        result["reason"] = "pytest_failed_or_timed_out"
    return result


def _verify_evidence(
    project: Path,
    record: dict[str, Any],
    root: str,
) -> dict[str, Any]:
    verification = record.get("verification")
    if verification is None:
        return {
            "evidence_id": record["evidence_id"],
            "status": "unsupported",
            "reason": "no_structured_verification_spec",
        }
    try:
        spec = normalize_verification_spec(
            record.get("kind"),
            record.get("artifact_sha256"),
            verification,
        )
    except (TypeError, ValueError) as exc:
        return {
            "evidence_id": record["evidence_id"],
            "status": "unsupported",
            "reason": f"invalid_verification_spec: {exc}",
        }
    if spec["type"] == "file_sha256":
        return _verify_file(project, record, spec, root)
    if spec["type"] == "pytest":
        return _verify_pytest(project, record, spec, root)
    return {
        "evidence_id": record["evidence_id"],
        "status": "unsupported",
        "reason": "unsupported_verification_type",
    }


def verify_acceptance(
    evaluation_id: str,
    expected_evaluation_sha256: str,
    expected_state_revision: int,
    expected_contract_revision: int,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    (
        project, state_path, evidence_path, acceptance_path,
        state, contract, evaluation_sha, evaluation_path, evaluation,
    ) = _load_evaluation(
        evaluation_id, expected_evaluation_sha256, project_path, root
    )
    ps._assert_expected_revision(state, expected_state_revision)
    if contract["contract_revision"] != expected_contract_revision:
        raise ValueError(
            "Acceptance contract changed since inspection: "
            f"expected revision {expected_contract_revision}, "
            f"current {contract['contract_revision']}"
        )
    state_sha = local._file_sha256(state_path)
    contract_sha = local._file_sha256(acceptance_path)
    if evaluation["acceptance_status"] != "pass":
        raise ValueError("Only a pass acceptance evaluation can be independently verified")
    if evaluation["verification_status"] != "unverified":
        raise ValueError("Acceptance evaluation is not in unverified state")
    if (
        evaluation["state_revision"] != state["revision"]
        or evaluation["state_sha256"] != state_sha
    ):
        raise ValueError("Acceptance evaluation does not match the current project state")
    if (
        evaluation["contract_revision"] != contract["contract_revision"]
        or evaluation["contract_sha256"] != contract_sha
    ):
        raise ValueError("Acceptance evaluation does not match the current contract")
    if state["lifecycle"] != "frozen":
        raise ValueError("Project state must be frozen before independent verification")

    evidence_by_id = {
        item["evidence_id"]: item for item in ac._load_evidence(evidence_path)
    }
    check_results: list[dict[str, Any]] = []
    for check in evaluation["results"]:
        if check.get("status") != "pass":
            raise ValueError("Pass acceptance evaluation contains a non-pass check")
        attempts: list[dict[str, Any]] = []
        for evidence_id in check.get("matching_evidence_ids", []):
            record = evidence_by_id.get(evidence_id)
            if record is None:
                raise ValueError(f"Acceptance evidence is missing: {evidence_id}")
            if (
                record.get("state_revision") != state["revision"]
                or record.get("state_sha256") != state_sha
                or record.get("status") != "pass"
            ):
                continue
            attempts.append(_verify_evidence(project, record, root))
        if any(item["status"] == "verified" for item in attempts):
            status = "verified"
        elif any(item["status"] == "failed" for item in attempts):
            status = "failed"
        else:
            status = "incomplete"
        check_results.append({
            "check_id": check.get("check_id"),
            "status": status,
            "attempts": attempts,
        })

    statuses = {item["status"] for item in check_results}
    verification_status = (
        "failed" if "failed" in statuses
        else "verified" if statuses == {"verified"}
        else "incomplete"
    )

    verification_id = (
        f"v-r{state['revision']:06d}-c{contract['contract_revision']:04d}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    source_revision = state["revision"]
    payload = {
        "schema_version": 1,
        "verification_id": verification_id,
        "created_at": _now(),
        "source_state_revision": source_revision,
        "source_state_sha256": state_sha,
        "contract_revision": contract["contract_revision"],
        "contract_sha256": contract_sha,
        "evaluation_id": evaluation_id,
        "evaluation_sha256": evaluation_sha,
        "verification_status": verification_status,
        "promoted_state_revision": None,
        "checks": check_results,
    }

    with local.WORKSPACE_MUTATION_LOCK:
        current_state = ps._load_state(state_path)
        ps._assert_expected_revision(current_state, expected_state_revision)
        if local._file_sha256(state_path) != state_sha:
            raise ValueError("Project state changed during independent verification")
        current_contract = ac._load_contract(acceptance_path)
        if (
            current_contract["contract_revision"] != expected_contract_revision
            or local._file_sha256(acceptance_path) != contract_sha
        ):
            raise ValueError("Acceptance contract changed during independent verification")
        if local._file_sha256(evaluation_path) != evaluation_sha:
            raise ValueError("Acceptance evaluation changed during independent verification")
        if current_state["lifecycle"] != "frozen":
            raise ValueError("Project state is no longer frozen")

        state_dir = state_path.parent
        verifications_dir = (state_dir / VERIFICATIONS_DIR).resolve()
        local.root_policy().resolve(root, str(verifications_dir), "write")
        verifications_dir.mkdir(exist_ok=True)
        target = (verifications_dir / f"{verification_id}.yaml").resolve()
        local.root_policy().resolve(root, str(target), "write")

        if verification_status == "verified":
            promoted = dict(current_state)
            promoted["lifecycle"] = "verified"
            promoted["revision"] += 1
            promoted["updated_at"] = ps._now()
            ps._validate_state(promoted)
            payload["promoted_state_revision"] = promoted["revision"]
            rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                local._atomic_write_text(
                    state_path,
                    yaml.safe_dump(promoted, allow_unicode=True, sort_keys=False),
                )
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return {
                "status": "completed",
                "verification_status": "verified",
                "project_path": local._relative_path(project, root),
                "verification_path": local._relative_path(target, root),
                "verification": payload,
                "state": promoted,
                "state_sha256": local._file_sha256(state_path),
            }

        rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        return {
            "status": "completed",
            "verification_status": verification_status,
            "project_path": local._relative_path(project, root),
            "verification_path": local._relative_path(target, root),
            "verification": payload,
            "state": current_state,
            "state_sha256": local._file_sha256(state_path),
        }


def get_verifications(
    project_path: str = ".",
    limit: int = 20,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    project, state_dir, state_path, _, _, _ = ps._paths(project_path, root, "read")
    state = ps._load_state(state_path)
    directory = (state_dir / VERIFICATIONS_DIR).resolve()
    local.root_policy().resolve(root, str(directory), "read")
    if not directory.is_dir():
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "state_revision": state["revision"],
            "total_count": 0,
            "verifications": [],
            "truncated": False,
        }
    files = sorted(
        (
            item for item in directory.iterdir()
            if item.is_file() and item.suffix.casefold() in {".yaml", ".yml"}
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    selected = files[:limit]
    records = []
    for path in selected:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"Could not read verification {path.name}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid verification record: {path.name}")
        records.append({
            "path": local._relative_path(path, root),
            "verification_id": payload.get("verification_id"),
            "created_at": payload.get("created_at"),
            "source_state_revision": payload.get("source_state_revision"),
            "promoted_state_revision": payload.get("promoted_state_revision"),
            "contract_revision": payload.get("contract_revision"),
            "evaluation_id": payload.get("evaluation_id"),
            "verification_status": payload.get("verification_status"),
            "sha256": local._file_sha256(path),
        })
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_revision": state["revision"],
        "total_count": len(files),
        "verifications": records,
        "truncated": len(files) > len(selected),
    }
