"""Worker-local cancellation and observations; never accepts caller-supplied PIDs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Callable


class TaskCancelled(RuntimeError):
    pass


def terminate_owned_process_tree(process, timeout: float = 5.0) -> None:
    """Stop a runtime-owned child and its descendants without accepting a caller PID."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            completed = subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=10,
                check=False,
            )
            if completed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            if process.poll() is None:
                process.kill()
    elif process.poll() is None:
        process.kill()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=timeout)


@dataclass
class ExecutionContext:
    observer: Callable[[str, dict[str, Any]], None]
    task_id: str | None = None
    cancelled: Event = field(default_factory=Event)
    lock: Any = field(default_factory=Lock)
    processes: set = field(default_factory=set)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise TaskCancelled("Cancellation requested; earlier side effects are not undone")

    def register(self, process) -> None:
        with self.lock:
            self.processes.add(process)
            if self.cancelled.is_set() and process.poll() is None:
                terminate_owned_process_tree(process)

    def unregister(self, process) -> None:
        with self.lock:
            self.processes.discard(process)

    def cancel(self) -> None:
        with self.lock:
            self.cancelled.set()
            for process in self.processes:
                if process.poll() is None:
                    terminate_owned_process_tree(process)


@dataclass(frozen=True)
class InvocationTrace:
    correlation_id: str
    causation_id: str | None
    capability_id: str
    transaction_id: str | None = None


CURRENT: ContextVar[ExecutionContext | None] = ContextVar("local_execution", default=None)
INVOCATION_TRACE: ContextVar[InvocationTrace | None] = ContextVar(
    "capability_invocation_trace",
    default=None,
)


def current_trace() -> dict[str, Any]:
    """Return the bounded task/capability trace currently active on this call path."""

    trace: dict[str, Any] = {}
    execution = CURRENT.get()
    if execution is not None and execution.task_id:
        trace["task_id"] = execution.task_id
    invocation = INVOCATION_TRACE.get()
    if invocation is not None:
        trace.update(
            {
                "correlation_id": invocation.correlation_id,
                "causation_id": invocation.causation_id,
                "capability_id": invocation.capability_id,
                "transaction_id": invocation.transaction_id,
            }
        )
    return trace


def checkpoint() -> None:
    context = CURRENT.get()
    if context:
        context.check()


def observe(kind: str, **data: Any) -> None:
    context = CURRENT.get()
    if context:
        context.observer(kind, data)
