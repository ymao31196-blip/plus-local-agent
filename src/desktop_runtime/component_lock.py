"""Cross-process ownership lock for one Desktop private component installer.

The native GUI manager and ChatGPT Runtime must not install dependencies into
the same private environment concurrently. The lock is released by the OS if
its holding process crashes; no PID file or stale lock deletion is needed.
"""
from __future__ import annotations

import os
from pathlib import Path


class ComponentInstallLock:
    def __init__(self, private_data: Path):
        self.path = private_data.resolve() / "components" / "installer.lock"
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            raise ValueError("Component installer ownership already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise ValueError("Another Desktop component installation is running") from exc
        self._file = stream

    def release(self) -> None:
        stream = self._file
        self._file = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
