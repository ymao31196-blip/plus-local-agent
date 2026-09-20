"""Durable action transaction state for ChatGPT-orchestrated multi-step work."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


TERMINAL_STATUSES = {"committed", "aborted", "rolled_back"}
OPEN_STATUSES = {
    "planned",
    "active",
    "verification_pending",
    "blocked",
    "rollback_required",
}
STEP_KINDS = {"action", "verify", "rollback"}
STEP_STATES = {
    "pending",
    "running",
    "succeeded",
    "failed",
    "verified",
    "rolled_back",
    "skipped",
    "interrupted",
}
OUTCOMES = {
    "started", "succeeded", "failed", "verified", "rolled_back", "skipped",
    "external_pending", "external_verified",
}
DECISIONS = {"commit", "abort", "rolled_back"}
_STEP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_STEPS = 100
MAX_TEXT = 20_000
MAX_EVENTS = 512


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(name: str, value: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{name} cannot contain NUL")
    if len(value) > limit:
        raise ValueError(f"{name} cannot exceed {limit} characters")
    return value


def _validate_json_object(name: str, value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) > 100_000:
        raise ValueError(f"{name} is too large")
    return value


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps must contain 1-{MAX_STEPS} entries")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise TypeError(f"steps[{index}] must be an object")
        step_id = raw.get("step_id")
        title = raw.get("title")
        kind = raw.get("kind", "action")
        rollback_step_id = raw.get("rollback_step_id")

        if not isinstance(step_id, str) or not _STEP_ID_RE.fullmatch(step_id):
            raise ValueError(f"steps[{index}].step_id is invalid")
        if step_id in ids:
            raise ValueError(f"Duplicate step_id: {step_id}")
        ids.add(step_id)
        title = _bounded_text(f"steps[{index}].title", title, 2000)
        if kind not in STEP_KINDS:
            raise ValueError(f"steps[{index}].kind must be one of {sorted(STEP_KINDS)}")
        if rollback_step_id is not None:
            if not isinstance(rollback_step_id, str) or not _STEP_ID_RE.fullmatch(rollback_step_id):
                raise ValueError(f"steps[{index}].rollback_step_id is invalid")
            if kind != "action":
                raise ValueError("Only action steps may reference rollback_step_id")

        normalized.append(
            {
                "step_id": step_id,
                "title": title,
                "kind": kind,
                "rollback_step_id": rollback_step_id,
                "state": "pending",
                "started_at": None,
                "finished_at": None,
                "summary": None,
                "evidence": {},
            }
        )

    by_id = {item["step_id"]: item for item in normalized}
    for step in normalized:
        rollback_id = step["rollback_step_id"]
        if rollback_id is None:
            continue
        target = by_id.get(rollback_id)
        if target is None:
            raise ValueError(f"Unknown rollback_step_id: {rollback_id}")
        if target["kind"] != "rollback":
            raise ValueError(f"rollback_step_id must reference a rollback step: {rollback_id}")
    return normalized


class ActionTransactionStore:
    """Persistent transaction state only; never executes external actions."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        require_outside_workspace: bool = False,
    ) -> None:
        self.db_path = str(db_path)
        self.require_outside_workspace = require_outside_workspace
        self._lock = RLock()
        self._db: sqlite3.Connection | None = None
        self._owner_file = None
        self._closed = False

    def _ensure(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("ActionTransactionStore is closed")
        if self._db is not None:
            return self._db

        db = None
        try:
            if self.db_path != ":memory:":
                path = Path(self.db_path).resolve()
                if self.require_outside_workspace:
                    from local_tools import WORKSPACE
                    if path.is_relative_to(WORKSPACE):
                        raise ValueError("Transaction database must be outside the user workspace")
                path.parent.mkdir(parents=True, exist_ok=True)
                owner = open(str(path) + ".lock", "a+b")
                self._owner_file = owner
                owner.seek(0)
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(owner.fileno()).st_size == 0:
                        owner.write(b"0")
                        owner.flush()
                    owner.seek(0)
                    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS action_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    final_summary TEXT
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS action_transaction_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS action_transaction_events_by_tx "
                "ON action_transaction_events(transaction_id, seq)"
            )
            self._db = db
            self._recover_interrupted_steps()
            return db
        except Exception:
            self._db = None
            if db is not None:
                db.close()
            if self._owner_file is not None:
                self._owner_file.close()
                self._owner_file = None
            raise

    def _event(self, transaction_id: str, event_type: str, data: dict[str, Any]) -> None:
        serialized = json.dumps(data, ensure_ascii=False)
        if len(serialized) > 24_000:
            data = {
                "content": serialized[-20_000:],
                "truncated": True,
                "original_length": len(serialized),
            }
        self._db.execute(
            "INSERT INTO action_transaction_events(transaction_id,event_type,created_at,data) "
            "VALUES (?, ?, ?, ?)",
            (transaction_id, event_type, _now(), json.dumps(data, ensure_ascii=False)),
        )
        cutoff = self._db.execute(
            "SELECT seq FROM action_transaction_events WHERE transaction_id=? "
            "ORDER BY seq DESC LIMIT 1 OFFSET ?",
            (transaction_id, MAX_EVENTS),
        ).fetchone()
        if cutoff:
            self._db.execute(
                "DELETE FROM action_transaction_events WHERE transaction_id=? AND seq<=?",
                (transaction_id, cutoff[0]),
            )

    def _recover_interrupted_steps(self) -> None:
        rows = self._db.execute(
            "SELECT transaction_id,status,revision,steps FROM action_transactions "
            "WHERE status NOT IN ('committed','aborted','rolled_back')"
        ).fetchall()
        with self._db:
            for row in rows:
                steps = json.loads(row["steps"])
                changed = False
                interrupted_count = 0
                preserved_external_count = 0
                recovered_at = _now()

                for step in steps:
                    if step.get("state") != "running":
                        continue

                    evidence = step.get("evidence")
                    if not isinstance(evidence, dict):
                        evidence = {}
                    can_resume_external = (
                        step.get("kind") == "action"
                        and evidence.get("external_completion_required") is True
                        and isinstance(evidence.get("completion_contract"), dict)
                    )
                    if can_resume_external:
                        recovery_count = evidence.get("runtime_recovery_count", 0)
                        if (
                            not isinstance(recovery_count, int)
                            or isinstance(recovery_count, bool)
                            or recovery_count < 0
                        ):
                            recovery_count = 0
                        evidence = {
                            **evidence,
                            "runtime_recovered": True,
                            "runtime_recovered_at": recovered_at,
                            "runtime_recovery_count": recovery_count + 1,
                        }
                        step["evidence"] = evidence
                        step["summary"] = (
                            "Runtime restarted while awaiting external completion; "
                            "the recorded read-only completion verifier remains required."
                        )
                        preserved_external_count += 1
                        changed = True
                        continue

                    step["state"] = "interrupted"
                    step["finished_at"] = recovered_at
                    step["summary"] = (
                        "Runtime restarted while this step was running; "
                        "external side effects are unknown and must be inspected."
                    )
                    interrupted_count += 1
                    changed = True

                if changed:
                    revision = row["revision"] + 1
                    next_status = (
                        "blocked"
                        if interrupted_count
                        else (
                            row["status"]
                            if row["status"] != "planned"
                            else "active"
                        )
                    )
                    self._db.execute(
                        "UPDATE action_transactions SET status=?,revision=?,updated_at=?,steps=? "
                        "WHERE transaction_id=?",
                        (
                            next_status,
                            revision,
                            recovered_at,
                            json.dumps(steps, ensure_ascii=False),
                            row["transaction_id"],
                        ),
                    )
                    reason = (
                        "running_step_interrupted"
                        if interrupted_count and not preserved_external_count
                        else (
                            "external_pending_preserved"
                            if preserved_external_count and not interrupted_count
                            else "mixed_running_steps_recovered"
                        )
                    )
                    self._event(
                        row["transaction_id"],
                        "runtime_recovery",
                        {
                            "status": next_status,
                            "reason": reason,
                            "interrupted_count": interrupted_count,
                            "preserved_external_count": preserved_external_count,
                        },
                    )

    def create(
        self,
        goal: str,
        steps: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal = _bounded_text("goal", goal, 10_000)
        normalized_steps = _normalize_steps(steps)
        metadata = _validate_json_object("metadata", metadata)
        transaction_id = uuid4().hex
        now = _now()

        with self._lock:
            db = self._ensure()
            with db:
                db.execute(
                    "INSERT INTO action_transactions("
                    "transaction_id,status,revision,created_at,updated_at,goal,steps,metadata"
                    ") VALUES (?, 'planned', 1, ?, ?, ?, ?, ?)",
                    (
                        transaction_id,
                        now,
                        now,
                        goal,
                        json.dumps(normalized_steps, ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                self._event(
                    transaction_id,
                    "transaction_created",
                    {"goal": goal, "step_count": len(normalized_steps)},
                )
        return self.get(transaction_id)

    def _load(self, transaction_id: str) -> sqlite3.Row:
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ValueError("transaction_id must be a non-empty string")
        row = self._ensure().execute(
            "SELECT * FROM action_transactions WHERE transaction_id=?",
            (transaction_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Transaction not found: {transaction_id}")
        return row

    def _decoded(self, row: sqlite3.Row, include_events: bool = True) -> dict[str, Any]:
        record = dict(row)
        record["steps"] = json.loads(record["steps"])
        record["metadata"] = json.loads(record["metadata"])
        if include_events:
            events = self._ensure().execute(
                "SELECT seq,event_type,created_at,data FROM action_transaction_events "
                "WHERE transaction_id=? ORDER BY seq DESC LIMIT 100",
                (record["transaction_id"],),
            ).fetchall()
            record["events"] = [
                {
                    "cursor": item["seq"],
                    "type": item["event_type"],
                    "created_at": item["created_at"],
                    "data": json.loads(item["data"]),
                }
                for item in reversed(events)
            ]
        return record

    def get(self, transaction_id: str) -> dict[str, Any]:
        with self._lock:
            return self._decoded(self._load(transaction_id))

    def checkpoint(
        self,
        transaction_id: str,
        expected_revision: int,
        step_id: str,
        outcome: str,
        summary: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
        summary = _bounded_text("summary", summary, 10_000)
        evidence = _validate_json_object("evidence", evidence)

        with self._lock:
            db = self._ensure()
            row = self._load(transaction_id)
            if row["status"] in TERMINAL_STATUSES:
                raise ValueError("Transaction is already terminal")
            if row["revision"] != expected_revision:
                raise ValueError(
                    f"Transaction changed since inspection: expected revision "
                    f"{expected_revision}, current {row['revision']}"
                )

            steps = json.loads(row["steps"])
            by_id = {step["step_id"]: step for step in steps}
            step = by_id.get(step_id)
            if step is None:
                raise ValueError(f"Unknown transaction step: {step_id}")

            previous = step["state"]
            now = _now()
            if outcome == "started":
                if previous != "pending":
                    raise ValueError("Only a pending step can be started")
                step["state"] = "running"
                step["started_at"] = now
                next_status = "active"
            elif outcome == "external_pending":
                if step["kind"] != "action" or previous != "running":
                    raise ValueError(
                        "external_pending is only valid for running action steps"
                    )
                if (
                    evidence.get("external_completion_required") is not True
                    or not isinstance(evidence.get("completion_contract"), dict)
                ):
                    raise ValueError(
                        "external_pending requires a persisted completion contract"
                    )
                step["state"] = "running"
                next_status = "active"
            elif outcome in {"succeeded", "external_verified"}:
                if step["kind"] != "action" or previous not in {"pending", "running"}:
                    raise ValueError(
                        f"{outcome} is only valid for pending/running action steps"
                    )
                pending_evidence = step.get("evidence") or {}
                completion_required = (
                    previous == "running"
                    and pending_evidence.get("external_completion_required") is True
                )
                if outcome == "succeeded" and completion_required:
                    raise ValueError(
                        "External-pending action requires the completion gate"
                    )
                if outcome == "external_verified":
                    if previous != "running" or not completion_required:
                        raise ValueError(
                            "external_verified requires a running external-pending action"
                        )
                step["state"] = "succeeded"
                step["started_at"] = step["started_at"] or now
                step["finished_at"] = now
                actions_pending = any(
                    item["kind"] == "action"
                    and item["state"] in {"pending", "running"}
                    for item in steps
                )
                verify_pending = any(
                    item["kind"] == "verify"
                    and item["state"] in {"pending", "running"}
                    for item in steps
                )
                next_status = (
                    "active"
                    if actions_pending
                    else ("verification_pending" if verify_pending else "active")
                )
            elif outcome == "verified":
                if step["kind"] != "verify" or previous not in {"pending", "running"}:
                    raise ValueError("verified is only valid for pending/running verify steps")
                step["state"] = "verified"
                step["started_at"] = step["started_at"] or now
                step["finished_at"] = now
                next_status = "verification_pending"
            elif outcome == "rolled_back":
                if step["kind"] != "rollback" or previous not in {"pending", "running"}:
                    raise ValueError("rolled_back is only valid for pending/running rollback steps")
                step["state"] = "rolled_back"
                step["started_at"] = step["started_at"] or now
                step["finished_at"] = now
                next_status = "rollback_required"
            elif outcome == "skipped":
                if previous != "pending":
                    raise ValueError("Only a pending step can be skipped")
                step["state"] = "skipped"
                step["finished_at"] = now
                next_status = row["status"] if row["status"] != "planned" else "active"
            else:
                if previous not in {"pending", "running"}:
                    raise ValueError("failed is only valid for pending/running steps")
                step["state"] = "failed"
                step["started_at"] = step["started_at"] or now
                step["finished_at"] = now
                rollback_needed = any(
                    item["kind"] == "action"
                    and item["state"] == "succeeded"
                    and item.get("rollback_step_id")
                    for item in steps
                )
                next_status = "rollback_required" if rollback_needed else "blocked"

            step["summary"] = summary
            step["evidence"] = evidence
            revision = row["revision"] + 1
            with db:
                db.execute(
                    "UPDATE action_transactions SET status=?,revision=?,updated_at=?,steps=? "
                    "WHERE transaction_id=?",
                    (
                        next_status,
                        revision,
                        now,
                        json.dumps(steps, ensure_ascii=False),
                        transaction_id,
                    ),
                )
                self._event(
                    transaction_id,
                    "step_checkpoint",
                    {
                        "step_id": step_id,
                        "kind": step["kind"],
                        "previous_state": previous,
                        "outcome": outcome,
                        "transaction_status": next_status,
                        "summary": summary,
                        "evidence": evidence,
                    },
                )
            return self.get(transaction_id)

    def finalize(
        self,
        transaction_id: str,
        expected_revision: int,
        decision: str,
        summary: str,
    ) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        summary = _bounded_text("summary", summary, 10_000)

        with self._lock:
            db = self._ensure()
            row = self._load(transaction_id)
            if row["status"] in TERMINAL_STATUSES:
                raise ValueError("Transaction is already terminal")
            if row["revision"] != expected_revision:
                raise ValueError(
                    f"Transaction changed since inspection: expected revision "
                    f"{expected_revision}, current {row['revision']}"
                )

            steps = json.loads(row["steps"])
            if any(step["state"] == "running" for step in steps):
                raise ValueError("Cannot finalize while a step is running")

            if decision == "commit":
                incomplete_actions = [
                    step["step_id"]
                    for step in steps
                    if step["kind"] == "action"
                    and step["state"] not in {"succeeded", "skipped"}
                ]
                incomplete_verify = [
                    step["step_id"]
                    for step in steps
                    if step["kind"] == "verify"
                    and step["state"] not in {"verified", "skipped"}
                ]
                failures = [
                    step["step_id"]
                    for step in steps
                    if step["state"] in {"failed", "interrupted"}
                ]
                if failures or incomplete_actions or incomplete_verify:
                    raise ValueError(
                        "Commit requires all action steps succeeded/skipped and all "
                        "verify steps verified/skipped"
                    )
                status = "committed"
            elif decision == "rolled_back":
                rollback_steps = [step for step in steps if step["kind"] == "rollback"]
                if not rollback_steps:
                    raise ValueError("Transaction has no rollback steps")
                if any(
                    step["state"] not in {"rolled_back", "skipped"}
                    for step in rollback_steps
                ):
                    raise ValueError("All rollback steps must be rolled_back or skipped")
                status = "rolled_back"
            else:
                status = "aborted"

            revision = row["revision"] + 1
            now = _now()
            with db:
                db.execute(
                    "UPDATE action_transactions SET status=?,revision=?,updated_at=?,"
                    "final_summary=? WHERE transaction_id=?",
                    (status, revision, now, summary, transaction_id),
                )
                self._event(
                    transaction_id,
                    "transaction_finalized",
                    {"decision": decision, "status": status, "summary": summary},
                )
            return self.get(transaction_id)

    def initialize(self) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            return {"status": "ready"}

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._owner_file is not None:
                self._owner_file.close()
                self._owner_file = None
            self._closed = True


TRANSACTION_STORE = ActionTransactionStore(
    db_path=os.environ.get(
        "AGENT_TRANSACTION_DB",
        str(Path(__file__).resolve().parent / "state" / "transactions.sqlite3"),
    ),
    require_outside_workspace=True,
)
