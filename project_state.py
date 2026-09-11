"""Repo-local project state and immutable checkpoints for durable agent handoff."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

import yaml


STATE_DIR = ".project-agent"
STATE_FILE = "STATE.yaml"
DECISIONS_FILE = "DECISIONS.md"
EVIDENCE_FILE = "EVIDENCE.jsonl"
CHECKPOINT_DIR = "CHECKPOINTS"
SCHEMA_VERSION = 1
LIFECYCLES = {"candidate", "computed", "frozen", "verified", "deprecated"}
MAX_BLOCKERS = 50
MAX_LOG_BYTES = 16_000_000
EVIDENCE_KINDS = {"test", "command", "artifact", "metric", "observation", "manual"}
EVIDENCE_STATUSES = {"pass", "fail", "info"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local():
    import local_tools
    return local_tools


def _project_dir(project_path: str, root: str, access: str) -> Path:
    local = _local()
    target = local.safe_path(project_path, root, access)
    if not target.exists():
        raise ValueError(f"Project path does not exist: {project_path}")
    if not target.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")
    return target


def _paths(project_path: str, root: str, access: str) -> tuple[Path, Path, Path, Path, Path]:
    local = _local()
    project = _project_dir(project_path, root, access)
    state_dir = (project / STATE_DIR).resolve()
    local.root_policy().resolve(root, str(state_dir), access)
    state = (state_dir / STATE_FILE).resolve()
    decisions = (state_dir / DECISIONS_FILE).resolve()
    evidence = (state_dir / EVIDENCE_FILE).resolve()
    checkpoints = (state_dir / CHECKPOINT_DIR).resolve()
    for target in (state, decisions, evidence, checkpoints):
        local.root_policy().resolve(root, str(target), access)
    return project, state_dir, state, decisions, evidence, checkpoints


def _bounded_text(name: str, value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{name} cannot exceed {limit} characters")
    if "\x00" in value:
        raise ValueError(f"{name} cannot contain NUL")
    return value


def _validate_blockers(blockers: list[str]) -> list[str]:
    if not isinstance(blockers, list) or len(blockers) > MAX_BLOCKERS:
        raise ValueError(f"blockers must be a list with at most {MAX_BLOCKERS} entries")
    return [_bounded_text("blocker", item, 2000) for item in blockers]


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("STATE.yaml must contain a mapping")
    required = {
        "schema_version", "revision", "project_name", "objective", "lifecycle",
        "current_phase", "next_action", "blockers", "created_at", "updated_at",
    }
    if set(value) != required:
        raise ValueError("STATE.yaml has an unexpected schema")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unsupported project state schema_version: {value['schema_version']!r}")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("STATE.yaml revision must be a positive integer")
    _bounded_text("project_name", value["project_name"], 2000)
    _bounded_text("objective", value["objective"], 10000)
    if value["lifecycle"] not in LIFECYCLES:
        raise ValueError("STATE.yaml lifecycle is not supported")
    _bounded_text("current_phase", value["current_phase"], 4000)
    _bounded_text("next_action", value["next_action"], 4000)
    _validate_blockers(value["blockers"])
    for name in ("created_at", "updated_at"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError(f"STATE.yaml {name} must be a non-empty timestamp string")
    return value


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        raise ValueError("Project state is not initialized")
    try:
        value = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read STATE.yaml: {exc}") from exc
    return _validate_state(value)


def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False)


def init_project_state(
    project_path: str = ".",
    project_name: str | None = None,
    objective: str = "",
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    with local.WORKSPACE_MUTATION_LOCK:
        project, state_dir, state_path, decisions_path, evidence_path, checkpoints = _paths(
            project_path, root, "write"
        )
        name = project.name if project_name is None else _bounded_text("project_name", project_name, 2000)
        if not name:
            raise ValueError("project_name cannot be empty")
        objective = _bounded_text("objective", objective, 10000)
        if state_dir.exists():
            raise ValueError(f"Project state already exists: {STATE_DIR}")
        created = False
        try:
            state_dir.mkdir(parents=False, exist_ok=False)
            created = True
            checkpoints.mkdir(exist_ok=False)
            now = _now()
            state = {
                "schema_version": SCHEMA_VERSION,
                "revision": 1,
                "project_name": name,
                "objective": objective,
                "lifecycle": "candidate",
                "current_phase": "",
                "next_action": "",
                "blockers": [],
                "created_at": now,
                "updated_at": now,
            }
            local._atomic_write_text(state_path, _dump_yaml(state))
            local._atomic_write_text(
                decisions_path,
                "# Decisions\n\nAppend durable owner/agent decisions here. Do not treat this log as verification evidence.\n",
            )
            local._atomic_write_text(evidence_path, "")
            return {
                "status": "completed",
                "project_path": local._relative_path(project, root),
                "state_dir": local._relative_path(state_dir, root),
                "state": state,
                "state_sha256": local._file_sha256(state_path),
            }
        except Exception:
            if created:
                shutil.rmtree(state_dir, ignore_errors=True)
            raise


def get_project_state(project_path: str = ".", root: str = "workspace") -> dict[str, Any]:
    local = _local()
    project, state_dir, state_path, decisions_path, evidence_path, checkpoints = _paths(
        project_path, root, "read"
    )
    if not state_dir.exists():
        return {
            "status": "absent",
            "project_path": local._relative_path(project, root),
            "state_dir": local._relative_path(state_dir, root),
        }
    state = _load_state(state_path)
    checkpoint_names = []
    if checkpoints.is_dir():
        checkpoint_names = sorted(
            (item.name for item in checkpoints.iterdir() if item.is_file() and item.suffix.casefold() in {".yaml", ".yml"}),
            reverse=True,
        )[:50]
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_dir": local._relative_path(state_dir, root),
        "state": state,
        "state_sha256": local._file_sha256(state_path),
        "decisions_path": local._relative_path(decisions_path, root),
        "evidence_path": local._relative_path(evidence_path, root),
        "checkpoint_dir": local._relative_path(checkpoints, root),
        "checkpoints": checkpoint_names,
        "checkpoints_truncated": len(checkpoint_names) == 50,
    }


def update_project_state(
    expected_revision: int,
    project_path: str = ".",
    objective: str | None = None,
    lifecycle: str | None = None,
    current_phase: str | None = None,
    next_action: str | None = None,
    blockers: list[str] | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    if all(value is None for value in (objective, lifecycle, current_phase, next_action, blockers)):
        raise ValueError("At least one state field must be provided")
    with local.WORKSPACE_MUTATION_LOCK:
        project, _, state_path, _, _, _ = _paths(project_path, root, "write")
        state = _load_state(state_path)
        if state["revision"] != expected_revision:
            raise ValueError(
                f"Project state changed since inspection: expected revision {expected_revision}, current {state['revision']}"
            )
        if state["lifecycle"] == "verified":
            state["lifecycle"] = "computed"
        if objective is not None:
            state["objective"] = _bounded_text("objective", objective, 10000)
        if lifecycle is not None:
            if lifecycle == "verified":
                raise ValueError("verified lifecycle is reserved for the independent verifier")
            if lifecycle not in LIFECYCLES:
                raise ValueError(f"Unsupported lifecycle: {lifecycle}")
            state["lifecycle"] = lifecycle
        if current_phase is not None:
            state["current_phase"] = _bounded_text("current_phase", current_phase, 4000)
        if next_action is not None:
            state["next_action"] = _bounded_text("next_action", next_action, 4000)
        if blockers is not None:
            state["blockers"] = _validate_blockers(blockers)
        state["revision"] += 1
        state["updated_at"] = _now()
        _validate_state(state)
        local._atomic_write_text(state_path, _dump_yaml(state))
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "state": state,
            "state_sha256": local._file_sha256(state_path),
        }


def create_checkpoint(
    label: str,
    summary: str,
    expected_revision: int,
    project_path: str = ".",
    checks: list[str] | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    label = _bounded_text("label", label, 200)
    if not label:
        raise ValueError("label cannot be empty")
    summary = _bounded_text("summary", summary, 10000)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    checks = [] if checks is None else _validate_blockers(checks)
    with local.WORKSPACE_MUTATION_LOCK:
        project, _, state_path, _, _, checkpoints = _paths(project_path, root, "write")
        state = _load_state(state_path)
        if state["revision"] != expected_revision:
            raise ValueError(
                f"Project state changed since inspection: expected revision {expected_revision}, current {state['revision']}"
            )
        checkpoints.mkdir(exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._")[:64] or "checkpoint"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"r{state['revision']:06d}-{stamp}-{slug}-{uuid4().hex[:8]}.yaml"
        target = (checkpoints / filename).resolve()
        local.root_policy().resolve(root, str(target), "write")
        payload = {
            "schema_version": 1,
            "checkpoint_id": filename.removesuffix(".yaml"),
            "created_at": _now(),
            "label": label,
            "summary": summary,
            "checks": checks,
            "verification_status": "unverified",
            "state_revision": state["revision"],
            "state_sha256": local._file_sha256(state_path),
            "state": state,
        }
        text = _dump_yaml(payload)
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "checkpoint": local._relative_path(target, root),
            "checkpoint_id": payload["checkpoint_id"],
            "state_revision": state["revision"],
            "verification_status": "unverified",
            "sha256": local._file_sha256(target),
        }
def _assert_expected_revision(state: dict[str, Any], expected_revision: int) -> None:
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    if state["revision"] != expected_revision:
        raise ValueError(
            f"Project state changed since inspection: expected revision {expected_revision}, "
            f"current {state['revision']}"
        )


def _append_bounded(path: Path, text: str) -> None:
    encoded = text.encode("utf-8")
    current = path.stat().st_size if path.exists() else 0
    if current + len(encoded) > MAX_LOG_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_LOG_BYTES} byte project-log limit")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def record_decision(
    title: str,
    decision: str,
    rationale: str,
    expected_revision: int,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    title = _bounded_text("title", title, 500)
    decision = _bounded_text("decision", decision, 10000)
    rationale = _bounded_text("rationale", rationale, 10000)
    if not title or not decision:
        raise ValueError("title and decision cannot be empty")
    with local.WORKSPACE_MUTATION_LOCK:
        project, _, state_path, decisions_path, _, _ = _paths(project_path, root, "write")
        state = _load_state(state_path)
        _assert_expected_revision(state, expected_revision)
        state_sha256 = local._file_sha256(state_path)
        recorded_at = _now()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        decision_id = f"D-{stamp}-{uuid4().hex[:8]}"
        entry = (
            f"\n## {decision_id} — {title}\n\n"
            f"- recorded_at: {recorded_at}\n"
            f"- state_revision: {state['revision']}\n"
            f"- state_sha256: {state_sha256}\n\n"
            f"### Decision\n{decision}\n\n"
            f"### Rationale\n{rationale or '(not provided)'}\n"
        )
        _append_bounded(decisions_path, entry)
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "decision_id": decision_id,
            "title": title,
            "state_revision": state["revision"],
            "state_sha256": state_sha256,
            "decisions_path": local._relative_path(decisions_path, root),
            "sha256": local._file_sha256(decisions_path),
        }


def get_decisions(
    project_path: str = ".",
    max_chars: int = 20000,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if type(max_chars) is not int or not 1 <= max_chars <= 20000:
        raise ValueError("max_chars must be an integer between 1 and 20000")
    project, _, state_path, decisions_path, _, _ = _paths(project_path, root, "read")
    state = _load_state(state_path)
    if not decisions_path.is_file():
        raise ValueError("DECISIONS.md is missing")
    if decisions_path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("DECISIONS.md exceeds the project-log read limit")
    rendered = local.truncate_text(decisions_path.read_text(encoding="utf-8"), max_chars)
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_revision": state["revision"],
        "decisions_path": local._relative_path(decisions_path, root),
        "content": rendered["content"],
        "truncated": rendered["truncated"],
        "original_length": rendered["original_length"],
        "sha256": local._file_sha256(decisions_path),
    }


def record_evidence(
    kind: str,
    status: str,
    summary: str,
    source: str,
    expected_revision: int,
    project_path: str = ".",
    details: str = "",
    artifact_sha256: str | None = None,
    verification: dict[str, Any] | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"Unsupported evidence kind: {kind}")
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"Unsupported evidence status: {status}")
    summary = _bounded_text("summary", summary, 10000)
    source = _bounded_text("source", source, 4000)
    details = _bounded_text("details", details, 10000)
    if not summary or not source:
        raise ValueError("summary and source cannot be empty")
    if artifact_sha256 is not None:
        if not isinstance(artifact_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha256):
            raise ValueError("artifact_sha256 must be exactly 64 hexadecimal characters when provided")
        artifact_sha256 = artifact_sha256.lower()
    from verification_spec import normalize_verification_spec
    verification = normalize_verification_spec(kind, artifact_sha256, verification)
    with local.WORKSPACE_MUTATION_LOCK:
        project, _, state_path, _, evidence_path, _ = _paths(project_path, root, "write")
        state = _load_state(state_path)
        _assert_expected_revision(state, expected_revision)
        state_sha256 = local._file_sha256(state_path)
        record = {
            "schema_version": 1,
            "evidence_id": f"E-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
            "created_at": _now(),
            "state_revision": state["revision"],
            "state_sha256": state_sha256,
            "kind": kind,
            "status": status,
            "summary": summary,
            "source": source,
            "details": details,
            "artifact_sha256": artifact_sha256,
            "verification": verification,
            "verification_status": "unverified",
        }
        _append_bounded(evidence_path, json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return {
            "status": "completed",
            "project_path": local._relative_path(project, root),
            "evidence": record,
            "evidence_path": local._relative_path(evidence_path, root),
            "sha256": local._file_sha256(evidence_path),
        }


def get_evidence(
    project_path: str = ".",
    limit: int = 20,
    kind: str | None = None,
    status: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    local = _local()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if kind is not None and kind not in EVIDENCE_KINDS:
        raise ValueError(f"Unsupported evidence kind: {kind}")
    if status is not None and status not in EVIDENCE_STATUSES:
        raise ValueError(f"Unsupported evidence status: {status}")
    project, _, state_path, _, evidence_path, _ = _paths(project_path, root, "read")
    state = _load_state(state_path)
    if not evidence_path.is_file():
        raise ValueError("EVIDENCE.jsonl is missing")
    if evidence_path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("EVIDENCE.jsonl exceeds the project-log read limit")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(evidence_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid EVIDENCE.jsonl record at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Invalid EVIDENCE.jsonl record at line {line_number}")
        if kind is not None and record.get("kind") != kind:
            continue
        if status is not None and record.get("status") != status:
            continue
        records.append(record)
    total = len(records)
    selected = list(reversed(records[-limit:]))
    return {
        "status": "completed",
        "project_path": local._relative_path(project, root),
        "state_revision": state["revision"],
        "evidence_path": local._relative_path(evidence_path, root),
        "limit": limit,
        "kind": kind,
        "evidence_status": status,
        "total_count": total,
        "records": selected,
        "truncated": total > len(selected),
        "sha256": local._file_sha256(evidence_path),
    }
