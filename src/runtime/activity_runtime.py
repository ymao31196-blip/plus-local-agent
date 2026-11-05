"""Metadata-only local tool activity for PLA Desktop.

This plane never receives tool arguments, file contents or process output.
Failures in telemetry are fail-open and must not affect the actual tool.
"""
from __future__ import annotations

from contextlib import closing
from contextvars import ContextVar
import json
import ntpath
import re
from functools import wraps
from pathlib import Path
import sqlite3
from typing import Any, Callable, TypeVar
from uuid import uuid4

from runtime.event_runtime import EVENT_STORE
from runtime.failure_diagnostics import (
    exception_diagnostic, failure_projection, result_diagnostic,
)

F = TypeVar("F", bound=Callable[..., Any])
# A synchronous native MCP endpoint can dispatch the same operation internally.
# Keep one top-level activity rather than showing the internal implementation twice.
_NATIVE_CALL: ContextVar[str | None] = ContextVar("pla_activity_native_call", default=None)
_PROGRAM_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}\Z")

_START_TYPES = (
    "native_tool.started", "local_action.started",
    "capability.before_invoke", "capability.gate_denied",
    "capability.policy_denied",
)
_END_TYPES = (
    "native_tool.succeeded", "native_tool.failed",
    "local_action.succeeded", "local_action.failed",
    "capability.succeeded", "capability.failed", "capability.gate_denied",
    "capability.policy_denied",
)


def _safe_program_name(program: Any) -> str | None:
    """Persist only an approved executable basename, never paths or arguments."""
    if not isinstance(program, str) or not program:
        return None
    name = ntpath.basename(program.replace("/", "\\"))
    if not _PROGRAM_BASENAME.fullmatch(name):
        return None
    try:
        from execution.execution_program_policy import ALLOWED_PROGRAMS
        from execution.user_program_permissions import executable_is_granted
        if name.casefold() not in ALLOWED_PROGRAMS and not executable_is_granted(program):
            return None
    except Exception:
        return None
    return name


def _emit(event_type: str, tool: str, source: str, call_id: str,
          payload: dict[str, Any] | None = None) -> None:
    try:
        EVENT_STORE.emit(
            event_type,
            source=source,
            subject=tool,
            correlation_id=call_id,
            payload=payload or {},
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
        "precondition_failed", "cancelled", "not_ready",
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
        program = _safe_program_name(
            kwargs.get("program", args[0] if args else None)
        ) if name == "run_process" else None
        _emit("native_tool.started", name, "native_mcp", call_id,
              {"program": program} if program else None)
        token = _NATIVE_CALL.set(call_id)
        try:
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                _emit("native_tool.failed", name, "native_mcp", call_id,
                      exception_diagnostic(exc))
                raise
            failed = result_failed(result)
            _emit(
                "native_tool.failed" if failed else "native_tool.succeeded",
                name, "native_mcp", call_id,
                result_diagnostic(result) if failed else None,
            )
            return result
        finally:
            _NATIVE_CALL.reset(token)
    return wrapped  # type: ignore[return-value]


def record_internal_action_start(
    tool: str, arguments: dict[str, Any] | None = None,
) -> str | None:
    if _NATIVE_CALL.get() is not None:
        return None
    call_id = uuid4().hex
    program = _safe_program_name((arguments or {}).get("program")) if tool == "run_process" else None
    _emit("local_action.started", tool, "local_action", call_id,
          {"program": program} if program else None)
    return call_id


def record_internal_action_finish(
    tool: str, call_id: str | None, ok: bool, *,
    result: Any = None, error: Any = None, exception: BaseException | None = None,
) -> None:
    if call_id is not None:
        diagnostic = None
        if not ok:
            if exception is not None:
                diagnostic = exception_diagnostic(exception)
            else:
                structured = dict(result) if isinstance(result, dict) else {}
                if isinstance(error, dict):
                    structured["error"] = error
                diagnostic = result_diagnostic(structured)
        _emit(
            "local_action.succeeded" if ok else "local_action.failed",
            tool, "local_action", call_id, diagnostic,
        )


def recent_activity(db_path: str | Path, limit: int = 5, *, only_components: bool = False) -> dict[str, Any]:
    """Read a small, normalized activity snapshot using a separate read-only connection.

    Uses an indexed descending lookup by sequence, then correlation-indexed
    terminal lookups. Does not initialize, migrate or write to the database.
    """
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Activity limit must be between 1 and 30")
    path = Path(db_path)
    if not path.is_file():
        return {"activities": [], "available": True}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            start_types = _START_TYPES[2:] if only_components else _START_TYPES
            placeholders = ",".join("?" for _ in start_types)
            base = db.execute(
                f"""SELECT sequence, timestamp, event_type, source, subject,
                           correlation_id, provider_id, capability_id, payload
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
                (*start_types, limit),
            ).fetchall()
            if not base:
                return {"activities": [], "available": True}
            ids = [row["correlation_id"] for row in base]
            slots = ",".join("?" for _ in ids)
            end_slots = ",".join("?" for _ in _END_TYPES)
            terminal = db.execute(
                f"""SELECT correlation_id, event_type, timestamp, payload
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
                result = ("failed" if end["event_type"].endswith(
                    ("failed", "gate_denied", "policy_denied"))
                          else "success") if end else "unknown"
                try:
                    metadata = json.loads(row["payload"]) if row["payload"] else {}
                except (ValueError, TypeError):
                    metadata = {}
                program = metadata.get("program") if isinstance(metadata, dict) else None
                if not isinstance(program, str) or not _PROGRAM_BASENAME.fullmatch(program):
                    program = None
                diagnostic = {}
                if end and result == "failed":
                    try:
                        diagnostic = failure_projection(json.loads(end["payload"]))
                    except (ValueError, TypeError):
                        pass
                results.append({
                    "id": row["correlation_id"],
                    "tool": row["subject"],
                    "source": source,
                    "provider": row["provider_id"] if source == "capability" else None,
                    "capability": row["capability_id"] if source == "capability" else None,
                    "program": program if source != "capability" else None,
                    "started_at": row["timestamp"],
                    "finished_at": end["timestamp"] if end else None,
                    "outcome": result,
                    **diagnostic,
                })
            return {"activities": results, "available": True}
    except (sqlite3.DatabaseError, OSError) as exc:
        # UI must show stale/unavailable rather than falsely claiming no activity.
        return {"activities": [], "available": False, "reason": type(exc).__name__}
