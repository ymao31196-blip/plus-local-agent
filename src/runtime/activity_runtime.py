"""Metadata-only local tool activity for PLA Desktop.

This plane never receives tool arguments, file contents or process output.
Failures in telemetry are fail-open and must not affect the actual tool.
"""
from __future__ import annotations

from contextlib import closing
from functools import wraps
from pathlib import Path
import sqlite3
from typing import Any, Callable, TypeVar
from uuid import uuid4

from runtime.event_runtime import EVENT_STORE

F = TypeVar("F", bound=Callable[..., Any])

_START_TYPES = (
    "native_tool.started", "local_action.started",
    "capability.before_invoke", "capability.gate_denied",
)
_END_TYPES = (
    "native_tool.succeeded", "native_tool.failed",
    "local_action.succeeded", "local_action.failed",
    "capability.succeeded", "capability.failed", "capability.gate_denied",
)


def _emit(event_type: str, tool: str, source: str, call_id: str) -> None:
    try:
        EVENT_STORE.emit(
            event_type,
            source=source,
            subject=tool,
            correlation_id=call_id,
            payload={},
        )
    except Exception:
        # Observability must never change execution, permission checks, or results.
        pass


def result_failed(result: Any) -> bool:
    """Classify tool-level failures even when no Python exception was raised."""
    if not isinstance(result, dict):
        return False
    if result.get("is_error") is True or result.get("ok") is False:
        return True
    if result.get("status") in {
        "failed", "error", "blocked", "timeout", "partial",
        "precondition_failed", "cancelled",
    }:
        return True
    if result.get("timeout") is True:
        return True
    code = result.get("returncode")
    return code is not None and type(code) is int and code != 0


def record_native_activity(function: F) -> F:
    """Wrap a direct MCP endpoint without changing FastMCP's tool schema."""
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        call_id = uuid4().hex
        name = function.__name__
        _emit("native_tool.started", name, "native_mcp", call_id)
        try:
            result = function(*args, **kwargs)
        except BaseException:
            _emit("native_tool.failed", name, "native_mcp", call_id)
            raise
        _emit(
            "native_tool.failed" if result_failed(result) else "native_tool.succeeded",
            name, "native_mcp", call_id,
        )
        return result
    return wrapped  # type: ignore[return-value]


def record_internal_action_start(tool: str) -> str:
    call_id = uuid4().hex
    _emit("local_action.started", tool, "local_action", call_id)
    return call_id


def record_internal_action_finish(tool: str, call_id: str, ok: bool) -> None:
    _emit(
        "local_action.succeeded" if ok else "local_action.failed",
        tool, "local_action", call_id,
    )


def recent_activity(db_path: str | Path, limit: int = 5) -> dict[str, Any]:
    """Read a small, normalized activity snapshot using a separate read-only connection.

    Uses an indexed descending lookup by sequence, then correlation-indexed
    terminal lookups. Does not initialize, migrate or write to the database.
    """
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Activity limit must be between 1 and 10")
    path = Path(db_path)
    if not path.is_file():
        return {"activities": [], "available": True}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            placeholders = ",".join("?" for _ in _START_TYPES)
            base = db.execute(
                f"""SELECT sequence, timestamp, event_type, source, subject,
                           correlation_id, provider_id
                    FROM runtime_events
                    WHERE event_type IN ({placeholders})
                    AND NOT (
                        event_type = 'capability.before_invoke'
                        AND (
                            subject LIKE 'runtime.%'
                            OR subject LIKE 'core.event_%'
                            OR subject LIKE 'core.hook_%'
                            OR subject LIKE 'core.gate_%'
                        )
                    )
                    ORDER BY sequence DESC LIMIT ?""",
                (*_START_TYPES, limit),
            ).fetchall()
            if not base:
                return {"activities": [], "available": True}
            ids = [row["correlation_id"] for row in base]
            slots = ",".join("?" for _ in ids)
            end_slots = ",".join("?" for _ in _END_TYPES)
            terminal = db.execute(
                f"""SELECT correlation_id, event_type, timestamp
                    FROM runtime_events
                    WHERE correlation_id IN ({slots})
                      AND event_type IN ({end_slots})
                    ORDER BY sequence DESC""",
                (*ids, *_END_TYPES),
            ).fetchall()
            finished = {}
            for row in terminal:
                finished.setdefault(row["correlation_id"], row)
            results = []
            for row in base:
                end = finished.get(row["correlation_id"])
                kind = row["event_type"].split(".")[0]
                source = {
                    "native_tool": "native_mcp",
                    "local_action": "local_action",
                    "capability": "capability",
                }[kind]
                result = ("failed" if end["event_type"].endswith(("failed", "gate_denied"))
                          else "success") if end else "unknown"
                results.append({
                    "id": row["correlation_id"],
                    "tool": row["subject"],
                    "source": source,
                    "provider": row["provider_id"] if source == "capability" else None,
                    "started_at": row["timestamp"],
                    "finished_at": end["timestamp"] if end else None,
                    "outcome": result,
                })
            return {"activities": results, "available": True}
    except (sqlite3.DatabaseError, OSError) as exc:
        # UI must show stale/unavailable rather than falsely claiming no activity.
        return {"activities": [], "available": False, "reason": type(exc).__name__}
