"""User-controlled executable grants shared by MCP Runtime and Execution Runner.

Only the Desktop's private manager may mutate this file. MCP tools are read-only
consumers. Grants are exact absolute executable paths, never basename wildcards.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

SCHEMA_VERSION = 1
MAX_PROGRAMS = 64
# Shell hosts remain outside the user-exe grant feature in this first phase.
SHELL_HOSTS = frozenset({"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe", "mshta.exe"})


def permissions_file() -> Path:
    override = os.environ.get("PLA_PROGRAM_PERMISSIONS_FILE")
    if override:
        return Path(override).expanduser().resolve()
    # Developer source installs have a private config path protected by RootPolicy.
    return (Path(__file__).resolve().parents[2] / "config" / "program_permissions.local.json").resolve()


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _normalized_executable(path: str, *, must_exist: bool) -> str:
    if not isinstance(path, str) or not path or len(path) > 1024 or path.startswith(("\\\\", "//")):
        raise ValueError("Select an absolute local executable path")
    raw = Path(path)
    if not raw.is_absolute() or raw.suffix.casefold() != ".exe":
        raise ValueError("Select an absolute .exe path")
    resolved = raw.resolve()
    if resolved.name.casefold() in SHELL_HOSTS:
        raise ValueError("Shell interpreters require a separate advanced permission policy")
    if must_exist and not resolved.is_file():
        raise ValueError("Executable does not exist")
    if resolved.suffix.casefold() != ".exe":
        raise ValueError("Executable must resolve to a local .exe file")
    return str(resolved)


def snapshot(path: Path | None = None) -> dict:
    path = permissions_file() if path is None else path
    if not path.is_file():
        return {"version": SCHEMA_VERSION, "programs": [], "config_sha256": None}
    if path.stat().st_size > 100_000:
        raise ValueError("Executable permission configuration is too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"version", "programs"} or payload["version"] != SCHEMA_VERSION:
        raise ValueError("Invalid executable permission configuration")
    records = payload["programs"]
    if not isinstance(records, list) or len(records) > MAX_PROGRAMS:
        raise ValueError("Invalid executable permission list")
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict) or set(item) != {"path", "enabled"} or type(item["enabled"]) is not bool:
            raise ValueError("Invalid executable permission entry")
        normalized = _normalized_executable(item["path"], must_exist=False)
        if normalized != item["path"]:
            raise ValueError("Executable permission path is not canonical")
        identity = os.path.normcase(normalized).casefold()
        if identity in seen:
            raise ValueError("Duplicate executable permission")
        seen.add(identity)
    return {"version": SCHEMA_VERSION, "programs": records, "config_sha256": _sha256(path)}


def executable_is_granted(program: str) -> bool:
    try:
        normalized = _normalized_executable(program, must_exist=True)
        identity = os.path.normcase(normalized).casefold()
        return any(
            item["enabled"] and os.path.normcase(item["path"]).casefold() == identity
            for item in snapshot()["programs"]
        )
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return False  # Invalid policy or changed path always fails closed.


def change_permission(
    operation: str,
    *,
    executable: str,
    expected_sha256: str | None,
    path: Path | None = None,
) -> dict:
    """Invoked exclusively by the Desktop private manager, never through MCP."""
    path = permissions_file() if path is None else path
    before = snapshot(path)
    if before["config_sha256"] != expected_sha256:
        raise ValueError("Executable permissions changed; refresh and retry")
    normalized = _normalized_executable(executable, must_exist=operation == "grant")
    identity = os.path.normcase(normalized).casefold()
    records = [dict(row) for row in before["programs"]]
    index = next(
        (i for i, row in enumerate(records) if os.path.normcase(row["path"]).casefold() == identity), None
    )
    if operation == "grant":
        if index is None:
            if len(records) >= MAX_PROGRAMS:
                raise ValueError("Executable permission limit reached")
            records.append({"path": normalized, "enabled": True})
        else:
            records[index]["enabled"] = True
    elif operation == "disable":
        if index is None:
            raise ValueError("Executable permission no longer exists")
        records[index]["enabled"] = False
    elif operation == "revoke":
        if index is None:
            raise ValueError("Executable permission no longer exists")
        records.pop(index)
    else:
        raise ValueError("Unsupported executable permission operation")
    content = json.dumps({"version": SCHEMA_VERSION, "programs": records}, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pla-program-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return snapshot(path)
