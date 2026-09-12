"""Global append-only event plane for PLA.

The EventStore records cross-cutting runtime facts without owning control flow.
TaskStore and ActionTransactionStore remain their own sources of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


EVENT_SCHEMA_VERSION = 1
MAX_PAYLOAD_CHARACTERS = 100_000
MAX_QUERY_LIMIT = 200
_EVENT_TYPE_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_optional_text(
    name: str,
    value: str | None,
    *,
    required: bool = False,
    limit: int = 512,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if not value:
        if required:
            raise ValueError(f"{name} must be non-empty")
        return None
    if "\x00" in value:
        raise ValueError(f"{name} cannot contain NUL")
    if len(value) > limit:
        raise ValueError(f"{name} cannot exceed {limit} characters")
    return value


def _canonical_payload(payload: dict[str, Any] | None) -> tuple[dict[str, Any], str, str]:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    if len(serialized) > MAX_PAYLOAD_CHARACTERS:
        raise ValueError(
            f"payload cannot exceed {MAX_PAYLOAD_CHARACTERS} characters"
        )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return payload, serialized, digest


@dataclass(frozen=True)
class EventEnvelope:
    sequence: int
    schema_version: int
    event_id: str
    timestamp: str
    event_type: str
    source: str
    subject: str
    correlation_id: str
    causation_id: str | None
    capability_id: str | None
    provider_id: str | None
    transaction_id: str | None
    task_id: str | None
    payload: dict[str, Any]
    payload_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventStore:
    """Append-only SQLite event store with cursor-based querying."""

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
            raise RuntimeError("EventStore is closed")
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
                            "Event database must be outside the user workspace"
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
                CREATE TABLE IF NOT EXISTS runtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    capability_id TEXT,
                    provider_id TEXT,
                    transaction_id TEXT,
                    task_id TEXT,
                    payload TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runtime_events_by_type "
                "ON runtime_events(event_type, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runtime_events_by_correlation "
                "ON runtime_events(correlation_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runtime_events_by_capability "
                "ON runtime_events(capability_id, sequence)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runtime_events_by_provider "
                "ON runtime_events(provider_id, sequence)"
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

    @staticmethod
    def _decode(row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            sequence=int(row["sequence"]),
            schema_version=int(row["schema_version"]),
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            event_type=row["event_type"],
            source=row["source"],
            subject=row["subject"],
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            capability_id=row["capability_id"],
            provider_id=row["provider_id"],
            transaction_id=row["transaction_id"],
            task_id=row["task_id"],
            payload=json.loads(row["payload"]),
            payload_sha256=row["payload_sha256"],
        )

    def emit(
        self,
        event_type: str,
        *,
        source: str,
        subject: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        capability_id: str | None = None,
        provider_id: str | None = None,
        transaction_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(event_type, str) or not _EVENT_TYPE_RE.fullmatch(
            event_type
        ):
            raise ValueError(
                "event_type must be a dotted lowercase identifier"
            )
        source = _bounded_optional_text(
            "source", source, required=True, limit=128
        )
        subject = _bounded_optional_text(
            "subject", subject, required=True, limit=512
        )
        correlation_id = _bounded_optional_text(
            "correlation_id",
            correlation_id or uuid4().hex,
            required=True,
            limit=128,
        )
        causation_id = _bounded_optional_text(
            "causation_id", causation_id, limit=128
        )
        capability_id = _bounded_optional_text(
            "capability_id", capability_id, limit=256
        )
        provider_id = _bounded_optional_text(
            "provider_id", provider_id, limit=128
        )
        transaction_id = _bounded_optional_text(
            "transaction_id", transaction_id, limit=128
        )
        task_id = _bounded_optional_text("task_id", task_id, limit=128)
        payload, serialized, payload_sha256 = _canonical_payload(payload)

        event_id = uuid4().hex
        timestamp = _now()
        with self._lock:
            db = self._ensure()
            with db:
                cursor = db.execute(
                    """
                    INSERT INTO runtime_events(
                        event_id,schema_version,timestamp,event_type,source,
                        subject,correlation_id,causation_id,capability_id,
                        provider_id,transaction_id,task_id,payload,payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        EVENT_SCHEMA_VERSION,
                        timestamp,
                        event_type,
                        source,
                        subject,
                        correlation_id,
                        causation_id,
                        capability_id,
                        provider_id,
                        transaction_id,
                        task_id,
                        serialized,
                        payload_sha256,
                    ),
                )
                sequence = int(cursor.lastrowid)

        return EventEnvelope(
            sequence=sequence,
            schema_version=EVENT_SCHEMA_VERSION,
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            source=source,
            subject=subject,
            correlation_id=correlation_id,
            causation_id=causation_id,
            capability_id=capability_id,
            provider_id=provider_id,
            transaction_id=transaction_id,
            task_id=task_id,
            payload=payload,
            payload_sha256=payload_sha256,
        ).to_dict()

    def query(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        event_types: list[str] | None = None,
        correlation_id: str | None = None,
        capability_id: str | None = None,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= MAX_QUERY_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {MAX_QUERY_LIMIT}"
            )

        clauses = ["sequence > ?"]
        params: list[Any] = [after_sequence]

        if event_types is not None:
            if (
                not isinstance(event_types, list)
                or not 1 <= len(event_types) <= 32
                or len(set(event_types)) != len(event_types)
            ):
                raise ValueError(
                    "event_types must contain 1-32 unique event types"
                )
            for event_type in event_types:
                if (
                    not isinstance(event_type, str)
                    or not _EVENT_TYPE_RE.fullmatch(event_type)
                ):
                    raise ValueError(
                        "event_types must contain dotted lowercase identifiers"
                    )
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        for column, value, maximum in (
            ("correlation_id", correlation_id, 128),
            ("capability_id", capability_id, 256),
            ("provider_id", provider_id, 128),
        ):
            normalized = _bounded_optional_text(
                column, value, limit=maximum
            )
            if normalized is not None:
                clauses.append(f"{column} = ?")
                params.append(normalized)

        sql = (
            "SELECT * FROM runtime_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence ASC LIMIT ?"
        )
        params.append(limit + 1)

        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [self._decode(row).to_dict() for row in rows]
        next_cursor = (
            events[-1]["sequence"] if events else after_sequence
        )
        return {
            "events": events,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "returned_count": len(events),
        }

    def initialize(self) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            return {
                "status": "ready",
                "schema_version": EVENT_SCHEMA_VERSION,
            }

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._owner_file is not None:
                self._owner_file.close()
                self._owner_file = None
            self._closed = True


EVENT_STORE = EventStore(
    db_path=os.environ.get(
        "PLA_EVENT_DB",
        str(Path(__file__).resolve().parent / "state" / "events.sqlite3"),
    ),
    require_outside_workspace=True,
)
