"""Fail-closed updater preflight for persisted background tasks.

Use a read-only SQLite connection. An unavailable or corrupt task database is
not evidence that it is safe to replace an active Desktop runtime.
"""
from __future__ import annotations
from pathlib import Path
from contextlib import closing
import sqlite3


def unfinished_tasks(database: Path) -> tuple[int | None, str | None]:
    """Return (active task count, error code); no SQL task content is exposed."""
    if not database.exists():
        return 0, None
    if not database.is_file():
        return None, "task_store_not_a_file"
    try:
        # uri=False with a read-only file descriptor isn't supported by sqlite3.
        # Path.as_uri handles spaces and non-ASCII Windows home directories.
        uri = database.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            present = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            if not present:
                return 0, None
            row = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')"
            ).fetchone()
        return int(row[0]), None
    except (sqlite3.Error, OSError, ValueError):
        return None, "task_store_unavailable"
