"""Privacy-preserving ChatGPT conversation identity and activity attribution.

Automatic attribution uses only the host-provided openai/session request
metadata. Transport connection IDs, request IDs and model arguments are not
treated as conversation identities. Legacy agent-declared check-ins remain
separate from automatic tool-call attribution.
"""
from __future__ import annotations

from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from runtime.event_runtime import EVENT_STORE

_KEY = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
_MAX_EVENTS = 10000
_EVENT = "conversation.checkin"
ACTIVE_WINDOW_SECONDS = 60
RECENTLY_STOPPED_SECONDS = 60


def _digest(prefix: str, value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256((prefix + ":" + value).encode("utf-8")).hexdigest()


def transport_probe(ctx: Any) -> dict[str, Any]:
    """Expose only hashes/presence; never raw client/session IDs or metadata."""
    result: dict[str, Any] = {
        "protocol": "unknown",
        "client_fingerprint": None,
        "request_session_fingerprint": None,
        "conversation_identity_from_transport": False,
    }
    if ctx is None:
        return result
    try:
        req = getattr(ctx, "request_context", None)
        version = getattr(req, "protocol_version", None)
        if isinstance(version, str):
            result["protocol"] = "mrtr" if version >= "2026-07-28" else "legacy"
        result["client_fingerprint"] = _digest("client", getattr(ctx, "client_id", None))
        result["request_session_fingerprint"] = _digest("session", getattr(ctx, "session_id", None))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    # Neither the MCP client ID nor the transport session is evidence of a
    # distinct ChatGPT conversation. MRTR may allocate a session per request.
    return result


def conversation_checkin(conversation_key: str, ctx: Any = None) -> dict[str, Any]:
    """Check in one dialogue using a caller-generated, dialogue-stable random key."""
    if not isinstance(conversation_key, str) or not _KEY.fullmatch(conversation_key):
        raise ValueError("conversation_key must be 20-80 ASCII letters, digits, - or _")
    fingerprint = _digest("pla-conversation-v1", conversation_key)
    assert fingerprint is not None
    try:
        EVENT_STORE.emit(
            _EVENT,
            source="agent_declared_conversation",
            subject="anonymous_dialogue",
            correlation_id=uuid4().hex,
            payload={"fingerprint": fingerprint},
        )
        registered = True
    except Exception:
        # Telemetry is advisory and never gates the actual tools.
        registered = False
    return {
        "registered": registered,
        "method": "agent_declared" if registered else "unavailable",
        "anonymous_id": fingerprint[:10] if registered else None,
        "transport_probe": transport_probe(ctx),
        "note": "This check-in is not a verified browser-window identifier.",
    }


def conversation_summary(
    db_path: str | Path, *, runtime_started_at: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only 60-second check-in activity, not a browser-window count.

    An agent must check in after *each actual PLA tool use* to refresh this
    lease. Ordinary tools carry no conversation ID and cannot refresh it.
    """
    empty = {
        "available": True, "method": "agent_declared", "identified_count": 0,
        "conversations": [], "truncated": False,
        "is_exact_window_count": False,
        "active_window_seconds": ACTIVE_WINDOW_SECONDS,
    }
    if runtime_started_at is None:
        return empty
    path = Path(db_path)
    if not path.is_file():
        return empty
    try:
        # Count check-ins no older than 60 seconds, and never before Runtime start.
        started = datetime.fromisoformat(runtime_started_at)
        observed_at = datetime.now(timezone.utc) if now is None else now
        if started.tzinfo is None or observed_at.tzinfo is None:
            return {**empty, "available": False}
        observed_utc = observed_at.astimezone(timezone.utc)
        if started > observed_utc:
            return empty
        cutoff = max(started.astimezone(timezone.utc),
                     observed_utc - timedelta(seconds=ACTIVE_WINDOW_SECONDS))
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute(
                """SELECT timestamp, payload FROM runtime_events
                   WHERE event_type = ? AND timestamp > ? AND timestamp <= ?
                   ORDER BY sequence DESC LIMIT ?""",
                (_EVENT, cutoff.isoformat(), observed_utc.isoformat(), _MAX_EVENTS + 1),
            ).fetchall()
        latest: dict[str, tuple[str, int]] = {}
        for timestamp, raw_payload in rows[:_MAX_EVENTS]:
            try:
                import json
                fingerprint = json.loads(raw_payload).get("fingerprint")
            except (ValueError, TypeError, AttributeError):
                continue
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                continue
            if fingerprint not in latest:
                latest[fingerprint] = (timestamp, 1)
            else:
                seen, n = latest[fingerprint]
                latest[fingerprint] = (seen, n + 1)
        return {
            **empty,
            "identified_count": len(latest),
            "conversations": [
                {"anonymous_id": fingerprint[:10], "last_seen_at": seen, "checkins": n}
                for fingerprint, (seen, n) in list(latest.items())[:20]
            ],
            "truncated": len(rows) > _MAX_EVENTS,
        }
    except (sqlite3.DatabaseError, OSError, ValueError):
        return {**empty, "available": False}


# The automatic path is isolated from the previous model-declared check-ins.
# Legacy conversation.checkin events never contribute to these counts.
_AUTO_EVENT_TYPES = (
    "conversation.tool_started", "conversation.tool_finished",
    "conversation.tool_failed",
)
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")
CURRENT_CONVERSATION_NUMBER: ContextVar[int | None] = ContextVar(
    "pla_current_conversation_number", default=None
)


def current_conversation_number() -> int | None:
    value = CURRENT_CONVERSATION_NUMBER.get()
    return value if type(value) is int and value > 0 else None


def format_conversation_label(number: Any) -> str:
    """Format a local numeric identity with a two-digit minimum and no fixed maximum."""
    if type(number) is not int or number < 1:
        return "?"
    return f"C{number:02d}"


# The Desktop process issues routine local MCP calls to inspect itself. Its
# explicit marker is telemetry-only: it cannot grant capabilities or access.
DESKTOP_MANAGEMENT_META_KEY = "io.pla.desktop/request_origin"
DESKTOP_MANAGEMENT_META = {DESKTOP_MANAGEMENT_META_KEY: "desktop_manager"}


def safe_tool_name(name: Any) -> str:
    """Persist only a syntactically safe tool identifier; never arguments."""
    return name if isinstance(name, str) and _SAFE_TOOL_NAME.fullmatch(name) else "unknown_mcp_tool"


def session_identity(ctx: Any) -> dict[str, Any]:
    """Read only trusted MCP request metadata, not a model-chosen tool argument.

    Returned fingerprint is internal; user-visible diagnostics use only its
    short form and an availability boolean. No raw session is logged.
    """
    try:
        request_context = getattr(ctx, "request_context", None)
        meta = getattr(request_context, "meta", None)
        raw = meta.get("openai/session") if isinstance(meta, dict) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        raw = None
    if not isinstance(raw, str) or not (1 <= len(raw) <= 1024):
        return {"available": False, "fingerprint": None, "anonymous_id": None}
    fingerprint = _digest("openai-session-v1", raw)
    return {"available": True, "fingerprint": fingerprint,
            "anonymous_id": fingerprint[:10] if fingerprint else None}


def is_desktop_management_request(ctx: Any) -> bool:
    """Recognize *marked* Desktop self-inspection, never a ChatGPT session.

    This is an accounting hint, not an authentication/security boundary. Do
    not exempt a request containing an actual openai/session identity.
    """
    if session_identity(ctx)["available"]:
        return False
    try:
        request_context = getattr(ctx, "request_context", None)
        meta = getattr(request_context, "meta", None)
        return (isinstance(meta, dict)
                and meta.get(DESKTOP_MANAGEMENT_META_KEY) == "desktop_manager")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def session_diagnostic(ctx: Any) -> dict[str, Any]:
    identity = session_identity(ctx)
    return {
        "openai_session_present": identity["available"],
        "anonymous_id": identity["anonymous_id"],
        "source": "mcp_request_meta",
        "verified_distinct_conversation": False,
    }


def emit_tool_activity(
    event_type: str, *, correlation_id: str, tool: str,
    fingerprint: str | None, conversation_number: int | None = None,
) -> None:
    """Record automatic attribution with no raw session/parameters, fail open."""
    if event_type not in _AUTO_EVENT_TYPES:
        raise ValueError("Unsupported tool activity event")
    payload = (
        {"identity": "openai_session", "fingerprint": fingerprint}
        if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        else {"identity": "missing"}
    )
    number = conversation_number if type(conversation_number) is int and conversation_number > 0 else current_conversation_number()
    if number is not None:
        payload["conversation_number"] = number
    try:
        EVENT_STORE.emit(
            event_type,
            source="conversation_transport",
            subject=safe_tool_name(tool),
            correlation_id=correlation_id,
            payload=payload,
        )
    except Exception:
        # Telemetry must not change a tool's result, failure or permissions.
        pass


def automatic_conversation_summary(
    db_path: str | Path, *, runtime_started_at: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Observe active sessions, plus a one-minute recently-inactive grace period.

    A session is active for 60 seconds after its latest PLA tool activity,
    recently stopped for the next 60, then disappears. This observes MCP
    activity, not whether a browser tab was closed or ChatGPT is still busy.
    """
    empty = {
        "available": True, "method": "openai_session",
        "identified_count": 0, "conversations": [],
        "recently_stopped_count": 0, "recently_stopped": [],
        "unattributed_calls": 0, "observed_calls": 0,
        "truncated": False, "is_exact_window_count": False,
        "active_window_seconds": ACTIVE_WINDOW_SECONDS,
        "recently_stopped_window_seconds": RECENTLY_STOPPED_SECONDS,
    }
    if runtime_started_at is None:
        return empty
    path = Path(db_path)
    if not path.is_file():
        return empty
    try:
        started = datetime.fromisoformat(runtime_started_at)
        observed_at = datetime.now(timezone.utc) if now is None else now
        if started.tzinfo is None or observed_at.tzinfo is None:
            return {**empty, "available": False}
        observed_utc = observed_at.astimezone(timezone.utc)
        if started > observed_utc:
            return empty
        active_cutoff = max(started.astimezone(timezone.utc),
                            observed_utc - timedelta(seconds=ACTIVE_WINDOW_SECONDS))
        cutoff = max(started.astimezone(timezone.utc),
                     observed_utc - timedelta(seconds=ACTIVE_WINDOW_SECONDS + RECENTLY_STOPPED_SECONDS))
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute(
                """SELECT timestamp, event_type, subject, correlation_id, payload
                   FROM runtime_events
                   WHERE source = 'conversation_transport'
                     AND event_type IN (?, ?, ?)
                     AND timestamp > ? AND timestamp <= ?
                   ORDER BY sequence DESC LIMIT ?""",
                (*_AUTO_EVENT_TYPES, cutoff.isoformat(),
                 observed_utc.isoformat(), _MAX_EVENTS + 1),
            ).fetchall()
        observed_ids: set[str] = set()
        unknown_ids: set[str] = set()
        latest: dict[str, dict[str, Any]] = {}
        for timestamp, event_type, subject, call_id, raw_payload in rows[:_MAX_EVENTS]:
            try:
                import json
                payload = json.loads(raw_payload)
                fingerprint = payload.get("fingerprint")
            except (ValueError, TypeError, AttributeError):
                fingerprint = None
                payload = {}
            if not isinstance(call_id, str):
                continue
            # Preserve the existing 60-second semantics for live call totals.
            if timestamp > active_cutoff.isoformat():
                observed_ids.add(call_id)
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                if timestamp > active_cutoff.isoformat():
                    unknown_ids.add(call_id)
                continue
            number = payload.get("conversation_number")
            if type(number) is not int or number < 1:
                number = None
            if fingerprint not in latest:
                latest[fingerprint] = {
                    "anonymous_id": fingerprint[:10],
                    "conversation_number": number,
                    "last_seen_at": timestamp,
                    "last_tool": safe_tool_name(subject),
                    "calls": set(),
                }
            elif latest[fingerprint]["conversation_number"] is None and number is not None:
                latest[fingerprint]["conversation_number"] = number
            latest[fingerprint]["calls"].add(call_id)
        active = [
            item for item in latest.values()
            if item["last_seen_at"] > active_cutoff.isoformat()
        ]
        recently_stopped = [
            item for item in latest.values()
            if item["last_seen_at"] <= active_cutoff.isoformat()
        ]
        def public(item: dict[str, Any]) -> dict[str, Any]:
            return {**{k: v for k, v in item.items() if k != "calls"},
                    "tool_calls": len(item["calls"])}
        return {
            **empty,
            "identified_count": len(active),
            "recently_stopped_count": len(recently_stopped),
            "unattributed_calls": len(unknown_ids),
            "observed_calls": len(observed_ids),
            "conversations": [public(item) for item in active[:20]],
            "recently_stopped": [public(item) for item in recently_stopped[:20]],
            "truncated": len(rows) > _MAX_EVENTS,
        }
    except (sqlite3.DatabaseError, OSError, ValueError):
        return {**empty, "available": False}
