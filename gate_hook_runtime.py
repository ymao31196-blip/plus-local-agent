"""Pre-invocation Gate Hook runtime for PLA v1.2 Phase 3.

Gate hooks run only after the Capability Broker has completed its existing
availability, confirmation, transaction, artifact-contract, and JSON-schema
validation. Gates may inspect a deep-copied invocation context and return an
ALLOW or DENY decision. They cannot mutate the real provider arguments.

Unlike Phase 2 observer hooks, Gate Hook failures are fail-closed: an exception,
invalid result, or decision-persistence failure is treated as DENY.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


MAX_QUERY_LIMIT = 200
MAX_GATES = 64
GATE_STATUSES = {"completed", "failed"}
GATE_DECISIONS = {"allow", "deny"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(name: str, value: str, limit: int = 256) -> str:
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


def _json_sha256(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("gate result must be JSON serializable") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GateHookDescriptor:
    hook_id: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GateDecisionStore:
    """Durable Gate Hook decision records, separate from EventStore."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
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
            raise RuntimeError("GateDecisionStore is closed")
        if self._db is not None:
            return self._db

        db = None
        try:
            if self.db_path != ":memory:":
                path = Path(self.db_path).resolve()
                if self.require_outside_workspace:
                    from local_tools import WORKSPACE

                    if path.is_relative_to(WORKSPACE):
                        raise ValueError(
                            "Gate database must be outside the user workspace"
                        )
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

            db = sqlite3.connect(
                self.db_path,
                timeout=5,
                check_same_thread=False,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS gate_hook_decisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    hook_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    result_sha256 TEXT,
                    error_type TEXT,
                    error_message_sha256 TEXT,
                    error_message_length INTEGER
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS gate_decisions_by_hook "
                "ON gate_hook_decisions(hook_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS gate_decisions_by_correlation "
                "ON gate_hook_decisions(correlation_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS gate_decisions_by_capability "
                "ON gate_hook_decisions(capability_id, sequence)"
            )
            self._db = db
            return db
        except Exception:
            self._db = None
            if db is not None:
                db.close()
            if self._owner_file is not None:
                self._owner_file.close()
                self._owner_file = None
            raise

    def record(
        self,
        *,
        hook_id: str,
        context: dict[str, Any],
        started_at: str,
        finished_at: str,
        duration_ms: float,
        status: str,
        decision: str,
        reason_code: str,
        result_sha256: str | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        hook_id = _bounded_text("hook_id", hook_id)
        if status not in GATE_STATUSES:
            raise ValueError(f"status must be one of {sorted(GATE_STATUSES)}")
        if decision not in GATE_DECISIONS:
            raise ValueError(f"decision must be one of {sorted(GATE_DECISIONS)}")
        reason_code = _bounded_text("reason_code", reason_code, 128)
        if not isinstance(context, dict):
            raise TypeError("context must be an object")

        correlation_id = _bounded_text(
            "correlation_id", context.get("correlation_id"), 128
        )
        capability_id = _bounded_text(
            "capability_id", context.get("capability_id"), 256
        )
        provider_id = _bounded_text("provider_id", context.get("provider_id"), 256)
        if not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

        decision_id = uuid4().hex
        error_type = None
        error_message_sha256 = None
        error_message_length = None
        if error is not None:
            message = str(error)
            error_type = type(error).__name__
            error_message_sha256 = hashlib.sha256(
                message.encode("utf-8", errors="replace")
            ).hexdigest()
            error_message_length = len(message)

        with self._lock:
            db = self._ensure()
            with db:
                cursor = db.execute(
                    """
                    INSERT INTO gate_hook_decisions(
                        decision_id,hook_id,correlation_id,capability_id,provider_id,
                        started_at,finished_at,duration_ms,status,decision,reason_code,
                        result_sha256,error_type,error_message_sha256,error_message_length
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        hook_id,
                        correlation_id,
                        capability_id,
                        provider_id,
                        started_at,
                        finished_at,
                        float(duration_ms),
                        status,
                        decision,
                        reason_code,
                        result_sha256,
                        error_type,
                        error_message_sha256,
                        error_message_length,
                    ),
                )
                sequence = int(cursor.lastrowid)

        return {
            "sequence": sequence,
            "decision_id": decision_id,
            "hook_id": hook_id,
            "correlation_id": correlation_id,
            "capability_id": capability_id,
            "provider_id": provider_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": float(duration_ms),
            "status": status,
            "decision": decision,
            "reason_code": reason_code,
            "result_sha256": result_sha256,
            "error_type": error_type,
            "error_message_sha256": error_message_sha256,
            "error_message_length": error_message_length,
        }

    def query(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        hook_id: str | None = None,
        correlation_id: str | None = None,
        capability_id: str | None = None,
        provider_id: str | None = None,
        status: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}")

        clauses = ["sequence > ?"]
        params: list[Any] = [after_sequence]
        for column, value, maximum in (
            ("hook_id", hook_id, 256),
            ("correlation_id", correlation_id, 128),
            ("capability_id", capability_id, 256),
            ("provider_id", provider_id, 256),
        ):
            if value is not None:
                normalized = _bounded_text(column, value, maximum)
                clauses.append(f"{column} = ?")
                params.append(normalized)

        if status is not None:
            if status not in GATE_STATUSES:
                raise ValueError(
                    f"status must be one of {sorted(GATE_STATUSES)}"
                )
            clauses.append("status = ?")
            params.append(status)

        if decision is not None:
            if decision not in GATE_DECISIONS:
                raise ValueError(
                    f"decision must be one of {sorted(GATE_DECISIONS)}"
                )
            clauses.append("decision = ?")
            params.append(decision)

        sql = (
            "SELECT * FROM gate_hook_decisions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence ASC LIMIT ?"
        )
        params.append(limit + 1)
        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        records = [dict(row) for row in rows]
        next_cursor = int(records[-1]["sequence"]) if records else after_sequence
        return {
            "decisions": records,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "returned_count": len(records),
        }

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


class GateHookRuntime:
    """Deterministic deny-overrides gate dispatcher."""

    def __init__(self, decision_store: GateDecisionStore) -> None:
        self._store = decision_store
        self._hooks: dict[
            str,
            tuple[GateHookDescriptor, Callable[[dict[str, Any]], Any]],
        ] = {}
        self._lock = RLock()

    def register(
        self,
        hook_id: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        enabled: bool = True,
    ) -> None:
        hook_id = _bounded_text("hook_id", hook_id)
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            if hook_id in self._hooks:
                raise ValueError(f"Gate hook already registered: {hook_id}")
            if len(self._hooks) >= MAX_GATES:
                raise ValueError(f"At most {MAX_GATES} gate hooks are supported")
            self._hooks[hook_id] = (
                GateHookDescriptor(hook_id=hook_id, enabled=bool(enabled)),
                handler,
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            hooks = [
                descriptor.to_dict()
                for descriptor, _handler in self._hooks.values()
            ]
        return {
            "status": "ready",
            "hook_count": len(hooks),
            "hooks": sorted(hooks, key=lambda item: item["hook_id"]),
        }

    @staticmethod
    def _normalize_result(value: Any) -> tuple[str, str, str]:
        if not isinstance(value, dict):
            raise ValueError("gate result must be an object")
        decision = value.get("decision")
        if decision not in GATE_DECISIONS:
            raise ValueError("gate decision must be 'allow' or 'deny'")
        reason_code = value.get("reason_code")
        if reason_code is None:
            reason_code = "allowed" if decision == "allow" else "denied"
        reason_code = _bounded_text("reason_code", reason_code, 128)
        return decision, reason_code, _json_sha256(
            {"decision": decision, "reason_code": reason_code}
        )

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, dict):
            raise TypeError("context must be an object")
        for field, maximum in (
            ("correlation_id", 128),
            ("capability_id", 256),
            ("provider_id", 256),
        ):
            _bounded_text(field, context.get(field), maximum)

        with self._lock:
            selected = [
                (hook_id, descriptor, handler)
                for hook_id, (descriptor, handler) in self._hooks.items()
                if descriptor.enabled
            ]

        records: list[dict[str, Any]] = []
        deny_hook_ids: list[str] = []
        persistence_failed = False

        for hook_id, _descriptor, handler in sorted(
            selected, key=lambda item: item[0]
        ):
            started_at = _now()
            started = perf_counter()
            error = None
            try:
                result = handler(deepcopy(context))
                decision, reason_code, result_sha256 = self._normalize_result(result)
                status = "completed"
            except Exception as exc:
                decision = "deny"
                reason_code = "gate_error"
                result_sha256 = None
                status = "failed"
                error = exc

            finished_at = _now()
            duration_ms = (perf_counter() - started) * 1000.0
            try:
                record = self._store.record(
                    hook_id=hook_id,
                    context=context,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status=status,
                    decision=decision,
                    reason_code=reason_code,
                    result_sha256=result_sha256,
                    error=error,
                )
                records.append(record)
            except Exception:
                persistence_failed = True
                decision = "deny"

            if decision == "deny":
                deny_hook_ids.append(hook_id)

        if persistence_failed and "__decision_store__" not in deny_hook_ids:
            deny_hook_ids.append("__decision_store__")

        denied = bool(deny_hook_ids)
        return {
            "status": "denied" if denied else "allowed",
            "decision": "deny" if denied else "allow",
            "matched_count": len(selected),
            "deny_hook_ids": deny_hook_ids,
            "persistence_failed": persistence_failed,
            "records": records,
        }

    def query_decisions(self, **filters: Any) -> dict[str, Any]:
        return self._store.query(**filters)


GATE_DECISION_STORE = GateDecisionStore(
    db_path=os.environ.get(
        "PLA_GATE_DB",
        str(Path(__file__).resolve().parent / "state" / "gates.sqlite3"),
    ),
    require_outside_workspace=True,
)

# Phase 3 ships the reviewed in-process gate runtime without a default policy gate.
# Production behavior therefore remains unchanged until a reviewed gate is
# explicitly registered by the runtime.
GATE_HOOK_RUNTIME = GateHookRuntime(GATE_DECISION_STORE)
