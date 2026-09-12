from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import re
import signal
import subprocess
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "elevation"
BROKER_STATUS = STATE_DIR / "broker_status.json"
MUTEX_NAME = "Local\\PLAInteractiveElevationBroker"
HEARTBEAT_SECONDS = 1.0
POLL_SECONDS = 0.25
ERROR_ALREADY_EXISTS = 183


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _session_id() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        return None
    return int(session.value)


def _user_object_name(handle: int | None) -> str | None:
    if not handle:
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    UOI_NAME = 2
    needed = wintypes.DWORD()
    user32.GetUserObjectInformationW(handle, UOI_NAME, None, 0, ctypes.byref(needed))
    if needed.value <= 2:
        return None
    buf = ctypes.create_unicode_buffer(needed.value // 2 + 1)
    if not user32.GetUserObjectInformationW(
        handle,
        UOI_NAME,
        buf,
        ctypes.sizeof(buf),
        ctypes.byref(needed),
    ):
        return None
    return buf.value


def _desktop_context() -> dict[str, Any]:
    if os.name != "nt":
        return {"session_id": None, "window_station": None, "desktop": None}

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    return {
        "session_id": _session_id(),
        "window_station": _user_object_name(user32.GetProcessWindowStation()),
        "desktop": _user_object_name(
            user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        ),
    }


def _validate_request(value: dict[str, Any]) -> dict[str, Any]:
    kind = value.get("kind")
    if kind not in {"registered_uninstaller", "winget_install"}:
        raise ValueError("Unsupported elevation request kind")

    launch_id = value.get("launch_id")
    if (
        not isinstance(launch_id, str)
        or len(launch_id) != 32
        or not launch_id.isalnum()
    ):
        raise ValueError("Invalid launch_id")

    executable = value.get("executable")
    if not isinstance(executable, str) or not executable:
        raise ValueError("Executable must be a non-empty string")
    executable_path = Path(executable)
    if not executable_path.is_absolute() or not executable_path.is_file():
        raise ValueError("Executable must be an existing absolute file")

    args = value.get("args", [])
    if not isinstance(args, list) or len(args) > 32:
        raise ValueError("args must be a bounded list")
    clean_args: list[str] = []
    for index, arg in enumerate(args):
        if (
            not isinstance(arg, str)
            or not arg
            or "\x00" in arg
            or len(arg) > 512
        ):
            raise ValueError(f"args[{index}] is invalid")
        clean_args.append(arg)

    timeout_seconds = value.get("timeout_seconds", 300)
    if (
        type(timeout_seconds) is not int
        or not 30 <= timeout_seconds <= 900
    ):
        raise ValueError("timeout_seconds must be between 30 and 900")

    if kind == "winget_install":
        if executable_path.name.casefold() != "winget.exe":
            raise ValueError("winget_install must execute winget.exe")

        package_id = value.get("package_id")
        source = value.get("source")
        target_directory = value.get("target_directory")
        silent = value.get("silent", True)

        token_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
        if (
            not isinstance(package_id, str)
            or not token_pattern.fullmatch(package_id)
        ):
            raise ValueError("Invalid winget package_id")
        if (
            not isinstance(source, str)
            or not token_pattern.fullmatch(source)
        ):
            raise ValueError("Invalid winget source")
        if (
            not isinstance(target_directory, str)
            or not PureWindowsPath(target_directory).is_absolute()
        ):
            raise ValueError("winget target_directory must be an absolute Windows path")
        if type(silent) is not bool:
            raise ValueError("silent must be a boolean")

        expected_args = [
            "install",
            "--id",
            package_id,
            "--exact",
            "--source",
            source,
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
            "--location",
            target_directory,
        ]
        if silent:
            expected_args.append("--silent")
        if clean_args != expected_args:
            raise ValueError("winget_install argv does not match the reviewed command shape")

    return {
        **value,
        "launch_id": launch_id,
        "executable": str(executable_path.resolve()),
        "args": clean_args,
        "timeout_seconds": timeout_seconds,
    }


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _execute_elevated(request: dict[str, Any], status_path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Interactive elevation broker requires Windows")

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    SW_SHOWNORMAL = 1
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessId.restype = wintypes.DWORD

    status = {
        "launch_id": request["launch_id"],
        "state": "requesting_elevation",
        "created_at": request.get("created_at"),
        "updated_at": _now(),
        "broker_pid": os.getpid(),
        "broker_context": _desktop_context(),
        "returncode": None,
        "win32_error": None,
        "elevated_pid": None,
    }
    _atomic_json_write(status_path, status)

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.hwnd = user32.GetForegroundWindow()
    info.lpVerb = "runas"
    info.lpFile = request["executable"]
    info.lpParameters = (
        subprocess.list2cmdline(request["args"])
        if request["args"]
        else None
    )
    info.lpDirectory = str(Path(request["executable"]).parent)
    info.nShow = SW_SHOWNORMAL

    ctypes.set_last_error(0)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        status.update(
            state="failed",
            updated_at=_now(),
            win32_error=ctypes.get_last_error(),
        )
        _atomic_json_write(status_path, status)
        return

    if not info.hProcess:
        status.update(
            state="failed",
            updated_at=_now(),
            win32_error=-1,
        )
        _atomic_json_write(status_path, status)
        return

    try:
        status.update(
            state="running",
            updated_at=_now(),
            elevated_pid=int(kernel32.GetProcessId(info.hProcess)),
        )
        _atomic_json_write(status_path, status)

        wait_result = kernel32.WaitForSingleObject(
            info.hProcess,
            request["timeout_seconds"] * 1000,
        )
        if wait_result == WAIT_TIMEOUT:
            status.update(state="timeout", updated_at=_now())
            _atomic_json_write(status_path, status)
            return
        if wait_result != WAIT_OBJECT_0:
            status.update(
                state="failed",
                updated_at=_now(),
                wait_result=int(wait_result),
            )
            _atomic_json_write(status_path, status)
            return

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            status.update(
                state="failed",
                updated_at=_now(),
                win32_error=ctypes.get_last_error(),
            )
            _atomic_json_write(status_path, status)
            return

        status.update(
            state="completed",
            updated_at=_now(),
            returncode=int(exit_code.value),
        )
        _atomic_json_write(status_path, status)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _request_files() -> list[Path]:
    return sorted(
        STATE_DIR.glob("*.request.json"),
        key=lambda path: path.stat().st_mtime,
    )


def _process_one_request() -> bool:
    for request_path in _request_files():
        launch_id = request_path.name[: -len(".request.json")]
        status_path = STATE_DIR / f"{launch_id}.status.json"
        try:
            status = (
                json.loads(status_path.read_text(encoding="utf-8"))
                if status_path.is_file()
                else {}
            )
        except Exception:
            status = {}

        if status.get("state") != "queued":
            continue

        try:
            request = _validate_request(
                json.loads(request_path.read_text(encoding="utf-8"))
            )
            _execute_elevated(request, status_path)
        except Exception as exc:
            _atomic_json_write(
                status_path,
                {
                    "launch_id": launch_id,
                    "state": "failed",
                    "updated_at": _now(),
                    "broker_pid": os.getpid(),
                    "exception_type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
            )
        return True
    return False


def _broker_status(state: str, current_launch_id: str | None = None) -> None:
    _atomic_json_write(
        BROKER_STATUS,
        {
            "state": state,
            "pid": os.getpid(),
            "updated_at": _now(),
            "current_launch_id": current_launch_id,
            **_desktop_context(),
        },
    )


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Interactive Elevation Broker requires Windows")

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
