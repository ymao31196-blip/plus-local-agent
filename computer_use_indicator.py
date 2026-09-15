"""User-visible Windows indicator for PLA Computer Use activity.

This is a first-party Observer-side UI.  It never participates in capability
selection or Computer Provider semantics; it only renders bounded Event Plane
facts so a local user can see when PLA is observing or controlling the desktop.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state"
STATE_PATH = STATE_DIR / "computer_use_indicator.json"
TAKEOVER_STATE_PATH = STATE_DIR / "human_takeover.json"
TAKEOVER_NOTICE_PATH = STATE_DIR / "human_takeover_notice.json"

_INDICATOR_PROCESS: subprocess.Popen[bytes] | None = None
_INDICATOR_LOCK = RLock()
_STALE_SECONDS = 120.0
_LINGER_SECONDS = 12.0
_TAKEOVER_NOTICE_SECONDS = 15.0
_POLL_MS = 200
_MUTEX_NAME = "Local\\PLAComputerUseIndicator_v1"
_WINDOW_TITLE = "__PLA_INTERNAL_COMPUTER_USE_INDICATOR__"


def _enabled() -> bool:
    raw = os.environ.get("PLA_COMPUTER_USE_INDICATOR", "1").strip().casefold()
    return raw not in {"0", "false", "off", "no"}


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_takeover_state() -> dict[str, Any]:
    try:
        raw = TAKEOVER_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {"state": "error"}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": "error"}
    return value if isinstance(value, dict) else {"state": "error"}


def _takeover_overlay_text(state: dict[str, Any]) -> str | None:
    takeover_state = state.get("state")
    if takeover_state == "human":
        return "PLA Human Takeover · 人工接管中，AI已暂停"
    if takeover_state == "resync_required":
        return "PLA Human Takeover · 正在重新同步，AI控制仍暂停"
    if takeover_state == "error":
        return "PLA Human Takeover · 状态异常，AI交互已锁定"
    return None


def _write_takeover_notice(now: float | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.time() if now is None else float(now)
    temp = TAKEOVER_NOTICE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {"schema_version": 1, "updated_at": timestamp},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temp, TAKEOVER_NOTICE_PATH)


def _takeover_notice_fresh(now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    try:
        value = json.loads(TAKEOVER_NOTICE_PATH.read_text(encoding="utf-8"))
        updated_at = float(value.get("updated_at", 0.0))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return False
    return updated_at > 0 and 0 <= current - updated_at <= _TAKEOVER_NOTICE_SECONDS


def _write_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temp, STATE_PATH)


def _event_mode(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    risk = payload.get("risk_level") if isinstance(payload, dict) else None
    return "observe" if risk == "read" else "control"


def _normalize_inflight(value: Any, now: float, updated_at: float) -> dict[str, dict[str, str]]:
    if now - updated_at > _STALE_SECONDS or not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, dict):
            continue
        result[key[:128]] = {
            "mode": "control" if item.get("mode") == "control" else "observe",
            "capability_id": str(item.get("capability_id") or "")[:256],
        }
    return result


def _ensure_indicator_process() -> None:
    global _INDICATOR_PROCESS
    if os.name != "nt" or not _enabled():
        return
    with _INDICATOR_LOCK:
        if _INDICATOR_PROCESS is not None and _INDICATOR_PROCESS.poll() is None:
            return
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _INDICATOR_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "computer_use_indicator", "--overlay"],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )


def ensure_computer_use_indicator_process() -> None:
    """Refresh takeover notice lifetime and ensure the shared overlay is running."""
    try:
        _write_takeover_notice()
    except Exception:
        pass
    _ensure_indicator_process()


def computer_use_indicator_observer(event: dict[str, Any]) -> dict[str, Any]:
    """Update the visible indicator from persisted Computer Provider events."""

    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    if event.get("provider_id") != "computer":
        return {"status": "ignored", "reason": "not_computer_provider"}
    if not _enabled() or os.name != "nt":
        return {"status": "disabled"}

    event_type = str(event.get("event_type") or "")
    if event_type not in {
        "capability.before_invoke",
        "capability.succeeded",
        "capability.failed",
    }:
        return {"status": "ignored", "reason": "event_type"}

    now = time.time()
    correlation_id = str(event.get("correlation_id") or "")[:128]
    capability_id = str(event.get("capability_id") or "")[:256]

    with _INDICATOR_LOCK:
        state = _read_state()
        try:
            updated_at = float(state.get("updated_at", 0.0))
        except (TypeError, ValueError):
            updated_at = 0.0
        inflight = _normalize_inflight(state.get("inflight"), now, updated_at)

        if event_type == "capability.before_invoke":
            if correlation_id:
                inflight[correlation_id] = {
                    "mode": _event_mode(event),
                    "capability_id": capability_id,
                }
            mode = (
                "control"
                if any(item.get("mode") == "control" for item in inflight.values())
                else "observe"
            )
            linger_until = 0.0
        else:
            if correlation_id:
                inflight.pop(correlation_id, None)
            if inflight:
                mode = (
                    "control"
                    if any(item.get("mode") == "control" for item in inflight.values())
                    else "observe"
                )
                linger_until = 0.0
            else:
                mode = "control" if state.get("mode") == "control" else "observe"
                linger_until = now + _LINGER_SECONDS

        new_state = {
            "schema_version": 1,
            "updated_at": now,
            "mode": mode,
            "capability_id": capability_id,
            "inflight": inflight,
            "linger_until": linger_until,
        }
        _write_state(new_state)
        _ensure_indicator_process()

    return {
        "status": "updated",
        "mode": new_state["mode"],
        "active_count": len(inflight),
    }


def _acquire_single_instance_mutex() -> Any:
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if not handle:
        return None
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def _apply_nonactivating_clickthrough_style(root: Any) -> None:
    import ctypes

    root.update_idletasks()
    hwnd = root.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
    )


def _run_overlay() -> int:
    if os.name != "nt" or not _enabled():
        return 0

    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        return 0

    import ctypes
    import tkinter as tk

    root = tk.Tk()
    root.title(_WINDOW_TITLE)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.94)
    except tk.TclError:
        pass

    width = 430
    height = 36
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    root.geometry(f"{width}x{height}+{x}+10")
    root.configure(bg="#171717")

    label = tk.Label(
        root,
        text="PLA Computer Use · AI正在控制此电脑",
        bg="#171717",
        fg="#ffffff",
        font=("Segoe UI", 10, "bold"),
        padx=12,
        pady=7,
    )
    label.pack(fill="both", expand=True)

    _apply_nonactivating_clickthrough_style(root)
    root.withdraw()
    last_visible_at = time.time()

    def refresh() -> None:
        nonlocal last_visible_at
        now = time.time()
        state = _read_state()
        try:
            updated_at = float(state.get("updated_at", 0.0))
            linger_until = float(state.get("linger_until", 0.0))
        except (TypeError, ValueError):
            updated_at = 0.0
            linger_until = 0.0

        inflight = state.get("inflight")
        active_count = len(inflight) if isinstance(inflight, dict) else 0
        fresh = updated_at > 0 and now - updated_at <= _STALE_SECONDS
        visible = fresh and (active_count > 0 or now <= linger_until)

        takeover = _read_takeover_state()
        takeover_text = _takeover_overlay_text(takeover)
        takeover_visible = (
            takeover_text is not None
            and (
                takeover.get("state") == "error"
                or _takeover_notice_fresh(now)
            )
        )

        if takeover_visible:
            label.configure(text=takeover_text)
            root.deiconify()
            root.lift()
            last_visible_at = now
        elif visible:
            mode = "control" if state.get("mode") == "control" else "observe"
            if mode == "control":
                label.configure(text="PLA Computer Use · AI正在控制此电脑")
            else:
                label.configure(text="PLA Computer Use · AI正在查看桌面")
            root.deiconify()
            root.lift()
            last_visible_at = now
        else:
            root.withdraw()

        if not takeover_visible and now - max(last_visible_at, updated_at) > _STALE_SECONDS:
            root.destroy()
            return
        root.after(_POLL_MS, refresh)

    try:
        refresh()
        root.mainloop()
    finally:
        ctypes.windll.kernel32.ReleaseMutex(mutex)
        ctypes.windll.kernel32.CloseHandle(mutex)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay", action="store_true")
    args = parser.parse_args(argv)
    if args.overlay:
        return _run_overlay()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
