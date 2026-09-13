"""Runtime Lifecycle control plane for PLA v1.2 Phase 5."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "lifecycle"
BROKER_STATUS = STATE_DIR / "broker_status.json"
SCHEMA_VERSION = 1
RESTART_GRACE_SECONDS = 3.0
BROKER_STALE_SECONDS = 5.0


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _request_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(ch not in "0123456789abcdef" for ch in value.casefold())
    ):
        raise ValueError("request_id must be a 32-character hexadecimal string")
    return value.casefold()


def _tcp_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _broker_snapshot() -> dict[str, Any]:
    status = _read_json(BROKER_STATUS)
    if status is None:
        return {
            "state": "absent",
            "ready": False,
            "stale": True,
            "alive": False,
            "pid": None,
            "updated_at": None,
            "current_request_id": None,
        }

    updated_at = status.get("updated_at")
    stale = True
    if isinstance(updated_at, str):
        try:
            age = (_now_dt() - datetime.fromisoformat(updated_at)).total_seconds()
            stale = age > BROKER_STALE_SECONDS
        except ValueError:
            stale = True

    pid = status.get("pid")
    alive = _pid_alive(pid) if isinstance(pid, int) else False
    ready = (
        status.get("state") == "running"
        and isinstance(pid, int)
        and pid > 0
        and alive
        and not stale
    )
    return {
        "state": status.get("state"),
        "ready": ready,
        "stale": stale,
        "alive": alive,
        "pid": pid if isinstance(pid, int) else None,
        "updated_at": updated_at,
        "current_request_id": status.get("current_request_id"),
    }


def lifecycle_status() -> dict[str, Any]:
    """Return bounded lifecycle status without process mutation."""
    return {
        "status": "ready",
        "http": {
            "pid": os.getpid(),
            "port": 8766,
            "listening": _tcp_listening(8766),
        },
        "tunnel": {
            "health_port": 18081,
            "listening": _tcp_listening(18081),
        },
        "broker": _broker_snapshot(),
    }


def request_http_restart() -> dict[str, Any]:
    """Queue one exact HTTP restart for the independent lifecycle broker."""
    broker = _broker_snapshot()
    if not broker["ready"]:
        raise RuntimeError(
            "Runtime Lifecycle Broker is not ready; start PLA with start_all.ps1"
        )

    request_id = uuid4().hex
    created = _now_dt()
    not_before = created + timedelta(seconds=RESTART_GRACE_SECONDS)
    request = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "kind": "restart_http",
        "created_at": created.isoformat(),
        "not_before": not_before.isoformat(),
        "expected_http_pid": os.getpid(),
        "requested_by": "runtime.restart_http",
    }
    status = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "kind": "restart_http",
        "state": "queued",
        "created_at": request["created_at"],
        "updated_at": request["created_at"],
        "expected_http_pid": os.getpid(),
        "not_before": request["not_before"],
    }

    # Status is written first; the request file is the broker's commit marker.
    _atomic_json_write(
        STATE_DIR / f"{request_id}.status.json",
        status,
    )
    _atomic_json_write(
        STATE_DIR / f"{request_id}.request.json",
        request,
    )
    return {
        "status": "accepted",
        "request_id": request_id,
        "state": "queued",
        "expected_http_pid": os.getpid(),
        "not_before": request["not_before"],
    }


def restart_request_status(request_id: str) -> dict[str, Any]:
    request_id = _request_id(request_id)
    status = _read_json(STATE_DIR / f"{request_id}.status.json")
    if status is None:
        return {
            "status": "absent",
            "request_id": request_id,
        }
    return status
