"""Validation for independently re-runnable evidence specifications."""
from __future__ import annotations

from pathlib import PurePath
from typing import Any


def normalize_verification_spec(
    evidence_kind: str,
    artifact_sha256: str | None,
    verification: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if verification is None:
        return None
    if not isinstance(verification, dict):
        raise TypeError("verification must be an object or null")

    verification_type = verification.get("type")
    if verification_type == "file_sha256":
        if set(verification) != {"type", "path"}:
            raise ValueError("file_sha256 verification requires exactly type and path")
        if evidence_kind != "artifact":
            raise ValueError("file_sha256 verification is only valid for artifact evidence")
        if artifact_sha256 is None:
            raise ValueError("file_sha256 verification requires artifact_sha256")
        path = verification["path"]
        if not isinstance(path, str) or not path or len(path) > 4000 or "\x00" in path:
            raise ValueError("verification path must be a non-empty bounded string")
        candidate = PurePath(path)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ValueError("verification path must be project-relative without parent traversal")
        return {"type": "file_sha256", "path": path}

    if verification_type == "pytest":
        if set(verification) != {"type", "args", "cwd", "timeout"}:
            raise ValueError("pytest verification requires exactly type, args, cwd, and timeout")
        if evidence_kind != "test":
            raise ValueError("pytest verification is only valid for test evidence")
        args = verification["args"]
        cwd = verification["cwd"]
        timeout = verification["timeout"]
        if not isinstance(args, list) or len(args) > 64 or any(
            not isinstance(item, str) or len(item) > 4000 or "\x00" in item
            for item in args
        ):
            raise ValueError("pytest verification args must be at most 64 bounded strings")
        if not isinstance(cwd, str) or not cwd or len(cwd) > 4000 or "\x00" in cwd:
            raise ValueError("pytest verification cwd must be a non-empty bounded string")
        cwd_path = PurePath(cwd)
        if cwd_path.is_absolute() or any(part == ".." for part in cwd_path.parts):
            raise ValueError("pytest verification cwd must be project-relative without parent traversal")
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError("pytest verification timeout must be an integer between 1 and 300")
        return {
            "type": "pytest",
            "args": list(args),
            "cwd": cwd,
            "timeout": timeout,
        }

    raise ValueError(f"Unsupported verification type: {verification_type!r}")
