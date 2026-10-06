"""Windows process identity helpers for the PLA Execution Runner.

PID alone is not a durable identity because Windows may reuse it.  The runner
records its creation timestamp and image path; the Control Plane re-queries the
OS before treating a durable state record as ownership evidence.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def current_process_identity() -> dict[str, Any]:
    identity = query_process_identity(os.getpid())
    if identity is None:
        raise RuntimeError("could not query current process identity")
    return identity


def query_process_identity(pid: int) -> dict[str, Any] | None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if os.name != "nt":
        return {"process_id": pid, "creation_time_100ns": None, "image_path": None}

    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != STILL_ACTIVE:
            return None

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        creation_value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime

        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        image_path = None
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            image_path = str(Path(buffer.value).resolve())

        return {
            "process_id": pid,
            "creation_time_100ns": int(creation_value),
            "image_path": image_path,
        }
    finally:
        kernel32.CloseHandle(handle)


def identity_matches(recorded: dict[str, Any], actual: dict[str, Any] | None) -> bool:
    if actual is None:
        return False
    if recorded.get("process_id") != actual.get("process_id"):
        return False
    expected_creation = recorded.get("process_creation_time_100ns")
    if expected_creation is not None and expected_creation != actual.get("creation_time_100ns"):
        return False
    expected_image = recorded.get("process_image_path")
    actual_image = actual.get("image_path")
    if expected_image and actual_image:
        if os.path.normcase(str(expected_image)) != os.path.normcase(str(actual_image)):
            return False
    return True
