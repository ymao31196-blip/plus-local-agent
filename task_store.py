"""In-memory task records and background execution for structured local work."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from internal_tool_executor import execute_actions_request, execute_local_tool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskRecord:
    task_id: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    request: dict[str, Any]
    result: Any = None
    error: dict[str, str] | None = None


class TaskStore:
    def __init__(self, max_workers: int = 4) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._lock = Lock()
        self._workers = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="local-task"
        )

    def submit(self, request: dict[str, Any]) -> dict[str, str]:
        task_id = uuid4().hex
        record = TaskRecord(task_id, "queued", _now(), None, None, deepcopy(request))
        with self._lock:
            self._records[task_id] = record
        self._workers.submit(self._run, task_id)
        return {"task_id": task_id, "status": "queued"}

    def _run(self, task_id: str) -> None:
        with self._lock:
            record = self._records[task_id]
            record.status = "running"
            record.started_at = _now()
            request = deepcopy(record.request)
        try:
            if "actions" in request:
                result = execute_actions_request(
                    request["actions"], request.get("stop_on_error", True)
                )
                failed = result["status"] != "completed"
            else:
                outcome = execute_local_tool(request["tool"], request["arguments"])
                result = outcome.to_dict()
                failed = not outcome.ok
            with self._lock:
                record.result = result
                record.status = "failed" if failed else "completed"
                if failed:
                    record.error = {
                        "type": "TaskExecutionError",
                        "message": "One or more local actions failed",
                    }
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            with self._lock:
                record.finished_at = _now()

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return {
                    "task_id": task_id,
                    "status": "error",
                    "error": {
                        "type": "TaskNotFound",
                        "message": f"Task not found: {task_id}",
                    },
                }
            return deepcopy(asdict(record))


TASK_STORE = TaskStore()
