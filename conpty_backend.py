"""Minimal native Windows ConPTY transport for PLA persistent sessions.

This module owns only pseudoconsole plumbing. Policy (workspace roots, program
allowlists, routing, confirmation, and transactions) stays in session_runtime.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
from typing import BinaryIO, Mapping


PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259
DEFAULT_COLUMNS = 120
DEFAULT_ROWS = 30
MIN_DIMENSION = 1
MAX_DIMENSION = 32767


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _handle_value(handle) -> int:
    value = getattr(handle, "value", handle)
    if value is None:
        return 0
    return int(value)


def _validate_dimension(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not MIN_DIMENSION <= value <= MAX_DIMENSION:
        raise ValueError(
            f"{name} must be between {MIN_DIMENSION} and {MAX_DIMENSION}"
        )
    return value


def conpty_available() -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return all(
        hasattr(kernel32, name)
        for name in ("CreatePseudoConsole", "ResizePseudoConsole", "ClosePseudoConsole")
    )


class ConPTYProcess:
    """Small process-like wrapper used by session_runtime."""

    def __init__(
        self,
        *,
        process_handle: int,
        pid: int,
        pseudoconsole: int,
        stdin: BinaryIO,
        stdout: BinaryIO,
        command: tuple[str, ...],
    ) -> None:
        self._handle = process_handle
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = None
        self._pseudoconsole = pseudoconsole
        self._command = command
        self._returncode: int | None = None
        self._resources_closed = False
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.ResizePseudoConsole.argtypes = [ctypes.c_void_p, COORD]
        self._kernel32.ResizePseudoConsole.restype = ctypes.c_long
        self._kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
        self._kernel32.ClosePseudoConsole.restype = None

    def poll(self) -> int | None:
        if not self._handle:
            return self._returncode
        code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(
            wintypes.HANDLE(self._handle), ctypes.byref(code)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value == STILL_ACTIVE:
            return None
        self._returncode = int(code.value)
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            milliseconds = INFINITE
        else:
            if timeout < 0:
                raise ValueError("timeout cannot be negative")
            milliseconds = min(int(timeout * 1000), INFINITE - 1)
        result = self._kernel32.WaitForSingleObject(
            wintypes.HANDLE(self._handle), milliseconds
        )
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self._command, timeout)
        if result != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        returncode = self.poll()
        if returncode is None:
            raise RuntimeError("Process signaled exit without an exit code")
        return returncode

    def resize(self, columns: int, rows: int) -> None:
        columns = _validate_dimension("columns", columns)
        rows = _validate_dimension("rows", rows)
        result = self._kernel32.ResizePseudoConsole(
            ctypes.c_void_p(self._pseudoconsole),
            COORD(columns, rows),
        )
        if result != 0:
            raise OSError(f"ResizePseudoConsole failed with HRESULT 0x{result & 0xFFFFFFFF:08X}")

    def close_terminal(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        for stream in (self.stdin, self.stdout):
            try:
                stream.close()
            except Exception:
                pass
        if self._pseudoconsole:
            self._kernel32.ClosePseudoConsole(ctypes.c_void_p(self._pseudoconsole))
            self._pseudoconsole = 0
        if self._handle:
            try:
                self.poll()
            except Exception:
                pass
            self._kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = 0


def open_conpty(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    columns: int = DEFAULT_COLUMNS,
    rows: int = DEFAULT_ROWS,
) -> ConPTYProcess:
    """Create a native ConPTY-hosted child with UTF-8 bidirectional pipes."""

    if os.name != "nt":
        raise RuntimeError("ConPTY requires Windows")
    if not conpty_available():
        raise RuntimeError("ConPTY is unavailable on this Windows runtime")
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must contain non-empty strings")
    columns = _validate_dimension("columns", columns)
    rows = _validate_dimension("rows", rows)

    executable = shutil.which(command[0], path=env.get("PATH"))
    if executable is None:
        raise FileNotFoundError(f"Executable not found: {command[0]}")
    resolved_command = (executable, *command[1:])

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.CreatePseudoConsole.argtypes = [
        COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
    kernel32.ClosePseudoConsole.restype = None
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    input_read = wintypes.HANDLE()
    input_write = wintypes.HANDLE()
    output_read = wintypes.HANDLE()
    output_write = wintypes.HANDLE()
    hpc = ctypes.c_void_p()
    attribute_buffer = None
    attribute_list = None
    pi = PROCESS_INFORMATION()
    stdin_stream = None
    stdout_stream = None

    def close_handle(handle) -> None:
        value = _handle_value(handle)
        if value:
            kernel32.CloseHandle(wintypes.HANDLE(value))

    try:
        if not kernel32.CreatePipe(
            ctypes.byref(input_read), ctypes.byref(input_write), None, 0
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.CreatePipe(
            ctypes.byref(output_read), ctypes.byref(output_write), None, 0
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        result = kernel32.CreatePseudoConsole(
            COORD(columns, rows), input_read, output_write, 0, ctypes.byref(hpc)
        )
        if result != 0:
            raise OSError(
                f"CreatePseudoConsole failed with HRESULT 0x{result & 0xFFFFFFFF:08X}"
            )

        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(
            None, 1, 0, ctypes.byref(attribute_size)
        )
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(attribute_size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            hpc,
            ctypes.sizeof(ctypes.c_void_p),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = wintypes.HANDLE()
        startup.StartupInfo.hStdOutput = wintypes.HANDLE()
        startup.StartupInfo.hStdError = wintypes.HANDLE()
        startup.lpAttributeList = attribute_list

        command_line = subprocess.list2cmdline(list(resolved_command))
        command_buffer = ctypes.create_unicode_buffer(command_line)
        environment_text = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0\0"
        environment_buffer = ctypes.create_unicode_buffer(environment_text)

        if not kernel32.CreateProcessW(
            executable,
            command_buffer,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            str(cwd),
            ctypes.byref(startup),
            ctypes.byref(pi),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        # These ends are owned by the pseudoconsole after the hosted child exists.
        close_handle(input_read)
        input_read = wintypes.HANDLE()
        close_handle(output_write)
        output_write = wintypes.HANDLE()
        close_handle(pi.hThread)
        pi.hThread = wintypes.HANDLE()

        input_fd = msvcrt.open_osfhandle(
            _handle_value(input_write), os.O_WRONLY | os.O_BINARY
        )
        input_write = wintypes.HANDLE()
        output_fd = msvcrt.open_osfhandle(
            _handle_value(output_read), os.O_RDONLY | os.O_BINARY
        )
        output_read = wintypes.HANDLE()
        stdin_stream = os.fdopen(input_fd, "wb", buffering=0)
        stdout_stream = os.fdopen(output_fd, "rb", buffering=0)

        process_handle = _handle_value(pi.hProcess)
        pi.hProcess = wintypes.HANDLE()
        pseudoconsole = _handle_value(hpc)
        hpc = ctypes.c_void_p()
        return ConPTYProcess(
            process_handle=process_handle,
            pid=int(pi.dwProcessId),
            pseudoconsole=pseudoconsole,
            stdin=stdin_stream,
            stdout=stdout_stream,
            command=resolved_command,
        )
    except Exception:
        if stdin_stream is not None:
            try:
                stdin_stream.close()
            except Exception:
                pass
        if stdout_stream is not None:
            try:
                stdout_stream.close()
            except Exception:
                pass
        if _handle_value(pi.hProcess):
            close_handle(pi.hProcess)
        if _handle_value(pi.hThread):
            close_handle(pi.hThread)
        if hpc.value:
            kernel32.ClosePseudoConsole(hpc)
        for handle in (input_read, input_write, output_read, output_write):
            close_handle(handle)
        raise
    finally:
        if attribute_list is not None:
            kernel32.DeleteProcThreadAttributeList(attribute_list)
