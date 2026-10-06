"""Bounded persistent local process sessions for PLA.

The current transport is pipe-backed.  The public contract is deliberately
backend-neutral so a future ConPTY backend can replace the transport without
changing ChatGPT-facing session semantics.
"""
from __future__ import annotations

from collections import deque
import base64
import codecs
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from threading import Condition, Lock, RLock, Thread
import time
from typing import Any, Mapping
from uuid import uuid4

from execution_backend import runner_metadata, spawn_session_backend
from execution_runtime import ExecutionPermissionEnvelope
from command_semantics import classify_command
from execution_program_policy import SESSION_ALLOWED_PROGRAMS
from runtime_context import current_trace, terminate_owned_process_tree


MAX_SESSIONS = 32
MAX_EVENTS = 256
MAX_READ_EVENTS = 64
DEFAULT_READ_OUTPUT_BYTES = 20_000
MAX_READ_OUTPUT_BYTES = 200_000
MAX_INPUT_CHARACTERS = 65_536
MAX_INPUT_BYTES = 65_536
MAX_WAIT_SECONDS = 60
OUTPUT_CHUNK_CHARACTERS = 4_096

_CONFIRMED_SESSION_WRITE_SUGGESTION = {
    "name": "runtime.confirmed_session_write",
    "surface": "capability",
    "purpose": "Write to an interactive code session behind explicit INVOKE confirmation.",
}
_INTERACTIVE_CODE_ACTIONS = {"repl", "interactive", "module.code"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsKillOnCloseJob:
    """Own a Job Object that kills its member tree when the last handle closes."""

    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Persistent process sessions currently require Windows")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        ok = kernel32.SetInformationJobObject(
            handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)

        self._kernel32 = kernel32
        self._handle = handle
        self._closed = False

    def assign(self, process: subprocess.Popen) -> None:
        if self._closed:
            raise RuntimeError("Job Object is already closed")
        process_handle = wintypes.HANDLE(int(process._handle))
        if not self._kernel32.AssignProcessToJobObject(
            self._handle,
            process_handle,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.CloseHandle(handle)


@dataclass
class _Session:
    session_id: str
    execution_id: str
    process: subprocess.Popen
    job: _WindowsKillOnCloseJob
    permissions: ExecutionPermissionEnvelope
    command_name: str
    origin_trace: dict[str, Any] = field(default_factory=dict)
    runner: dict[str, object] = field(default_factory=dict)
    terminal_mode: str = "pipe"
    columns: int | None = None
    rows: int | None = None
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    status: str = "running"
    returncode: int | None = None
    cursor: int = 0
    dropped_through_cursor: int = 0
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_EVENTS)
    )
    exit_event_emitted: bool = False
    stdin_closed: bool = False
    operation_seq: int = 0
    lock: RLock = field(default_factory=RLock)
    interaction_lock: Lock = field(default_factory=Lock)
    condition: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.condition = Condition(self.lock)


