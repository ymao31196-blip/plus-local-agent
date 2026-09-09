"""Local capabilities shared by MCP tool wrappers and the in-server agent."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


WORKSPACE = Path(
    os.environ.get("AGENT_WORKSPACE", r"D:\AI_Tools\plus-local-agent\workspace")
).resolve()

ALLOWED_PROGRAMS = {
    "python", "python.exe", "pytest", "pytest.exe", "git", "git.exe"
}

MAX_TEXT_CHARACTERS = 20_000


def truncate_text(value: str, limit: int = MAX_TEXT_CHARACTERS) -> dict[str, Any]:
    """Return bounded text without hiding whether content was discarded."""
    original_length = len(value)
    truncated = original_length > limit
    return {
        "content": value[-limit:] if truncated else value,
        "truncated": truncated,
        "original_length": original_length,
    }


def safe_path(path: str) -> Path:
    target = (WORKSPACE / path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"Path outside workspace is not allowed: {target}")
    return target


def _remove_cached_bytecode(target: Path) -> None:
    """Avoid stale pyc files after rapid same-size Python source edits."""
    if target.suffix != ".py":
        return
    cache_dir = target.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    for cached in cache_dir.glob(f"{target.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


def list_directory(path: str = ".") -> list[str]:
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not target.is_dir():
        raise ValueError(f"Not a directory: {path}")
    return [item.name + ("/" if item.is_dir() else "") for item in target.iterdir()]


def read_text(path: str, start_line: int = 1, end_line: int = 400) -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    lines = target.read_text(encoding="utf-8").splitlines()
    start = max(start_line - 1, 0)
    end = min(end_line, len(lines))
    rendered = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(lines[start:end], start=start + 1)
    )
    return {
        "path": str(target.relative_to(WORKSPACE)),
        **truncate_text(rendered),
    }


def write_text(path: str, content: str) -> dict[str, Any]:
    target = safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _remove_cached_bytecode(target)
    return {
        "path": str(target.relative_to(WORKSPACE)),
        "characters_written": len(content),
    }


def replace_text(path: str, old: str, new: str, count: int = 1) -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    text = target.read_text(encoding="utf-8")
    matches = text.count(old)
    if matches == 0:
        raise ValueError("Target text not found")
    target.write_text(text.replace(old, new, count), encoding="utf-8")
    _remove_cached_bytecode(target)
    return {
        "path": str(target.relative_to(WORKSPACE)),
        "matches_found": matches,
        "replacements": min(matches, count),
    }


def run_process(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 120,
) -> dict[str, Any]:
    args = [] if args is None else args
    program_name = Path(program).name.lower()
    if program_name not in ALLOWED_PROGRAMS:
        raise ValueError(f"Program not allowed: {program}")
    working_directory = safe_path(cwd)
    if not working_directory.exists():
        raise ValueError(f"Working directory does not exist: {cwd}")
    if not working_directory.is_dir():
        raise ValueError(f"Not a directory: {cwd}")
    timeout = max(1, min(timeout, 300))
    command = [program, *args]
    if program_name in {"pytest", "pytest.exe"}:
        command = [sys.executable, "-m", "pytest", *args]
    try:
        result = subprocess.run(
            command, cwd=working_directory, capture_output=True, text=True,
            timeout=timeout, shell=False,
        )
        stdout = truncate_text(result.stdout)
        stderr = truncate_text(result.stderr)
        return {
            "program": program,
            "args": args,
            "cwd": str(working_directory.relative_to(WORKSPACE)),
            "returncode": result.returncode,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = truncate_text(exc.stdout if isinstance(exc.stdout, str) else "")
        stderr = truncate_text(exc.stderr if isinstance(exc.stderr, str) else "")
        return {
            "program": program,
            "args": args,
            "cwd": str(working_directory.relative_to(WORKSPACE)),
            "timeout": True,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }


LOCAL_TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "list_directory": list_directory,
    "read_text": read_text,
    "write_text": write_text,
    "replace_text": replace_text,
    "run_process": run_process,
}
