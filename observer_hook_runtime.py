"""Observer-only hook runtime for PLA v1.2 Phase 2.

Observer hooks receive already-persisted EventEnvelope facts. They cannot alter
capability arguments, decisions, or results. Hook failures are recorded and
remain fail-open relative to capability execution.
"""

from __future__ import annotations

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
MAX_HOOKS = 64
HOOK_STATUSES = {"completed", "failed"}


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
        raise ValueError("hook result must be JSON serializable") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ObserverHookDescriptor:
    hook_id: str
    event_types: tuple[str, ...]
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HookInvocationStore:
    """Durable observer-hook execution records, separate from EventStore."""

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
            raise RuntimeError("HookInvocationStore is closed")
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
                            "Hook database must be outside the user workspace"
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
                CREATE TABLE IF NOT EXISTS observer_hook_invocations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    invocation_id TEXT NOT NULL UNIQUE,
                    hook_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    result_sha256 TEXT,
                    error_type TEXT,
                    error_message_sha256 TEXT,
                    error_message_length INTEGER
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS hook_invocations_by_hook "
                "ON observer_hook_invocations(hook_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS hook_invocations_by_event "
                "ON observer_hook_invocations(event_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS hook_invocations_by_correlation "
                "ON observer_hook_invocations(correlation_id, sequence)"
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
        event: dict[str, Any],
        started_at: str,
        finished_at: str,
        duration_ms: float,
        status: str,
        result_sha256: str | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        hook_id = _bounded_text("hook_id", hook_id)
        if status not in HOOK_STATUSES:
            raise ValueError(f"status must be one of {sorted(HOOK_STATUSES)}")
        if not isinstance(event, dict):
            raise TypeError("event must be an object")

        event_id = _bounded_text("event_id", event.get("event_id"), 128)
        event_type = _bounded_text("event_type", event.get("event_type"), 256)
        correlation_id = _bounded_text(
            "correlation_id", event.get("correlation_id"), 128
        )
        event_sequence = event.get("sequence")
        if type(event_sequence) is not int or event_sequence < 1:
            raise ValueError("event.sequence must be a positive integer")
        if not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

        invocation_id = uuid4().hex
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
                    INSERT INTO observer_hook_invocations(
                        invocation_id,hook_id,event_id,event_sequence,event_type,
                        correlation_id,started_at,finished_at,duration_ms,status,
                        result_sha256,error_type,error_message_sha256,
                        error_message_length
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        invocation_id,
                        hook_id,
                        event_id,
                        event_sequence,
                        event_type,
                        correlation_id,
                        started_at,
                        finished_at,
                        float(duration_ms),
                        status,
                        result_sha256,
                        error_type,
                        error_message_sha256,
                        error_message_length,
                    ),
                )
                sequence = int(cursor.lastrowid)

        return {
            "sequence": sequence,
            "invocation_id": invocation_id,
            "hook_id": hook_id,
            "event_id": event_id,
            "event_sequence": event_sequence,
            "event_type": event_type,
            "correlation_id": correlation_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": float(duration_ms),
            "status": status,
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
        event_id: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= MAX_QUERY_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {MAX_QUERY_LIMIT}"
            )
        clauses = ["sequence > ?"]
        params: list[Any] = [after_sequence]

        for column, value, maximum in (
            ("hook_id", hook_id, 256),
            ("event_id", event_id, 128),
            ("event_type", event_type, 256),
            ("correlation_id", correlation_id, 128),
        ):
            if value is not None:
                normalized = _bounded_text(column, value, maximum)
                clauses.append(f"{column} = ?")
                params.append(normalized)

        if status is not None:
            if status not in HOOK_STATUSES:
                raise ValueError(
                    f"status must be one of {sorted(HOOK_STATUSES)}"
                )
            clauses.append("status = ?")
            params.append(status)

        sql = (
            "SELECT * FROM observer_hook_invocations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence ASC LIMIT ?"
        )
        params.append(limit + 1)

        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        records = [dict(row) for row in rows]
        next_cursor = (
            int(records[-1]["sequence"]) if records else after_sequence
        )
        return {
            "invocations": records,
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


class ObserverHookRuntime:
    """Deterministic observer dispatcher. Observer failures never raise."""

    def __init__(self, invocation_store: HookInvocationStore) -> None:
        self._store = invocation_store
        self._hooks: dict[
            str,
            tuple[ObserverHookDescriptor, Callable[[dict[str, Any]], Any]],
        ] = {}
        self._lock = RLock()

    def register(
        self,
        hook_id: str,
        event_types: tuple[str, ...],
        handler: Callable[[dict[str, Any]], Any],
        *,
        enabled: bool = True,
    ) -> None:
        hook_id = _bounded_text("hook_id", hook_id)
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not isinstance(event_types, tuple) or not event_types:
            raise ValueError("event_types must be a non-empty tuple")
        normalized = tuple(
            _bounded_text("event_type", item, 256)
            for item in event_types
        )
        if len(set(normalized)) != len(normalized):
            raise ValueError("event_types must be unique")
        with self._lock:
            if hook_id in self._hooks:
                raise ValueError(f"Observer hook already registered: {hook_id}")
            if len(self._hooks) >= MAX_HOOKS:
                raise ValueError(f"At most {MAX_HOOKS} observer hooks are supported")
            descriptor = ObserverHookDescriptor(
                hook_id=hook_id,
                event_types=normalized,
                enabled=bool(enabled),
            )
            self._hooks[hook_id] = (descriptor, handler)

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

    def dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise TypeError("event must be an object")
        event_type = _bounded_text(
            "event.event_type", event.get("event_type"), 256
        )

        with self._lock:
            selected = [
                (hook_id, descriptor, handler)
                for hook_id, (descriptor, handler) in self._hooks.items()
                if descriptor.enabled
                and (
                    "*" in descriptor.event_types
                    or event_type in descriptor.event_types
                )
            ]

        completed = 0
        failed = 0
        records: list[dict[str, Any]] = []

        for hook_id, _descriptor, handler in sorted(
            selected, key=lambda item: item[0]
        ):
            started_at = _now()
            started = perf_counter()
            try:
                result = handler(event)
                result_sha256 = _json_sha256(result)
                status = "completed"
                error = None
                completed += 1
            except Exception as exc:
                result_sha256 = None
                status = "failed"
                error = exc
                failed += 1
            finished_at = _now()
            duration_ms = (perf_counter() - started) * 1000.0

            try:
                record = self._store.record(
                    hook_id=hook_id,
                    event=event,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status=status,
                    result_sha256=result_sha256,
                    error=error,
                )
                records.append(record)
            except Exception:
                # Observer persistence is itself observational. Never let a
                # hook-recording failure mutate the selected capability path.
                pass

        return {
            "status": "completed" if failed == 0 else "partial",
            "matched_count": len(selected),
            "completed_count": completed,
            "failed_count": failed,
            "records": records,
        }

    def query_invocations(self, **filters: Any) -> dict[str, Any]:
        return self._store.query(**filters)


def audit_observer(event: dict[str, Any]) -> dict[str, Any]:
    """Built-in observer: attest that one persisted event was observed."""
    return {
        "event_id": event.get("event_id"),
        "event_sequence": event.get("sequence"),
        "event_type": event.get("event_type"),
        "correlation_id": event.get("correlation_id"),
        "payload_sha256": event.get("payload_sha256"),
    }


HOOK_INVOCATION_STORE = HookInvocationStore(
    db_path=os.environ.get(
        "PLA_HOOK_DB",
        str(Path(__file__).resolve().parent / "state" / "hooks.sqlite3"),
    ),
    require_outside_workspace=True,
)

OBSERVER_HOOK_RUNTIME = ObserverHookRuntime(HOOK_INVOCATION_STORE)
OBSERVER_HOOK_RUNTIME.register(
    "audit-observer",
    (
        "capability.before_invoke",
        "capability.succeeded",
        "capability.failed",
    ),
    audit_observer,
)
