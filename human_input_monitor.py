"""Automatic physical-user takeover detection for PLA interactive automation.

The monitor is intentionally separate from Browser/Computer providers. It observes
only provider lifecycle metadata from the Event Plane and listens for local
Windows low-level keyboard/mouse events. Injected input is ignored so PLA's own
automation does not trigger Human Takeover.

No key codes, typed text, coordinates, or raw input payloads are persisted.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from threading import Event, RLock, Thread
import time
from typing import Any


TARGET_PROVIDERS = frozenset({"browser", "computer"})
INTERACTIVE_EVENTS = frozenset({
    "capability.before_invoke",
    "capability.succeeded",
    "capability.failed",
})

_LL_KBD_INJECTED_MASK = 0x10 | 0x02
_LL_MOUSE_INJECTED_MASK = 0x01 | 0x02

_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_RBUTTONDOWN = 0x0204
_WM_MBUTTONDOWN = 0x0207
_WM_XBUTTONDOWN = 0x020B
_WM_MOUSEWHEEL = 0x020A
_WM_MOUSEHWHEEL = 0x020E
_WM_QUIT = 0x0012

_MOUSE_IMMEDIATE = frozenset({
    _WM_LBUTTONDOWN,
    _WM_RBUTTONDOWN,
    _WM_MBUTTONDOWN,
    _WM_XBUTTONDOWN,
    _WM_MOUSEWHEEL,
    _WM_MOUSEHWHEEL,
})


class HumanInputMonitor:
    """Arm on interactive automation and convert real user input into takeover."""

    def __init__(
        self,
        takeover_controller: Any,
        *,
        linger_seconds: float = 8.0,
        mouse_move_threshold: int = 12,
        enabled: bool | None = None,
    ) -> None:
        if linger_seconds < 0:
            raise ValueError("linger_seconds must be non-negative")
        if mouse_move_threshold < 1:
            raise ValueError("mouse_move_threshold must be positive")
        self._takeover = takeover_controller
        self._linger_seconds = float(linger_seconds)
        self._mouse_move_threshold = int(mouse_move_threshold)
        self._enabled = self._resolve_enabled() if enabled is None else bool(enabled)

        self._lock = RLock()
        self._inflight: dict[str, str] = {}
        self._armed_until = 0.0
        self._mouse_anchor: tuple[int, int] | None = None
        self._last_trigger_kind: str | None = None
        self._last_trigger_at = 0.0

        self._takeover_requested = Event()
        self._stop_requested = Event()
        self._ready = Event()
        self._worker_thread: Thread | None = None
        self._hook_thread: Thread | None = None
        self._hook_thread_id: int | None = None
        self._hook_installed = False
        self._hook_error: str | None = None

        # Keep callback references alive for the lifetime of installed hooks.
        self._keyboard_callback = None
        self._mouse_callback = None

    @staticmethod
    def _resolve_enabled() -> bool:
        raw = os.environ.get("PLA_AUTO_HUMAN_TAKEOVER", "1").strip().casefold()
        return raw not in {"0", "false", "off", "no"}

    def observer(self, event: dict[str, Any]) -> dict[str, Any]:
        """Observer Hook: arm while Browser/Computer capabilities are in flight."""

        if not isinstance(event, dict):
            raise TypeError("event must be an object")
        provider_id = event.get("provider_id")
        event_type = event.get("event_type")
        if provider_id not in TARGET_PROVIDERS:
            return {"status": "ignored", "reason": "provider"}
        if event_type not in INTERACTIVE_EVENTS:
            return {"status": "ignored", "reason": "event_type"}

        correlation_id = str(event.get("correlation_id") or "")[:128]
        now = time.monotonic()
        with self._lock:
            if event_type == "capability.before_invoke":
                if correlation_id:
                    self._inflight[correlation_id] = str(provider_id)
                self._armed_until = max(
                    self._armed_until, now + self._linger_seconds
                )
            else:
                if correlation_id:
                    self._inflight.pop(correlation_id, None)
                self._armed_until = max(
                    self._armed_until, now + self._linger_seconds
                )
            self._mouse_anchor = None
            armed = self._is_armed_locked(now)
            active_count = len(self._inflight)

        return {
            "status": "armed" if armed else "idle",
            "active_count": active_count,
        }

    def _is_armed_locked(self, now: float) -> bool:
        return bool(self._inflight) or now <= self._armed_until

    def is_armed(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            return self._is_armed_locked(current)

    def note_input(
        self,
        kind: str,
        *,
        injected: bool,
        x: int | None = None,
        y: int | None = None,
        now: float | None = None,
    ) -> bool:
        """Record a classified input event without retaining its raw payload."""

        if injected or not self._enabled:
            return False
        current = time.monotonic() if now is None else float(now)

        with self._lock:
            if not self._is_armed_locked(current):
                self._mouse_anchor = None
                return False

            qualifies = False
            if kind in {"keyboard", "mouse_button", "mouse_wheel"}:
                qualifies = True
            elif kind == "mouse_move":
                if x is None or y is None:
                    return False
                point = (int(x), int(y))
                if self._mouse_anchor is None:
                    self._mouse_anchor = point
                    return False
                dx = point[0] - self._mouse_anchor[0]
                dy = point[1] - self._mouse_anchor[1]
                qualifies = (
                    dx * dx + dy * dy
                    >= self._mouse_move_threshold * self._mouse_move_threshold
                )
                if not qualifies:
                    return False
            else:
                raise ValueError(f"Unsupported input kind: {kind}")

            if qualifies:
                self._mouse_anchor = None
                self._last_trigger_kind = kind
                self._last_trigger_at = current
                self._takeover_requested.set()
                return True
        return False

    def _perform_requested_takeover(self) -> bool:
        """Perform one pending transition outside the low-level hook callback."""

        if not self._takeover_requested.is_set():
            return False
        self._takeover_requested.clear()

        try:
            self._takeover.intervene(
                "Physical user input detected during interactive automation",
                ["browser", "computer"],
            )
            return True
        except Exception:
            # Detection is defense-in-depth. Hook/transition failures must not
            # corrupt the existing Human Takeover state machine.
            return False

    def _worker_loop(self) -> None:
        while not self._stop_requested.is_set():
            if not self._takeover_requested.wait(0.2):
                continue
            if self._stop_requested.is_set():
                break
            self._perform_requested_takeover()

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {
                "enabled": self._enabled,
                "platform_supported": os.name == "nt",
                "running": bool(
                    self._hook_thread is not None and self._hook_thread.is_alive()
                ),
                "hook_installed": self._hook_installed,
                "hook_error": self._hook_error,
                "armed": self._is_armed_locked(now),
                "active_count": len(self._inflight),
                "linger_seconds": self._linger_seconds,
                "mouse_move_threshold": self._mouse_move_threshold,
                "last_trigger_kind": self._last_trigger_kind,
                "last_trigger_at_monotonic": (
                    self._last_trigger_at if self._last_trigger_at > 0 else None
                ),
            }

    def start(self) -> dict[str, Any]:
        if not self._enabled or os.name != "nt":
            return self.status()
        with self._lock:
            if self._hook_thread is not None and self._hook_thread.is_alive():
                return self.status()
            self._stop_requested.clear()
            self._ready.clear()
            self._worker_thread = Thread(
                target=self._worker_loop,
                name="pla-human-input-worker",
                daemon=True,
            )
            self._hook_thread = Thread(
                target=self._windows_hook_loop,
                name="pla-human-input-hook",
                daemon=True,
            )
            self._worker_thread.start()
            self._hook_thread.start()
        self._ready.wait(2.0)
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop_requested.set()
        self._takeover_requested.set()
        thread_id = self._hook_thread_id
        if os.name == "nt" and thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    wintypes.DWORD(thread_id),
                    wintypes.UINT(_WM_QUIT),
                    wintypes.WPARAM(0),
                    wintypes.LPARAM(0),
                )
            except Exception:
                pass
        for thread in (self._hook_thread, self._worker_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        with self._lock:
            self._hook_installed = False
            self._hook_thread_id = None
        return self.status()

    def _windows_hook_loop(self) -> None:
        """Install global low-level hooks on a dedicated Windows message thread."""

        if os.name != "nt":
            self._ready.set()
            return

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        hook_proc_type = ctypes.WINFUNCTYPE(
            wintypes.LPARAM,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_proc_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        def keyboard_proc(n_code, w_param, l_param):
            if n_code >= 0 and int(w_param) in {_WM_KEYDOWN, _WM_SYSKEYDOWN}:
                data = ctypes.cast(
                    l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                self.note_input(
                    "keyboard",
                    injected=bool(data.flags & _LL_KBD_INJECTED_MASK),
                )
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def mouse_proc(n_code, w_param, l_param):
            if n_code >= 0:
                message = int(w_param)
                data = ctypes.cast(
                    l_param, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                injected = bool(data.flags & _LL_MOUSE_INJECTED_MASK)
                if message == _WM_MOUSEMOVE:
                    self.note_input(
                        "mouse_move",
                        injected=injected,
                        x=int(data.pt.x),
                        y=int(data.pt.y),
                    )
                elif message in _MOUSE_IMMEDIATE:
                    kind = (
                        "mouse_wheel"
                        if message in {_WM_MOUSEWHEEL, _WM_MOUSEHWHEEL}
                        else "mouse_button"
                    )
                    self.note_input(kind, injected=injected)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._keyboard_callback = hook_proc_type(keyboard_proc)
        self._mouse_callback = hook_proc_type(mouse_proc)
        self._hook_thread_id = int(kernel32.GetCurrentThreadId())

        keyboard_hook = user32.SetWindowsHookExW(
            13, self._keyboard_callback, None, 0
        )
        mouse_hook = user32.SetWindowsHookExW(
            14, self._mouse_callback, None, 0
        )

        if not keyboard_hook or not mouse_hook:
            error = ctypes.get_last_error()
            if keyboard_hook:
                user32.UnhookWindowsHookEx(keyboard_hook)
            if mouse_hook:
                user32.UnhookWindowsHookEx(mouse_hook)
            with self._lock:
                self._hook_error = f"SetWindowsHookExW failed: {error}"
                self._hook_installed = False
            self._ready.set()
            return

        with self._lock:
            self._hook_error = None
            self._hook_installed = True
        self._ready.set()

        message = wintypes.MSG()
        try:
            while not self._stop_requested.is_set():
                result = user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0
                )
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(keyboard_hook)
            user32.UnhookWindowsHookEx(mouse_hook)
            with self._lock:
                self._hook_installed = False
                self._hook_thread_id = None