class InteractiveSessionStore:
    """Own persistent children by opaque session id, never caller-supplied PID."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        if (
            isinstance(max_sessions, bool)
            or not isinstance(max_sessions, int)
            or not 1 <= max_sessions <= MAX_SESSIONS
        ):
            raise ValueError(
                f"max_sessions must be an integer from 1 to {MAX_SESSIONS}"
            )
        self.max_sessions = max_sessions
        self._lock = RLock()
        self._sessions: dict[str, _Session] = {}

    def _get(self, session_id: str) -> _Session:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown process session: {session_id}")
        return session

    def _prune_terminal(self) -> None:
        terminal = [
            item
            for item in self._sessions.values()
            if item.status in {"exited", "closed", "failed"}
        ]
        terminal.sort(key=lambda item: item.created_at)
        while len(self._sessions) >= self.max_sessions and terminal:
            victim = terminal.pop(0)
            self._sessions.pop(victim.session_id, None)

    @staticmethod
    def _append_event(
        session: _Session,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        with session.condition:
            if len(session.events) == MAX_EVENTS:
                session.dropped_through_cursor = session.events[0]["cursor"]
            session.cursor += 1
            session.events.append(
                {
                    "cursor": session.cursor,
                    "type": event_type,
                    "created_at": _now(),
                    "execution_id": session.execution_id,
                    "data": data,
                }
            )
            session.condition.notify_all()

    def _append_output(
        self,
        session: _Session,
        stream: str,
        content: str,
        *,
        final: bool = False,
    ) -> None:
        if not content and not final:
            return
        self._append_event(
            session,
            "output",
            {
                "stream": stream,
                "content": content,
                "final": final,
            },
        )

    def _record_exit(self, session: _Session, returncode: int | None) -> None:
        with session.condition:
            session.returncode = returncode
            session.finished_at = session.finished_at or _now()
            if session.status == "running":
                session.status = "exited"
            if session.exit_event_emitted:
                session.condition.notify_all()
                return
            session.exit_event_emitted = True
            if len(session.events) == MAX_EVENTS:
                session.dropped_through_cursor = session.events[0]["cursor"]
            session.cursor += 1
            session.events.append(
                {
                    "cursor": session.cursor,
                    "type": "process_exit",
                    "created_at": _now(),
                    "execution_id": session.execution_id,
                    "data": {
                        "returncode": returncode,
                        "status": session.status,
                    },
                }
            )
            session.condition.notify_all()

    def _reader(self, session: _Session, pipe, stream: str) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        try:
            while True:
                block = os.read(pipe.fileno(), 8192)
                if not block:
                    pending += decoder.decode(b"", final=True)
                    break
                pending += decoder.decode(block, final=False)
                while "\n" in pending:
                    newline = pending.find("\n") + 1
                    chunk, pending = pending[:newline], pending[newline:]
                    self._append_output(session, stream, chunk)
                while len(pending) >= OUTPUT_CHUNK_CHARACTERS:
                    chunk, pending = (
                        pending[:OUTPUT_CHUNK_CHARACTERS],
                        pending[OUTPUT_CHUNK_CHARACTERS:],
                    )
                    self._append_output(session, stream, chunk)
        except Exception as exc:
            self._append_event(
                session,
                "runtime_error",
                {
                    "stream": stream,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
            )
        finally:
            if pending:
                self._append_output(session, stream, pending, final=True)
            try:
                pipe.close()
            except Exception:
                pass

    def _watch(self, session: _Session) -> None:
        try:
            returncode = session.process.wait()
            self._record_exit(session, returncode)
        finally:
            # Closing the Job handle also reaps any descendants intentionally left
            # behind by the direct child. PLA sessions never detach process trees.
            session.job.close()
            close_terminal = getattr(session.process, "close_terminal", None)
            if callable(close_terminal):
                close_terminal()

    def _open_resolved(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str],
        permissions: ExecutionPermissionEnvelope,
        terminal_mode: str,
        columns: int,
        rows: int,
    ) -> dict[str, Any]:
        with self._lock:
            self._prune_terminal()
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(
                    f"At most {self.max_sessions} active process sessions are allowed"
                )

        runner = runner_metadata()
        process = spawn_session_backend(
            command,
            cwd=cwd,
            env=env,
            terminal_mode=terminal_mode,
            columns=columns,
            rows=rows,
        )

        job = None
        try:
            job = _WindowsKillOnCloseJob()
            job.assign(process)
        except Exception:
            if job is not None:
                job.close()
            if process.poll() is None:
                terminate_owned_process_tree(process)
            close_terminal = getattr(process, "close_terminal", None)
            if callable(close_terminal):
                close_terminal()
            raise

        session = _Session(
            session_id=uuid4().hex,
            execution_id=uuid4().hex,
            process=process,
            job=job,
            permissions=permissions,
            command_name=Path(command[0]).name,
            origin_trace=current_trace(),
            runner=runner,
            terminal_mode=terminal_mode,
            columns=columns if terminal_mode == "conpty" else None,
            rows=rows if terminal_mode == "conpty" else None,
        )
        with self._lock:
            self._sessions[session.session_id] = session

        readers = [("stdout", process.stdout)]
        if process.stderr is not None:
            readers.append(("stderr", process.stderr))
        for stream, pipe in readers:
            Thread(
                target=self._reader,
                args=(session, pipe, stream),
                daemon=True,
                name=f"pla-session-{session.session_id[:8]}-{stream}",
            ).start()
        Thread(
            target=self._watch,
            args=(session,),
            daemon=True,
            name=f"pla-session-{session.session_id[:8]}-watch",
        ).start()

        return {
            "session_id": session.session_id,
            "execution_id": session.execution_id,
            "status": "running",
            "terminal_mode": terminal_mode,
            "command_name": session.command_name,
            "created_at": session.created_at,
            "next_cursor": 0,
            "permissions": permissions.to_dict(),
            "job_containment": "kill_on_close",
            "columns": session.columns,
            "rows": session.rows,
            "stdin_closed": session.stdin_closed,
            "operation_seq": session.operation_seq,
            "origin_trace": dict(session.origin_trace),
            "runner": dict(session.runner),
        }

    def open(
        self,
        program: str,
        args: list[str] | None = None,
        cwd: str = ".",
        env: dict[str, str] | None = None,
        root: str = "workspace",
        terminal_mode: str = "pipe",
        columns: int = 120,
        rows: int = 30,
    ) -> dict[str, Any]:
        """Open one persistent session through the existing local process policy."""

        # Lazy import avoids a module cycle: local_tools imports SESSION_STORE.
        import local_tools

        args = [] if args is None else args
        if not isinstance(args, list) or any(
            not isinstance(item, str) for item in args
        ):
            raise TypeError("args must be an array of strings")
        if terminal_mode not in {"pipe", "conpty"}:
            raise ValueError("terminal_mode must be 'pipe' or 'conpty'")
        for name, value in (("columns", columns), ("rows", rows)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if not 1 <= value <= 32767:
                raise ValueError(f"{name} must be between 1 and 32767")

        program_name = Path(program).name.lower()
        if program_name not in SESSION_ALLOWED_PROGRAMS:
            raise ValueError(f"Program not allowed: {program}")

        semantic = classify_command(program, args)
        steering = local_tools.steer_run_process(program, args, root)
        if steering is not None:
            steering.setdefault("command_semantic", semantic.to_dict())
            return steering

        working_directory = local_tools.safe_path(cwd, root, "execute")
        if not working_directory.exists() or not working_directory.is_dir():
            raise ValueError(
                f"Working directory does not exist or is not a directory: {cwd}"
            )

        command = [program, *args]
        if program_name in {"pytest", "pytest.exe"}:
            import sys

            command = [sys.executable, "-m", "pytest", *args]

        relative_cwd = local_tools._relative_path(working_directory, root)
        permissions = ExecutionPermissionEnvelope(
            selected_root=root,
            cwd=relative_cwd,
            route=(
                "interactive_conpty_session"
                if terminal_mode == "conpty"
                else "interactive_pipe_session"
            ),
            program_policy="allowlisted_program",
            semantic_domain=semantic.domain,
            semantic_action=semantic.action,
            semantic_risk_level=semantic.risk_level,
            semantic_effect_class=semantic.effect_class,
            network_intent=semantic.network_intent,
            semantic_confidence=semantic.confidence,
        )
        return self._open_resolved(
            command=tuple(command),
            cwd=working_directory,
            env=local_tools._validate_process_env(env),
            permissions=permissions,
            terminal_mode=terminal_mode,
            columns=columns,
            rows=rows,
        )

    @staticmethod
    def _record_interaction(session: _Session, action: str) -> int:
        with session.lock:
            session.operation_seq += 1
            operation_seq = session.operation_seq
        InteractiveSessionStore._append_event(
            session,
            "interaction",
            {
                "operation_seq": operation_seq,
                "action": action,
                "trace": current_trace(),
            },
        )
        return operation_seq

    @staticmethod
    def _input_policy(session: _Session, payload: bytes) -> dict[str, Any]:
        permissions = session.permissions
        if payload == b"\x03":
            return {
                "policy_id": "session_control_input",
                "policy_version": "1",
                "requires_confirmation": False,
                "risk_level": "write_local",
                "effect_class": "control_input",
                "network_intent": "none",
                "confidence": "high",
                "reason": "terminal interrupt control input",
            }
        interactive_code = (
            permissions.semantic_domain == "python"
            and permissions.semantic_action in _INTERACTIVE_CODE_ACTIONS
        )
        if interactive_code:
            return {
                "policy_id": "interactive_session_input",
                "policy_version": "1",
                "requires_confirmation": True,
                "risk_level": "write_local",
                "effect_class": "arbitrary_code",
                "network_intent": "possible",
                "confidence": "high",
                "reason": "stdin is executable code for an interactive Python session",
            }
        return {
            "policy_id": "session_data_input",
            "policy_version": "1",
            "requires_confirmation": False,
            "risk_level": permissions.semantic_risk_level,
            "effect_class": "session_input",
            "network_intent": permissions.network_intent,
            "confidence": "high",
            "reason": "stdin is treated as data for this session origin",
        }

    def input_policy(self, session_id: str, payload: bytes) -> dict[str, Any]:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError(f"input cannot exceed {MAX_INPUT_BYTES} bytes")
        session = self._get(session_id)
        policy = self._input_policy(session, payload)
        return {
            "session_id": session_id,
            "execution_id": session.execution_id,
            "terminal_mode": session.terminal_mode,
            "command_name": session.command_name,
            "origin_semantic": {
                "domain": session.permissions.semantic_domain,
                "action": session.permissions.semantic_action,
                "risk_level": session.permissions.semantic_risk_level,
                "effect_class": session.permissions.semantic_effect_class,
            },
            **policy,
        }

    def _write_bytes_unchecked(
        self,
        session: _Session,
        payload: bytes,
        *,
        confirmed: bool,
        input_policy: dict[str, Any],
    ) -> dict[str, Any]:
        with session.interaction_lock:
            operation_seq = self._record_interaction(
                session,
                "write_confirmed" if confirmed else "write",
            )
            with session.lock:
                if session.status != "running" or session.process.poll() is not None:
                    raise RuntimeError("Process session is not running")
                if session.stdin_closed or session.process.stdin is None:
                    raise RuntimeError("Process session stdin is closed")
                try:
                    session.process.stdin.write(payload)
                    session.process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    session.stdin_closed = True
                    raise RuntimeError("Process session stdin is closed") from exc

        return {
            "session_id": session.session_id,
            "execution_id": session.execution_id,
            "status": session.status,
            "bytes_written": len(payload),
            "operation_seq": operation_seq,
            "terminal_mode": session.terminal_mode,
            "input_policy": input_policy,
            "confirmation_required": bool(input_policy["requires_confirmation"]),
            "confirmation_supplied": confirmed,
        }

    def write_bytes(self, session_id: str, payload: bytes) -> dict[str, Any]:
        policy = self.input_policy(session_id, payload)
        session = self._get(session_id)
        if policy["requires_confirmation"]:
            return {
                "status": "blocked",
                "reason": "session_input_confirmation_required",
                "session_id": session_id,
                "execution_id": session.execution_id,
                "terminal_mode": session.terminal_mode,
                "operation_seq": session.operation_seq,
                "bytes_requested": len(payload),
                "input_policy": policy,
                "suggested_capabilities": [dict(_CONFIRMED_SESSION_WRITE_SUGGESTION)],
            }
        return self._write_bytes_unchecked(
            session,
            payload,
            confirmed=False,
            input_policy=policy,
        )

    def write_confirmed_bytes(self, session_id: str, payload: bytes) -> dict[str, Any]:
        policy = self.input_policy(session_id, payload)
        if not policy["requires_confirmation"]:
            raise ValueError(
                "runtime.confirmed_session_write only accepts session input that requires confirmation"
            )
        session = self._get(session_id)
        return self._write_bytes_unchecked(
            session,
            payload,
            confirmed=True,
            input_policy=policy,
        )

    def write(self, session_id: str, input_text: str) -> dict[str, Any]:
        if not isinstance(input_text, str):
            raise TypeError("input must be a string")
        if len(input_text) > MAX_INPUT_CHARACTERS:
            raise ValueError(
                f"input cannot exceed {MAX_INPUT_CHARACTERS} characters"
            )
        payload = input_text.encode("utf-8")
        result = self.write_bytes(session_id, payload)
        if "bytes_written" in result:
            result["characters_written"] = len(input_text)
        else:
            result["characters_requested"] = len(input_text)
        return result

    def write_confirmed(self, session_id: str, input_text: str) -> dict[str, Any]:
        if not isinstance(input_text, str):
            raise TypeError("input must be a string")
        if len(input_text) > MAX_INPUT_CHARACTERS:
            raise ValueError(
                f"input cannot exceed {MAX_INPUT_CHARACTERS} characters"
            )
        result = self.write_confirmed_bytes(session_id, input_text.encode("utf-8"))
        result["characters_written"] = len(input_text)
        return result

    def write_base64(self, session_id: str, input_base64: str) -> dict[str, Any]:
        if not isinstance(input_base64, str):
            raise TypeError("input_base64 must be a string")
        try:
            payload = base64.b64decode(input_base64, validate=True)
        except Exception as exc:
            raise ValueError("input_base64 must be valid base64") from exc
        return self.write_bytes(session_id, payload)

    def write_confirmed_base64(self, session_id: str, input_base64: str) -> dict[str, Any]:
        if not isinstance(input_base64, str):
            raise TypeError("input_base64 must be a string")
        try:
            payload = base64.b64decode(input_base64, validate=True)
        except Exception as exc:
            raise ValueError("input_base64 must be valid base64") from exc
        return self.write_confirmed_bytes(session_id, payload)

    def close_stdin(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        with session.interaction_lock:
            operation_seq = self._record_interaction(session, "close_stdin")
            with session.lock:
                if session.stdin_closed:
                    return {
                        "session_id": session_id,
                        "execution_id": session.execution_id,
                        "status": session.status,
                        "stdin_closed": True,
                        "operation_seq": operation_seq,
                        "terminal_mode": session.terminal_mode,
                    }
                if session.process.stdin is None:
                    raise RuntimeError("Process session stdin is unavailable")
                try:
                    session.process.stdin.close()
                except OSError as exc:
                    raise RuntimeError("Failed to close process session stdin") from exc
                session.stdin_closed = True

        self._append_event(
            session,
            "stdin_closed",
            {"operation_seq": operation_seq},
        )
        return {
            "session_id": session_id,
            "execution_id": session.execution_id,
            "status": session.status,
            "stdin_closed": True,
            "operation_seq": operation_seq,
            "terminal_mode": session.terminal_mode,
        }

    def resize(self, session_id: str, columns: int, rows: int) -> dict[str, Any]:
        session = self._get(session_id)
        if session.terminal_mode != "conpty":
            raise RuntimeError("Only ConPTY sessions support resize")
        for name, value in (("columns", columns), ("rows", rows)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if not 1 <= value <= 32767:
                raise ValueError(f"{name} must be between 1 and 32767")

        with session.interaction_lock:
            operation_seq = self._record_interaction(session, "resize")
            with session.lock:
                if session.status != "running" or session.process.poll() is not None:
                    raise RuntimeError("Process session is not running")
                resize = getattr(session.process, "resize", None)
                if not callable(resize):
                    raise RuntimeError("Process session backend does not support resize")
                resize(columns, rows)
                session.columns = columns
                session.rows = rows

        self._append_event(
            session,
            "resize",
            {"columns": columns, "rows": rows, "operation_seq": operation_seq},
        )
        return {
            "session_id": session_id,
            "execution_id": session.execution_id,
            "status": session.status,
            "terminal_mode": session.terminal_mode,
            "columns": columns,
            "rows": rows,
            "operation_seq": operation_seq,
        }

    def terminate(self, session_id: str) -> dict[str, Any]:
        """Terminate the owned process tree but retain the session record."""

        session = self._get(session_id)
        with session.interaction_lock:
            operation_seq = self._record_interaction(session, "terminate")
            with session.lock:
                if session.status == "closed":
                    raise RuntimeError("Process session is closed")
                already_exited = session.process.poll() is not None
            if not already_exited:
                session.job.close()
                if session.process.poll() is None:
                    terminate_owned_process_tree(session.process)
                try:
                    session.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self._record_exit(session, session.process.poll())

        self._append_event(
            session,
            "terminate",
            {"operation_seq": operation_seq, "returncode": session.returncode},
        )
        return {
            "session_id": session_id,
            "execution_id": session.execution_id,
            "status": session.status,
            "returncode": session.returncode,
            "operation_seq": operation_seq,
            "terminal_mode": session.terminal_mode,
        }

    @staticmethod
    def _apply_output_budget(
        events: list[dict[str, Any]],
        max_output_bytes: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
        remaining = max_output_bytes
        original_bytes = 0
        returned_bytes = 0
        budgeted: list[dict[str, Any]] = []

        for event in events:
            copied = dict(event)
            copied["data"] = dict(event.get("data") or {})
            if event.get("type") == "output":
                content = copied["data"].get("content", "")
                encoded = content.encode("utf-8")
                size = len(encoded)
                original_bytes += size
                if size > remaining:
                    prefix = encoded[:remaining].decode("utf-8", errors="ignore")
                    kept = len(prefix.encode("utf-8"))
                    copied["data"]["content"] = prefix
                    copied["data"]["truncated"] = True
                    copied["data"]["original_bytes"] = size
                    copied["data"]["omitted_bytes"] = size - kept
                    returned_bytes += kept
                    remaining = max(0, remaining - kept)
                else:
                    returned_bytes += size
                    remaining -= size
            budgeted.append(copied)

        return budgeted, {
            "output_original_bytes": original_bytes,
            "output_returned_bytes": returned_bytes,
            "output_omitted_bytes": original_bytes - returned_bytes,
            "output_truncated": original_bytes > returned_bytes,
        }

    def read(
        self,
        session_id: str,
        cursor: int = 0,
        wait_seconds: float = 0,
        max_output_bytes: int = DEFAULT_READ_OUTPUT_BYTES,
    ) -> dict[str, Any]:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if (
            isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float))
            or not 0 <= wait_seconds <= MAX_WAIT_SECONDS
        ):
            raise ValueError(
                f"wait_seconds must be between 0 and {MAX_WAIT_SECONDS}"
            )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 0 <= max_output_bytes <= MAX_READ_OUTPUT_BYTES
        ):
            raise ValueError(
                f"max_output_bytes must be between 0 and {MAX_READ_OUTPUT_BYTES}"
            )

        session = self._get(session_id)
        deadline = time.monotonic() + wait_seconds
        with session.condition:
            while True:
                events = [
                    dict(item)
                    for item in session.events
                    if item["cursor"] > cursor
                ][:MAX_READ_EVENTS]
                if (
                    events
                    or session.status != "running"
                    or wait_seconds == 0
                ):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                session.condition.wait(remaining)

            next_cursor = events[-1]["cursor"] if events else cursor
            has_more = any(
                item["cursor"] > next_cursor
                for item in session.events
            )
            budgeted_events, budget = self._apply_output_budget(
                events,
                max_output_bytes,
            )
            return {
                "session_id": session_id,
                "execution_id": session.execution_id,
                "status": session.status,
                "returncode": session.returncode,
                "created_at": session.created_at,
                "finished_at": session.finished_at,
                "events": budgeted_events,
                "next_cursor": next_cursor,
                "events_truncated": cursor < session.dropped_through_cursor,
                "dropped_through_cursor": session.dropped_through_cursor,
                "has_more": has_more,
                "permissions": session.permissions.to_dict(),
                "origin_trace": dict(session.origin_trace),
                "runner": dict(session.runner),
                "terminal_mode": session.terminal_mode,
                "columns": session.columns,
                "rows": session.rows,
                "stdin_closed": session.stdin_closed,
                "operation_seq": session.operation_seq,
                "max_output_bytes": max_output_bytes,
                **budget,
            }

    def close(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)

        with session.interaction_lock:
            operation_seq = self._record_interaction(session, "close")
            with session.lock:
                if session.status == "closed":
                    return {
                        "session_id": session_id,
                        "execution_id": session.execution_id,
                        "status": "closed",
                        "returncode": session.returncode,
                        "operation_seq": operation_seq,
                        "terminal_mode": session.terminal_mode,
                    }
                session.status = "closed"
                session.finished_at = session.finished_at or _now()
                try:
                    if not session.stdin_closed and session.process.stdin is not None:
                        session.process.stdin.close()
                except Exception:
                    pass
                session.stdin_closed = True
                session.job.close()

            if session.process.poll() is None:
                try:
                    session.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_owned_process_tree(session.process)
                    try:
                        session.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass

            self._record_exit(session, session.process.poll())
        return {
            "session_id": session_id,
            "execution_id": session.execution_id,
            "status": "closed",
            "returncode": session.returncode,
            "operation_seq": operation_seq,
            "terminal_mode": session.terminal_mode,
        }

    def close_all(self) -> dict[str, int]:
        with self._lock:
            session_ids = list(self._sessions)

        closed = 0
        errors = 0
        for session_id in session_ids:
            try:
                result = self.close(session_id)
                if result["status"] == "closed":
                    closed += 1
            except Exception:
                errors += 1
        return {
            "sessions_seen": len(session_ids),
            "sessions_closed": closed,
            "errors": errors,
        }


def confirmed_session_write_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """Write confirmed stdin only when the session input policy requires approval."""

    if not isinstance(arguments, dict):
        raise TypeError("arguments must be an object")
    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("confirmed session write requires session_id")
    input_text = arguments.get("input")
    input_base64 = arguments.get("input_base64")
    has_text = isinstance(input_text, str)
    has_base64 = isinstance(input_base64, str)
    if has_text == has_base64:
        raise ValueError("confirmed session write requires exactly one of input or input_base64")
    if has_base64:
        return SESSION_STORE.write_confirmed_base64(session_id, input_base64)
    return SESSION_STORE.write_confirmed(session_id, input_text)


def process_session_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the stable runtime.process_session capability contract."""

    if not isinstance(arguments, dict):
        raise TypeError("arguments must be an object")
    action = arguments.get("action")

    if action == "open":
        program = arguments.get("program")
        if not isinstance(program, str) or not program:
            raise ValueError("open requires a non-empty program")
        return SESSION_STORE.open(
            program=program,
            args=arguments.get("args"),
            cwd=arguments.get("cwd", "."),
            env=arguments.get("env"),
            root=arguments.get("root", "workspace"),
            terminal_mode=arguments.get("terminal_mode", "pipe"),
            columns=arguments.get("columns", 120),
            rows=arguments.get("rows", 30),
        )

    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"{action or 'session action'} requires session_id")

    if action == "write":
        input_text = arguments.get("input")
        input_base64 = arguments.get("input_base64")
        has_text = isinstance(input_text, str)
        has_base64 = isinstance(input_base64, str)
        if has_text == has_base64:
            raise ValueError("write requires exactly one of input or input_base64")
        if has_base64:
            return SESSION_STORE.write_base64(session_id, input_base64)
        return SESSION_STORE.write(session_id, input_text)

    if action == "close_stdin":
        return SESSION_STORE.close_stdin(session_id)

    if action == "read":
        return SESSION_STORE.read(
            session_id,
            cursor=arguments.get("cursor", 0),
            wait_seconds=arguments.get("wait_seconds", 0),
            max_output_bytes=arguments.get(
                "max_output_bytes",
                DEFAULT_READ_OUTPUT_BYTES,
            ),
        )

    if action == "resize":
        return SESSION_STORE.resize(
            session_id,
            columns=arguments.get("columns", 120),
            rows=arguments.get("rows", 30),
        )

    if action == "terminate":
        return SESSION_STORE.terminate(session_id)

    if action == "close":
        return SESSION_STORE.close(session_id)

    raise ValueError(
        "action must be one of: open, write, close_stdin, read, resize, terminate, close"
    )


SESSION_STORE = InteractiveSessionStore()
SESSION_MANAGER = SESSION_STORE
