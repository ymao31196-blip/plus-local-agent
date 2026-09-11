"""Acceptance contracts and evidence-bound unverified evaluations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import yaml

import project_state as ps


ACCEPTANCE_FILE = "ACCEPTANCE.yaml"
ACCEPTANCE_RESULTS_DIR = "ACCEPTANCE_RESULTS"
MAX_ACCEPTANCE_CHECKS = 50
CHECK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _local():
    import local_tools
    return local_tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paths(
    project_path: str, root: str, access: str,
) -> tuple[Path, Path, Path, Path, Path]:
    local = _local()
    project, state_dir, state_path, _, evidence_path, _ = ps._paths(
        project_path, root, access
    )
    acceptance_path = (state_dir / ACCEPTANCE_FILE).resolve()
    results_dir = (state_dir / ACCEPTANCE_RESULTS_DIR).resolve()
    local.root_policy().resolve(root, str(acceptance_path), access)
    local.root_policy().resolve(root, str(results_dir), access)
    return project, state_path, evidence_path, acceptance_path, results_dir


def _validate_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_ACCEPTANCE_CHECKS:
        raise ValueError(f"checks must contain 1–{MAX_ACCEPTANCE_CHECKS} entries")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in checks:
        if not isinstance(item, dict) or set(item) != {"id", "description", "evidence_kinds"}:
            raise ValueError(
                "Each acceptance check requires exactly id, description, and evidence_kinds"
            )
        check_id = item["id"]
        if not isinstance(check_id, str) or not CHECK_ID_PATTERN.fullmatch(check_id):
            raise ValueError(
                "Acceptance check id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
            )
        if check_id in seen:
            raise ValueError(f"Duplicate acceptance check id: {check_id}")
        seen.add(check_id)
        description = ps._bounded_text(
            "acceptance check description", item["description"], 4000
        )
        if not description:
            raise ValueError("Acceptance check description cannot be empty")
        kinds = item["evidence_kinds"]
        if not isinstance(kinds, list) or len(kinds) > len(ps.EVIDENCE_KINDS):
            raise ValueError("evidence_kinds must be a list of supported evidence kinds")
        if any(kind not in ps.EVIDENCE_KINDS for kind in kinds):
            raise ValueError("evidence_kinds contains an unsupported evidence kind")
        if len(set(kinds)) != len(kinds):
            raise ValueError("evidence_kinds cannot contain duplicates")
        normalized.append({
            "id": check_id,
            "description": description,
            "evidence_kinds": list(kinds),
        })
    return normalized


def _load_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Acceptance contract is not defined")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read ACCEPTANCE.yaml: {exc}") from exc
    required = {
        "schema_version", "contract_revision", "created_at", "updated_at",
        "authored_state_revision", "authored_state_sha256", "title", "checks",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("ACCEPTANCE.yaml has an unexpected schema")
    if value["schema_version"] != 1:
        raise ValueError("Unsupported acceptance schema_version")
    if type(value["contract_revision"]) is not int or value["contract_revision"] < 1:
        raise ValueError("Acceptance contract_revision must be a positive integer")
    ps._bounded_text("acceptance title", value["title"], 2000)
    _validate_checks(value["checks"])
    if type(value["authored_state_revision"]) is not int or value["authored_state_revision"] < 1:
        raise ValueError("Acceptance authored_state_revision must be positive")
    if not isinstance(value["authored_state_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["authored_state_sha256"]
    ):
        raise ValueError("Acceptance authored_state_sha256 is invalid")
    return value


def set_contract(
    title: str,
    checks: list[dict[str, Any]],
    expected_state_revision: int,
    expected_contract_revision: int = 0,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    title = ps._bounded_text("acceptance title", title, 2000)
    if not title:
        raise ValueError("acceptance title cannot be empty")
    checks = _validate_checks(checks)
    if type(expected_contract_revision) is not int or expected_contract_revision < 0:
        raise ValueError("expected_contract_revision must be a non-negative integer")
    with local.WORKSPACE_MUTATION_LOCK:
        project, state_path, _, acceptance_path, _ = _paths(
            project_path, root, "write"
        )
        state = ps._load_state(state_path)
        ps._assert_expected_revision(state, expected_state_revision)
        if state["lifecycle"] == "verified":
            raise ValueError(
                "Verified project state must be reopened before revising the acceptance contract"
            )
        current = None
        if acceptance_path.exists():
            current = _load_contract(acceptance_path)
            if current["contract_revision"] != expected_contract_revision:
                raise ValueError(
                    "Acceptance contract changed since inspection: "
                    f"expected revision {expected_contract_revision}, "
                    f"current {current['contract_revision']}"
                )
        elif expected_contract_revision != 0:
            raise ValueError(
                "Acceptance contract does not exist; "
                "expected_contract_revision must be 0"
            )
        now = _now()
        contract = {
            "schema_version": 1,
            "contract_revision": 1 if current is None else current["contract_revision"] + 1,
            "created_at": now if current is None else current["created_at"],
            "updated_at": now,
            "authored_state_revision": state["revision"],
            "authored_state_sha256": local._file_sha256(state_path),
            "title": title,
            "checks": checks,
        }
        local._atomic_write_text(
            acceptance_path,
            yaml.safe_dump(contract, allow_unicode=True, sort_keys=False),
        )
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "acceptance_path": local._relative_path(acceptance_path, root),
            "contract": contract,
            "sha256": local._file_sha256(acceptance_path),
        }


def get_contract(
    project_path: str = ".", root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    project, state_path, _, acceptance_path, _ = _paths(project_path, root, "read")
    state = ps._load_state(state_path)
    if not acceptance_path.is_file():
        return {
            "status": "absent",
            "project_path": local._relative_path(project, root),
            "state_revision": state["revision"],
            "acceptance_path": local._relative_path(acceptance_path, root),
        }
    contract = _load_contract(acceptance_path)
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_revision": state["revision"],
        "contract": contract,
        "acceptance_path": local._relative_path(acceptance_path, root),
        "sha256": local._file_sha256(acceptance_path),
    }


def _load_evidence(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError("EVIDENCE.jsonl is missing")
    if path.stat().st_size > ps.MAX_LOG_BYTES:
        raise ValueError("EVIDENCE.jsonl exceeds the project-log read limit")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid EVIDENCE.jsonl record at line {line_number}"
            ) from exc
        if not isinstance(record, dict) or not isinstance(
            record.get("evidence_id"), str
        ):
            raise ValueError(
                f"Invalid EVIDENCE.jsonl record at line {line_number}"
            )
        records.append(record)
    return records


def evaluate_contract(
    bindings: list[dict[str, Any]],
    expected_state_revision: int,
    expected_contract_revision: int,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if not isinstance(bindings, list):
        raise ValueError("bindings must be a list")
    with local.WORKSPACE_MUTATION_LOCK:
        project, state_path, evidence_path, acceptance_path, results_dir = _paths(
            project_path, root, "write"
        )
        state = ps._load_state(state_path)
        ps._assert_expected_revision(state, expected_state_revision)
        contract = _load_contract(acceptance_path)
        if contract["contract_revision"] != expected_contract_revision:
            raise ValueError(
                "Acceptance contract changed since inspection: "
                f"expected revision {expected_contract_revision}, "
                f"current {contract['contract_revision']}"
            )
        check_by_id = {item["id"]: item for item in contract["checks"]}
        if len(bindings) != len(check_by_id):
            raise ValueError(
                "bindings must provide exactly one entry for every acceptance check"
            )
        binding_by_id: dict[str, list[str]] = {}
        for item in bindings:
            if not isinstance(item, dict) or set(item) != {"check_id", "evidence_ids"}:
                raise ValueError(
                    "Each binding requires exactly check_id and evidence_ids"
                )
            check_id = item["check_id"]
            evidence_ids = item["evidence_ids"]
            if check_id not in check_by_id or check_id in binding_by_id:
                raise ValueError(
                    f"Unknown or duplicate acceptance check binding: {check_id}"
                )
            if not isinstance(evidence_ids, list) or len(evidence_ids) > 20:
                raise ValueError(
                    "evidence_ids must be a list with at most 20 entries"
                )
            if any(not isinstance(value, str) or not value for value in evidence_ids):
                raise ValueError("evidence_ids must contain non-empty strings")
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError("evidence_ids cannot contain duplicates")
            binding_by_id[check_id] = list(evidence_ids)
        if set(binding_by_id) != set(check_by_id):
            raise ValueError(
                "bindings must cover every acceptance check exactly once"
            )

        state_sha256 = local._file_sha256(state_path)
        evidence_by_id = {
            item["evidence_id"]: item for item in _load_evidence(evidence_path)
        }
        results: list[dict[str, Any]] = []
        for check in contract["checks"]:
            evidence_ids = binding_by_id[check["id"]]
            selected: list[dict[str, Any]] = []
            for evidence_id in evidence_ids:
                record = evidence_by_id.get(evidence_id)
                if record is None:
                    raise ValueError(f"Unknown evidence_id: {evidence_id}")
                if (
                    record.get("state_revision") != state["revision"]
                    or record.get("state_sha256") != state_sha256
                ):
                    raise ValueError(
                        f"Evidence {evidence_id} does not belong to "
                        "the current project-state revision"
                    )
                selected.append(record)
            allowed = set(check["evidence_kinds"])
            matching = [
                item for item in selected
                if not allowed or item.get("kind") in allowed
            ]
            if any(item.get("status") == "fail" for item in matching):
                check_status = "fail"
            elif any(item.get("status") == "pass" for item in matching):
                check_status = "pass"
            else:
                check_status = "incomplete"
            results.append({
                "check_id": check["id"],
                "description": check["description"],
                "status": check_status,
                "evidence_ids": evidence_ids,
                "matching_evidence_ids": [
                    item["evidence_id"] for item in matching
                ],
            })

        statuses = {item["status"] for item in results}
        acceptance_status = (
            "fail" if "fail" in statuses
            else "pass" if statuses == {"pass"}
            else "incomplete"
        )
        results_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = (
            f"r{state['revision']:06d}-c{contract['contract_revision']:04d}-"
            f"{stamp}-{uuid4().hex[:8]}.yaml"
        )
        target = (results_dir / filename).resolve()
        local.root_policy().resolve(root, str(target), "write")
        payload = {
            "schema_version": 1,
            "evaluation_id": filename.removesuffix(".yaml"),
            "created_at": _now(),
            "state_revision": state["revision"],
            "state_sha256": state_sha256,
            "contract_revision": contract["contract_revision"],
            "contract_sha256": local._file_sha256(acceptance_path),
            "acceptance_status": acceptance_status,
            "verification_status": "unverified",
            "results": results,
        }
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                yaml.safe_dump(
                    payload, allow_unicode=True, sort_keys=False
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "evaluation": payload,
            "evaluation_path": local._relative_path(target, root),
            "sha256": local._file_sha256(target),
        }


def get_evaluations(
    project_path: str = ".",
    limit: int = 20,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    project, state_path, _, _, results_dir = _paths(project_path, root, "read")
    state = ps._load_state(state_path)
    if not results_dir.is_dir():
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "state_revision": state["revision"],
            "total_count": 0,
            "evaluations": [],
            "truncated": False,
        }
    files = sorted(
        (
            item for item in results_dir.iterdir()
            if item.is_file()
            and item.suffix.casefold() in {".yaml", ".yml"}
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    selected = files[:limit]
    evaluations = []
    for path in selected:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(
                f"Could not read acceptance evaluation {path.name}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid acceptance evaluation: {path.name}")
        evaluations.append({
            "path": local._relative_path(path, root),
            "evaluation_id": payload.get("evaluation_id"),
            "created_at": payload.get("created_at"),
            "state_revision": payload.get("state_revision"),
            "contract_revision": payload.get("contract_revision"),
            "acceptance_status": payload.get("acceptance_status"),
            "verification_status": payload.get("verification_status"),
            "sha256": local._file_sha256(path),
        })
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_revision": state["revision"],
        "total_count": len(files),
        "evaluations": evaluations,
        "truncated": len(files) > len(selected),
    }
