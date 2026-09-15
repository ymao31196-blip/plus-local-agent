"""Durable Human Takeover control plane for PLA v1.5.

The controller is intentionally independent from Browser/Computer providers.  It
uses the existing Gate Hook and Observer Hook extension points:

* active takeover: deny every capability on the scoped provider(s), including
  observations, so sensitive user input cannot be read by the model;
* resume requested: permit observation-only calls, but keep control calls
  blocked;
* successful trusted re-observation: release that provider back to the agent.

State is persisted atomically so a PLA HTTP restart cannot silently hand control
back to automation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = PROJECT_ROOT / "state" / "human_takeover.json"
SCHEMA_VERSION = 1
TARGET_PROVIDERS = frozenset({"browser", "computer"})
RESYNC_COMPLETERS: dict[str, frozenset[str]] = {
    "browser": frozenset({"browser.inspect", "browser.screenshot"}),
    "computer": frozenset({"computer.inspect", "computer.screenshot"}),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reason_digest(reason: str) -> dict[str, Any]:
    encoded = reason.encode("utf-8", errors="replace")
    return {
        "reason_sha256": hashlib.sha256(encoded).hexdigest(),
        "reason_length": len(reason),
    }


class HumanTakeoverController:
    """Persisted ownership state for Browser/Computer human handoff."""

    def __init__(
        self,
        state_path: str | Path = DEFAULT_STATE_PATH,
        event_store: Any = None,
        on_state_change: Callable[[], None] | None = None,
    ):
        self._state_path = Path(state_path)
        self._event_store = event_store
        self._on_state_change = on_state_change
        self._lock = RLock()

    def _notify_state_change(self) -> None:
        callback = self._on_state_change
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # Visibility is defense-in-depth. A UI failure must not weaken or
            # roll back the persisted ownership boundary.
            pass

    def restore_visibility(self) -> bool:
        """Restore the local ownership overlay after a runtime restart."""
        if not self._state_path.is_file():
            return False
        self._notify_state_change()
        return True

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state": "agent",
            "revision": 0,
            "takeover_id": None,
            "provider_ids": [],
            "pending_resync_provider_ids": [],
            "reason": None,
            "started_at": None,
            "resume_requested_at": None,
            "completed_at": None,
        }

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.is_file():
            return self._default_state()
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("Human takeover state is unreadable") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Human takeover state must be an object")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("Unsupported human takeover state schema_version")
        if value.get("state") not in {"agent", "human", "resync_required"}:
            raise RuntimeError("Human takeover state is invalid")
        revision = value.get("revision")
        if type(revision) is not int or revision < 0:
            raise RuntimeError("Human takeover state revision is invalid")
        for field in ("provider_ids", "pending_resync_provider_ids"):
            providers = value.get(field)
            if not isinstance(providers, list) or any(
                provider not in TARGET_PROVIDERS for provider in providers
            ):
                raise RuntimeError(f"Human takeover {field} is invalid")
        return value

    def _write_state(self, value: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self._state_path)

    @staticmethod
    def _normalize_providers(provider_ids: list[str] | None) -> list[str]:
        providers = sorted(TARGET_PROVIDERS if provider_ids is None else provider_ids)
        if not providers:
            raise ValueError("provider_ids must contain at least one provider")
        if len(providers) != len(set(providers)):
            raise ValueError("provider_ids must not contain duplicates")
        unknown = sorted(set(providers) - TARGET_PROVIDERS)
        if unknown:
            raise ValueError(
                "Human takeover only supports providers: browser, computer"
            )
        return providers

    def _emit(self, event_type: str, state: dict[str, Any], payload: dict[str, Any]) -> None:
        if self._event_store is None:
            return
        try:
            self._event_store.emit(
                event_type,
                source="human_takeover",
                subject=str(state.get("takeover_id") or "human-takeover"),
                provider_id=None,
                payload=payload,
            )
        except Exception:
            # Persistence of takeover ownership is the source of truth. Event-plane
            # failures must not weaken the control boundary.
            pass

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            return {
                **state,
                "active": state["state"] != "agent",
                "control_owner": "human" if state["state"] == "human" else "agent",
                "automation_blocked": state["state"] in {"human", "resync_required"},
            }


    @staticmethod
    def _validate_reason(reason: str) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        reason = reason.strip()
        if len(reason) > 1000 or "\x00" in reason:
            raise ValueError(
                "reason must be at most 1000 characters and contain no NUL"
            )
        return reason

    def begin(
        self,
        reason: str,
        provider_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        reason = self._validate_reason(reason)
        providers = self._normalize_providers(provider_ids)

        with self._lock:
            current = self._read_state()
            if current["state"] != "agent":
                raise RuntimeError(
                    "Human takeover is already active; inspect status before starting another"
                )
            state = {
                "schema_version": SCHEMA_VERSION,
                "state": "human",
                "revision": current["revision"] + 1,
                "takeover_id": uuid4().hex,
                "provider_ids": providers,
                "pending_resync_provider_ids": [],
                "reason": reason,
                "started_at": _now(),
                "resume_requested_at": None,
                "completed_at": None,
            }
            self._write_state(state)
            self._notify_state_change()
            self._emit(
                "human_takeover.started",
                state,
                {
                    "revision": state["revision"],
                    "provider_ids": providers,
                    **_reason_digest(reason),
                },
            )
            return self.status()

    def intervene(
        self,
        reason: str,
        provider_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Enter or strengthen human ownership after detected physical input.

        This internal path is idempotent across an existing human handoff. It can
        expand a partial takeover to additional interactive providers, and physical
        input during resynchronization returns ownership to the human immediately.
        """

        reason = self._validate_reason(reason)
        providers = self._normalize_providers(provider_ids)

        with self._lock:
            current = self._read_state()
            if current["state"] == "agent":
                state = {
                    "schema_version": SCHEMA_VERSION,
                    "state": "human",
                    "revision": current["revision"] + 1,
                    "takeover_id": uuid4().hex,
                    "provider_ids": providers,
                    "pending_resync_provider_ids": [],
                    "reason": reason,
                    "started_at": _now(),
                    "resume_requested_at": None,
                    "completed_at": None,
                }
                event_type = "human_takeover.started"
                added = list(providers)
            else:
                combined = sorted(set(current["provider_ids"]) | set(providers))
                added = sorted(set(combined) - set(current["provider_ids"]))
                if current["state"] == "human" and not added:
                    return self.status()
                state = {
                    **current,
                    "state": "human",
                    "revision": current["revision"] + 1,
                    "provider_ids": combined,
                    "pending_resync_provider_ids": [],
                    "resume_requested_at": None,
                    "completed_at": None,
                }
                event_type = (
                    "human_takeover.reintervened"
                    if current["state"] == "resync_required"
                    else "human_takeover.expanded"
                )

            self._write_state(state)
            self._notify_state_change()
            self._emit(
                event_type,
                state,
                {
                    "revision": state["revision"],
                    "provider_ids": list(state["provider_ids"]),
                    "added_provider_ids": added,
                    **_reason_digest(reason),
                },
            )
            return self.status()

    def resume(
        self,
        takeover_id: str,
        expected_revision: int,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != "RESUME":
            raise PermissionError(
                "Human takeover resume requires confirmation='RESUME' after the human is done"
            )
        if not isinstance(takeover_id, str) or not takeover_id:
            raise ValueError("takeover_id must be a non-empty string")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")

        with self._lock:
            current = self._read_state()
            if current["state"] != "human":
                raise RuntimeError("Human takeover is not waiting for human control")
            if current.get("takeover_id") != takeover_id:
                raise ValueError("takeover_id does not match the active human takeover")
            if current["revision"] != expected_revision:
                raise ValueError(
                    "Human takeover revision changed; refresh status before resuming"
                )

            state = {
                **current,
                "state": "resync_required",
                "revision": current["revision"] + 1,
                "pending_resync_provider_ids": list(current["provider_ids"]),
                "resume_requested_at": _now(),
            }
            self._write_state(state)
            self._notify_state_change()
            self._emit(
                "human_takeover.resume_requested",
                state,
                {
                    "revision": state["revision"],
                    "provider_ids": list(state["provider_ids"]),
                },
            )
            return self.status()

    def gate(self, context: dict[str, Any]) -> dict[str, str]:
        """Gate Hook handler enforcing current ownership."""

        provider_id = context.get("provider_id")
        if provider_id not in TARGET_PROVIDERS:
            return {"decision": "allow", "reason_code": "takeover_not_scoped"}

        try:
            with self._lock:
                state = self._read_state()
        except Exception:
            return {
                "decision": "deny",
                "reason_code": "human_takeover_state_unreadable",
            }

        if provider_id not in state["provider_ids"]:
            return {"decision": "allow", "reason_code": "takeover_provider_not_scoped"}

        if state["state"] == "human":
            return {
                "decision": "deny",
                "reason_code": "human_takeover_active",
            }

        if state["state"] == "resync_required":
            if provider_id not in state["pending_resync_provider_ids"]:
                return {
                    "decision": "allow",
                    "reason_code": "human_takeover_provider_resynced",
                }
            tags = set(context.get("tags") or [])
            if (
                context.get("risk_level") == "read"
                and "observation" in tags
            ):
                return {
                    "decision": "allow",
                    "reason_code": "human_takeover_resync_observation_allowed",
                }
            return {
                "decision": "deny",
                "reason_code": "human_takeover_resync_required",
            }

        return {"decision": "allow", "reason_code": "human_takeover_inactive"}

    def observer(self, event: dict[str, Any]) -> dict[str, Any]:
        """Observer Hook handler releasing a provider after trusted re-observation."""

        if event.get("event_type") != "capability.succeeded":
            return {"status": "ignored", "reason": "event_type"}

        provider_id = event.get("provider_id")
        capability_id = event.get("capability_id")
        if provider_id not in TARGET_PROVIDERS:
            return {"status": "ignored", "reason": "provider"}
        if capability_id not in RESYNC_COMPLETERS.get(provider_id, frozenset()):
            return {"status": "ignored", "reason": "not_resync_completer"}

        with self._lock:
            state = self._read_state()
            if state["state"] != "resync_required":
                return {"status": "ignored", "reason": "not_resyncing"}
            pending = list(state["pending_resync_provider_ids"])
            if provider_id not in pending:
                return {"status": "ignored", "reason": "provider_not_pending"}

            pending.remove(provider_id)
            completed = not pending
            next_state = {
                **state,
                "state": "agent" if completed else "resync_required",
                "revision": state["revision"] + 1,
                "pending_resync_provider_ids": pending,
                "completed_at": _now() if completed else state.get("completed_at"),
            }
            self._write_state(next_state)
            self._notify_state_change()
            self._emit(
                "human_takeover.provider_resynced",
                next_state,
                {
                    "revision": next_state["revision"],
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "remaining_provider_ids": pending,
                },
            )
            if completed:
                self._emit(
                    "human_takeover.completed",
                    next_state,
                    {
                        "revision": next_state["revision"],
                        "provider_ids": list(next_state["provider_ids"]),
                    },
                )
            return {
                "status": "completed" if completed else "resyncing",
                "provider_id": provider_id,
                "remaining_provider_ids": pending,
                "state": next_state["state"],
                "revision": next_state["revision"],
            }
