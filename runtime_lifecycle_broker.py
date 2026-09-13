"""Independent restart executor for the PLA Runtime Lifecycle Plane."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "lifecycle"
BROKER_STATUS = STATE_DIR / "broker_status.json"
RESTART_SCRIPT = PROJECT_ROOT / "restart_pla.ps1"
MUTEX_NAME = "Local\\PLARuntimeLifecycleBroker"
SCHEMA_VERSION = 1
HEARTBEAT_SECONDS = 1.0
POLL_SECONDS = 0.25
ERROR_ALREADY_EXISTS = 183
MAX_REQUEST_AGE_SECONDS = 120.0


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


def _validate_request(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Lifecycle request must be an object")
    allowed = {
        "schema_version",
        "request_id",
        "kind",
        "created_at",
        "not_before",
        "expected_http_pid",
        "requested_by",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "Unknown lifecycle request fields: " + ", ".join(sorted(unknown))
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported lifecycle request schema_version")
    if value.get("kind") != "restart_http":
        raise ValueError("Unsupported lifecycle request kind")
    if value.get("requested_by") != "runtime.restart_http":
        raise ValueError("Invalid lifecycle request source")

    request_id = value.get("request_id")
    if (
        not isinstance(request_id, str)
        or len(request_id) != 32
        or any(ch not in "0123456789abcdef" for ch in request_id.casefold())
    ):
        raise ValueError("Invalid lifecycle request_id")
    request_id = request_id.casefold()

    expected_pid = value.get("expected_http_pid")
    if type(expected_pid) is not int or expected_pid <= 0:
        raise ValueError("expected_http_pid must be a positive integer")

    try:
        created_at = datetime.fromisoformat(value["created_at"])
        not_before = datetime.fromisoformat(value["not_before"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid lifecycle request timestamps") from exc
    now = _now_dt()
    age = (now - created_at).total_seconds()
    if age < -5 or age > MAX_REQUEST_AGE_SECONDS:
        raise ValueError("Lifecycle request is outside the accepted age window")
    if not_before < created_at:
        raise ValueError("not_before cannot precede created_at")

    return {
        **value,
        "request_id": request_id,
        "expected_http_pid": expected_pid,
        "_not_before_dt": not_before,
    }


def _powershell() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    path = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not path.is_file():
        raise RuntimeError("Windows PowerShell 5.1 is unavailable")
    return path


def _failure_metadata(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    return {
        "exception_type": type(exc).__name__,
        "message_sha256": hashlib.sha256(
            message.encode("utf-8", errors="replace")
        ).hexdigest(),
        "message_length": len(message),
    }


def _execute_restart(request: dict[str, Any], status_path: Path) -> None:
    not_before = request["_not_before_dt"]
    while _now_dt() < not_before:
        time.sleep(min(0.1, max(0.0, (not_before - _now_dt()).total_seconds())))

    running = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request["request_id"],
        "kind": "restart_http",
        "state": "running",
        "created_at": request["created_at"],
        "updated_at": _now(),
        "expected_http_pid": request["expected_http_pid"],
        "broker_pid": os.getpid(),
    }
    _atomic_json_write(status_path, running)

    completed = subprocess.run(
        [
            str(_powershell()),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RESTART_SCRIPT),
            "-ExpectedPid",
            str(request["expected_http_pid"]),
            "-Json",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=45,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        raise RuntimeError(
            f"restart_pla.ps1 failed with code {completed.returncode}; "
            f"stdout_sha256={hashlib.sha256(stdout.encode('utf-8', errors='replace')).hexdigest()}; "
            f"stderr_sha256={hashlib.sha256(stderr.encode('utf-8', errors='replace')).hexdigest()}"
        )
    try:
        result = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("restart_pla.ps1 did not return valid JSON") from exc
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise RuntimeError("restart_pla.ps1 returned an invalid completion result")

    _atomic_json_write(
        status_path,
        {
            **running,
            "state": "completed",
            "updated_at": _now(),
            "result": {
                "old_pid": result.get("old_pid"),
                "new_pid": result.get("new_pid"),
                "tunnel_pid": result.get("tunnel_pid"),
                "tunnel_listening": bool(result.get("tunnel_listening")),
            },
        },
    )


def _request_files() -> list[Path]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        STATE_DIR.glob("*.request.json"),
        key=lambda path: path.stat().st_mtime,
    )


def _process_one_request() -> bool:
    for request_path in _request_files():
        request_id = request_path.name[: -len(".request.json")]
        status_path = STATE_DIR / f"{request_id}.status.json"
        try:
            current_status = (
                json.loads(status_path.read_text(encoding="utf-8"))
                if status_path.is_file()
                else {}
            )
        except Exception:
            current_status = {}
        if current_status.get("state") != "queued":
            continue

        try:
            request = _validate_request(
                json.loads(request_path.read_text(encoding="utf-8"))
            )
            _broker_status("running", request_id)
            _execute_restart(request, status_path)
        except Exception as exc:
            _atomic_json_write(
                status_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "request_id": request_id,
                    "kind": "restart_http",
                    "state": "failed",
                    "updated_at": _now(),
                    "broker_pid": os.getpid(),
                    **_failure_metadata(exc),
                },
            )
        finally:
            try:
                request_path.unlink()
            except FileNotFoundError:
                pass
        return True
    return False


def _broker_status(state: str, current_request_id: str | None = None) -> None:
    _atomic_json_write(
        BROKER_STATUS,
        {
            "state": state,
            "pid": os.getpid(),
            "updated_at": _now(),
            "current_request_id": current_request_id,
        },
    )


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Runtime Lifecycle Broker requires Windows")
    if not RESTART_SCRIPT.is_file():
        raise RuntimeError(f"restart script is missing: {RESTART_SCRIPT}")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        return 10

    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    last_heartbeat = 0.0
    _broker_status("running")
    try:
        while not stop:
            now_monotonic = time.monotonic()
            if now_monotonic - last_heartbeat >= HEARTBEAT_SECONDS:
                _broker_status("running")
                last_heartbeat = now_monotonic
            if _process_one_request():
                _broker_status("running")
                last_heartbeat = time.monotonic()
                continue
            time.sleep(POLL_SECONDS)
    finally:
        _broker_status("stopped")
        kernel32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
