"""SQLite task records, bounded observations, and background execution."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4
from internal_tool_executor import execute_actions_request, execute_local_tool
from runtime_context import CURRENT, ExecutionContext

TERMINAL = {"completed", "failed", "cancelled"}
MAX_EVENTS = 256
MAX_RECORD_CHARACTERS = 2_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(kind: str, message: str, task_id: str | None = None) -> dict:
    return {"task_id": task_id, "status": "error",
            "error": {"type": kind, "message": message[:20_000]}}


class TaskStore:
    """One owner per database; in-memory default for embedded/test callers.

    Server explicitly supplies state/tasks.sqlite3. Lazy initialization maps
    corrupt/unavailable storage to structured tool errors instead of import failure.
    """
    def __init__(self, max_workers: int = 4, db_path: str | Path = ":memory:",
                 require_outside_workspace: bool = False) -> None:
        self.db_path = str(db_path)
        self.require_outside_workspace = require_outside_workspace
        self._lock = RLock()
        self._db = None
        self._owner_file = None
        self._closed = False
        self._contexts: dict[str, ExecutionContext] = {}
        self._workers = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="local-task")

    def _ensure(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("TaskStore is closed")
        if self._db is not None:
            return self._db
        db = None
        try:
            if self.db_path != ":memory:":
                path = Path(self.db_path).resolve()
                if self.require_outside_workspace:
                    from local_tools import WORKSPACE
                    if path.is_relative_to(WORKSPACE):
                        raise ValueError("Task database must be outside the user workspace")
                path.parent.mkdir(parents=True, exist_ok=True)
                owner = open(str(path) + ".lock", "a+b")
                self._owner_file = owner
                owner.seek(0)
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(owner.fileno()).st_size == 0:
                        owner.write(b"0")
                        owner.flush()
                    owner.seek(0)
                    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, request TEXT NOT NULL, result TEXT, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, dropped_cursor INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS events_by_task ON events(task_id, seq)")
            self._db = db
            with db:
                pending = db.execute("SELECT task_id FROM tasks WHERE status IN ('queued', 'running')").fetchall()
                for row in pending:
                    error = {"type": "TaskInterruptedByRestart", "message": "Previous runtime stopped; no replay; prior side effects may exist"}
                    db.execute("UPDATE tasks SET status='failed', finished_at=?, error=? WHERE task_id=?", (_now(), json.dumps(error), row[0]))
                    self._event(row[0], "task_failed", {"error": error})
            return db
        except Exception:
            self._db = None
            if db is not None:
                db.close()
            if self._owner_file:
                self._owner_file.close()
                self._owner_file = None
            raise

    def _event(self, task_id: str, kind: str, data: dict) -> None:
        # Called under the store lock inside a database transaction.
        serialized = json.dumps(data, ensure_ascii=False)
        if len(serialized) > 24_000:
            data = {"content": serialized[-20_000:], "truncated": True,
                    "original_length": len(serialized), "encoding": "json_tail"}
        self._db.execute("INSERT INTO events(task_id, kind, created_at, data) VALUES (?, ?, ?, ?)",
                         (task_id, kind, _now(), json.dumps(data, ensure_ascii=False)))
        cutoff = self._db.execute("SELECT seq FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1 OFFSET ?", (task_id, MAX_EVENTS)).fetchone()
        if cutoff:
            self._db.execute("UPDATE tasks SET dropped_cursor=? WHERE task_id=?", (cutoff[0], task_id))
            self._db.execute("DELETE FROM events WHERE task_id=? AND seq<=?", (task_id, cutoff[0]))

    def _observe(self, task_id: str, kind: str, data: dict) -> None:
        with self._lock, self._ensure():
            self._event(task_id, kind, data)

    def submit(self, request: dict[str, Any]) -> dict:
        with self._lock:
            try:
                db = self._ensure()
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                if "actions" in request and (not isinstance(request["actions"], list) or not 1 <= len(request["actions"]) <= 100):
                    raise ValueError("actions must contain 1–100 entries")
                serialized = json.dumps(request, ensure_ascii=False)
                if len(serialized) > 1_000_000:
                    raise ValueError("Task request exceeds 1,000,000 characters")
                if len(self._contexts) >= 256:
                    return _error("TaskQueueFull", "At most 256 active tasks are allowed")
                task_id = uuid4().hex
                with db:
                    db.execute("INSERT INTO tasks(task_id,status,created_at,request) VALUES (?, 'queued', ?, ?)", (task_id, _now(), serialized))
                    self._event(task_id, "task_queued", {})
                self._contexts[task_id] = ExecutionContext(lambda kind, data: self._observe(task_id, kind, data))
                try:
                    self._workers.submit(self._run, task_id)
                except Exception as exc:
                    self._contexts.pop(task_id, None)
                    self._finish(task_id, "failed", None, {"type": type(exc).__name__, "message": str(exc)})
                return {"task_id": task_id, "status": "queued"}
            except Exception as exc:
                return _error("TaskStoreError", str(exc))

    def _finish(self, task_id: str, status: str, result: Any, error: Any) -> None:
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) > MAX_RECORD_CHARACTERS:
            serialized = json.dumps({"content": serialized[-20_000:], "truncated": True,
                                     "original_length": len(serialized), "encoding": "json_tail"})
        with self._db:
            self._db.execute("UPDATE tasks SET status=?,result=?,error=?,finished_at=? WHERE task_id=?",
                             (status, serialized, json.dumps(error), _now(), task_id))
            self._event(task_id, "task_" + status, {"error": error})

    def _run(self, task_id: str) -> None:
        token = None
        try:
            with self._lock:
                db = self._ensure()
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row["status"] != "queued":
                    return
                context = self._contexts[task_id]
                with db:
                    db.execute("UPDATE tasks SET status='running',started_at=? WHERE task_id=?", (_now(), task_id))
                    self._event(task_id, "task_started", {})
                request = json.loads(row["request"])
            token = CURRENT.set(context)
            context.check()
            if "actions" in request:
                result = execute_actions_request(request["actions"], request.get("stop_on_error", True))
                failed = result["status"] != "completed"
            else:
                outcome = execute_local_tool(request["tool"], request["arguments"])
                result = outcome.to_dict()
                failed = not outcome.ok
            error = {"type": "TaskExecutionError", "message": "One or more local actions failed"} if failed else None
            with self._lock:
                status = "cancelled" if context.cancelled.is_set() else "failed" if failed else "completed"
                if status == "cancelled":
                    error = {"type": "TaskCancelled", "message": "Cancellation acknowledged; inspect result for prior side effects"}
                self._finish(task_id, status, result, error)
        except Exception as exc:
            with self._lock:
                context = self._contexts.get(task_id)
                status = "cancelled" if context and context.cancelled.is_set() else "failed"
                try:
                    self._finish(task_id, status, None, {"type": type(exc).__name__, "message": str(exc)[:20_000]})
                except Exception:
                    # Reads expose storage errors; the next owner recovers without replay.
                    pass
        finally:
            if token is not None:
                CURRENT.reset(token)
            with self._lock:
                self._contexts.pop(task_id, None)

    def get(self, task_id: str, cursor: int | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                if cursor is not None and (type(cursor) is not int or cursor < 0):
                    return _error("ValidationError", "cursor must be a non-negative integer", task_id)
                db = self._ensure()
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row is None:
                    return _error("TaskNotFound", f"Task not found: {task_id}", task_id)
                record = dict(row)
                for name in ("request", "result", "error"):
                    record[name] = json.loads(record[name]) if record[name] is not None else None
                if record["status"] not in TERMINAL | {"queued", "running"} or not isinstance(record["request"], dict):
                    raise ValueError("Malformed task record")
                if record["status"] in {"queued", "running"} and task_id not in self._contexts:
                    return _error("TaskStoreError", "Worker stopped without persisting a terminal record; restart recovery required", task_id)
                record["cancel_requested"] = bool(record["cancel_requested"])
                dropped = record.pop("dropped_cursor")
                if cursor is not None:
                    record["result_available"] = record["result"] is not None
                    record.pop("request")
                    record.pop("result")
                    first = db.execute("SELECT MIN(seq) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
                    rows = db.execute("SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 64", (task_id, cursor)).fetchall()
                    record["events"] = [{"cursor": event["seq"], "type": event["kind"], "created_at": event["created_at"], "data": json.loads(event["data"])} for event in rows]
                    record["next_cursor"] = rows[-1]["seq"] if rows else cursor
                    record["events_truncated"] = cursor < dropped
                    record["dropped_through_cursor"] = dropped
                    record["oldest_cursor"] = first
                    record["has_more"] = bool(db.execute("SELECT 1 FROM events WHERE task_id=? AND seq>?", (task_id, record["next_cursor"])).fetchone())
                return record
            except Exception as exc:
                return _error("TaskStoreError", str(exc), task_id)

    def cancel(self, task_id: str) -> dict:
        with self._lock:
            record = self.get(task_id)
            if record["status"] == "error" or record["status"] in TERMINAL:
                return record
            try:
                context = self._contexts.get(task_id)
                if context is None:
                    return _error("TaskStoreError", "Task has no active owner", task_id)
                with self._db:
                    self._db.execute("UPDATE tasks SET cancel_requested=1 WHERE task_id=?", (task_id,))
                    if not record["cancel_requested"]:
                        self._event(task_id, "cancellation_requested", {})
                context.cancel()
                if record["status"] == "queued":
                    self._finish(task_id, "cancelled", None, {"type": "TaskCancelled", "message": "Cancelled before execution"})
                return self.get(task_id)
            except Exception as exc:
                return _error("TaskStoreError", str(exc), task_id)

    def close(self) -> None:
        self._workers.shutdown(wait=True)
        with self._lock:
            if self._db:
                self._db.close()
                self._db = None
            if self._owner_file:
                self._owner_file.close()
                self._owner_file = None
            self._closed = True

    def initialize(self) -> dict:
        with self._lock:
            try:
                self._ensure()
                return {"status": "ready"}
            except Exception as exc:
                return _error("TaskStoreError", str(exc))


TASK_STORE = TaskStore(db_path=os.environ.get("AGENT_TASK_DB", str(Path(__file__).resolve().parent / "state" / "tasks.sqlite3")),
                       require_outside_workspace=True)
