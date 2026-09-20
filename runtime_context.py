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


CURRENT: ContextVar[ExecutionContext | None] = ContextVar("local_execution", default=None)


def checkpoint() -> None:
    context = CURRENT.get()
    if context:
        context.check()


def observe(kind: str, **data: Any) -> None:
    context = CURRENT.get()
    if context:
        context.observer(kind, data)
