"""Worker-local cancellation and observations; never accepts caller-supplied PIDs."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Callable


class TaskCancelled(RuntimeError):
    pass


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
                process.kill()

    def unregister(self, process) -> None:
        with self.lock:
            self.processes.discard(process)

    def cancel(self) -> None:
        with self.lock:
            self.cancelled.set()
            for process in self.processes:
                if process.poll() is None:
                    process.kill()


CURRENT: ContextVar[ExecutionContext | None] = ContextVar("local_execution", default=None)


def checkpoint() -> None:
    context = CURRENT.get()
    if context:
        context.check()


def observe(kind: str, **data: Any) -> None:
    context = CURRENT.get()
    if context:
        context.observer(kind, data)
