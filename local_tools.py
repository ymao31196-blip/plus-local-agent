"""Local capabilities shared by MCP tool wrappers and the in-server agent."""

from __future__ import annotations

import base64
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Timer, RLock
from functools import wraps

from runtime_context import CURRENT, checkpoint
from process_controller import controlled_run
from typing import Any, Callable


WORKSPACE = Path(
    os.environ.get("AGENT_WORKSPACE", r"D:\AI_Tools\plus-local-agent\workspace")
).resolve()

ALLOWED_PROGRAMS = {
    "python", "python.exe", "pytest", "pytest.exe", "git", "git.exe"
}

MAX_TEXT_CHARACTERS = 20_000
MAX_SEARCH_RESULTS = 1_000
MAX_ENV_OVERRIDES = 32
MAX_PATCH_CHARACTERS = 1_000_000
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)


class FileChangedSinceRead(RuntimeError):
    """Raised when an optional mutation precondition no longer matches."""


class PatchApplyError(ValueError):
    """Raised when a text patch cannot be applied exactly and safely."""


class PowerShellValidationError(ValueError):
    """Raised when a structured PowerShell request violates its policy."""


class PowerShellUnavailable(RuntimeError):
    """Raised when Windows PowerShell is not present at its system location."""


class SearchToolUnavailable(RuntimeError):
    """Raised when ripgrep is unavailable for search_text."""


def truncate_text(value: str, limit: int = MAX_TEXT_CHARACTERS) -> dict[str, Any]:
    """Return bounded text without hiding whether content was discarded."""
    original_length = getattr(value, "original_length", len(value))
    truncated = original_length > limit
    return {
        "content": str(value[-limit:] if truncated else value),
        "truncated": truncated,
        "original_length": original_length,
    }


def safe_path(path: str) -> Path:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    target = (WORKSPACE / path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"Path outside workspace is not allowed: {target}")
    return target


def _relative_path(target: Path) -> str:
    relative = target.relative_to(WORKSPACE)
    return "." if relative == Path(".") else relative.as_posix()


def _file_sha256(target: Path) -> str:
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_expected_sha256(target: Path, expected_sha256: str | None) -> None:
    if expected_sha256 is None:
        return
    if not isinstance(expected_sha256, str) or not SHA256_PATTERN.fullmatch(
        expected_sha256
    ):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal string")
    actual = _file_sha256(target) if target.is_file() else None
    if actual != expected_sha256.lower():
        raise FileChangedSinceRead(
            f"File hash precondition failed for {_relative_path(target)}"
        )


def _decode_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _decode_powershell_output(value: str | bytes | None) -> str:
    if not isinstance(value, bytes):
        return value or ""
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _remove_cached_bytecode(target: Path) -> None:
    """Avoid stale pyc files after rapid same-size Python source edits."""
    if target.suffix != ".py":
        return
    cache_dir = target.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    for cached in cache_dir.glob(f"{target.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", delete=False,
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp",
        ) as temporary:
            temporary.write(content)
            temporary_name = temporary.name
        os.replace(temporary_name, target)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


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
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    start = max(start_line - 1, 0)
    end = min(end_line, len(lines))
    rendered = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(lines[start:end], start=start + 1)
    )
    stat = target.stat()
    return {
        "path": _relative_path(target),
        **truncate_text(rendered),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size": stat.st_size,
    }


WORKSPACE_MUTATION_LOCK = RLock()


def workspace_mutation(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with WORKSPACE_MUTATION_LOCK:
            checkpoint()
            return function(*args, **kwargs)
    return locked


@workspace_mutation
def write_text(
    path: str, content: str, expected_sha256: str | None = None,
) -> dict[str, Any]:
    target = safe_path(path)
    _validate_expected_sha256(target, expected_sha256)
    _atomic_write_text(target, content)
    _remove_cached_bytecode(target)
    return {
        "path": _relative_path(target),
        "characters_written": len(content),
        "sha256": _file_sha256(target),
    }


@workspace_mutation
def replace_text(
    path: str,
    old: str,
    new: str,
    count: int = 1,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    _validate_expected_sha256(target, expected_sha256)
    text = target.read_text(encoding="utf-8")
    matches = text.count(old)
    if matches == 0:
        raise ValueError("Target text not found")
    updated = text.replace(old, new, count)
    _atomic_write_text(target, updated)
    _remove_cached_bytecode(target)
    return {
        "path": _relative_path(target),
        "matches_found": matches,
        "replacements": min(matches, count),
        "sha256": _file_sha256(target),
    }


def search_text(
    query: str,
    path: str = ".",
    glob: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 100,
) -> dict[str, Any]:
    """Search workspace text through ripgrep and return bounded line matches."""
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not isinstance(max_results, int) or isinstance(max_results, bool):
        raise TypeError("max_results must be an integer")
    if not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
    if glob is not None and (not isinstance(glob, str) or not glob):
        raise ValueError("glob must be a non-empty string when provided")
    if not isinstance(case_sensitive, bool):
        raise TypeError("case_sensitive must be a boolean")
    executable = shutil.which("rg")
    if executable is None:
        raise SearchToolUnavailable("ripgrep (rg) is required for search_text")

    command = [executable, "--json", "--color", "never", "--fixed-strings"]
    if not case_sensitive:
        command.append("--ignore-case")
    if glob is not None:
        command.extend(["--glob", glob])
    command.extend(["--", query, str(target)])

    process = subprocess.Popen(
        command, cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", shell=False,
    )
    context = CURRENT.get()
    if context:
        context.register(process)
    timed_out = [False]

    def kill_on_timeout() -> None:
        timed_out[0] = True
        process.kill()

    timer = Timer(30, kill_on_timeout)
    timer.start()
    matches: list[dict[str, Any]] = []
    truncated = False
    stderr = ""
    try:
        assert process.stdout is not None
        for line in process.stdout:
            event = json.loads(line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            raw_path = data["path"].get("text")
            if raw_path is None:
                continue
            matched_path = Path(raw_path).resolve()
            if matched_path != WORKSPACE and WORKSPACE not in matched_path.parents:
                process.kill()
                raise ValueError("ripgrep returned a path outside workspace")
            if len(matches) == max_results:
                truncated = True
                process.kill()
                break
            matches.append({
                "path": _relative_path(matched_path),
                "line": data["line_number"],
                "text": data["lines"].get("text", "").rstrip("\r\n"),
            })
        process.wait(timeout=5)
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        timer.cancel()
        if context:
            context.unregister(process)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    if timed_out[0]:
        raise TimeoutError("search_text exceeded its 30 second timeout")
    if not truncated and process.returncode not in {0, 1}:
        error = truncate_text(stderr)["content"].strip()
        raise RuntimeError(f"ripgrep failed: {error or process.returncode}")
    return {
        "status": "completed",
        "query": query,
        "path": _relative_path(target),
        "matches": matches,
        "match_count": len(matches),
        "truncated": truncated,
    }


def _validate_process_env(env: dict[str, str] | None) -> dict[str, str]:
    if env is None:
        return os.environ.copy()
    if not isinstance(env, dict):
        raise TypeError("env must be an object")
    if len(env) > MAX_ENV_OVERRIDES:
        raise ValueError(f"env cannot contain more than {MAX_ENV_OVERRIDES} overrides")
    result = os.environ.copy()
    blocked = {"PATH", "PATHEXT", "COMSPEC", "PYTHONHOME", "PYTHONPATH"}
    for name, value in env.items():
        if not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid environment variable name: {name!r}")
        if name.upper() in blocked:
            raise ValueError(f"Environment variable override is not allowed: {name}")
        if not isinstance(value, str):
            raise TypeError(f"Environment variable {name} must have a string value")
        if len(value) > 32_768:
            raise ValueError(f"Environment variable {name} value is too long")
        result[name] = value
    return result


def run_process(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout: int = 120,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> dict[str, Any]:
    args = [] if args is None else args
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise TypeError("args must be an array of strings")
    if stdin is not None and not isinstance(stdin, str):
        raise TypeError("stdin must be null or a string")
    if workdir is not None:
        if cwd != "." and cwd != workdir:
            raise ValueError("Provide either cwd or workdir, not conflicting values")
        cwd = workdir
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
        result = (controlled_run if CURRENT.get() else subprocess.run)(
            command, cwd=working_directory, capture_output=True, text=True,
            input=stdin, timeout=timeout, shell=False,
            env=_validate_process_env(env),
        )
        stdout = truncate_text(result.stdout)
        stderr = truncate_text(result.stderr)
        return {
            "program": program,
            "args": args,
            "cwd": _relative_path(working_directory),
            "returncode": result.returncode,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = truncate_text(_decode_process_output(exc.stdout))
        stderr = truncate_text(_decode_process_output(exc.stderr))
        return {
            "program": program,
            "args": args,
            "cwd": _relative_path(working_directory),
            "timeout": True,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }


POWERSHELL_PARAMETER_POLICY: dict[str, dict[str, str]] = {
    "Get-ChildItem": {
        "LiteralPath": "path", "Recurse": "bool", "File": "bool",
        "Directory": "bool", "Force": "bool", "Name": "bool", "Depth": "int",
    },
    "Get-Item": {"LiteralPath": "path", "Force": "bool"},
    "Test-Path": {"LiteralPath": "path", "PathType": "path_type"},
    "Get-Content": {
        "LiteralPath": "path", "Raw": "bool", "TotalCount": "int",
        "Tail": "int", "Encoding": "encoding",
    },
    "Select-String": {
        "LiteralPath": "path", "Pattern": "str", "SimpleMatch": "bool",
        "CaseSensitive": "bool", "AllMatches": "bool", "List": "bool",
        "NotMatch": "bool",
    },
    "Get-FileHash": {"LiteralPath": "path", "Algorithm": "hash_algorithm"},
    "Get-Process": {"Name": "str", "Id": "int"},
    "Get-Command": {"Name": "str"},
    "Get-Location": {},
    "Resolve-Path": {"LiteralPath": "path"},
    "Get-Date": {"Date": "str", "Format": "str"},
}

POWERSHELL_SCRIPT = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
[Console]::InputEncoding = New-Object Text.UTF8Encoding $false
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:PLUS_LOCAL_AGENT_PS_PAYLOAD))
$payload = $json | ConvertFrom-Json
$parameters = @{}
$payload.parameters.psobject.Properties | ForEach-Object { $parameters[$_.Name] = $_.Value }
$result = switch ($payload.command) {
    'Get-ChildItem' { Get-ChildItem @parameters }
    'Get-Item' { Get-Item @parameters }
    'Test-Path' { Test-Path @parameters }
    'Get-Content' { Get-Content @parameters }
    'Select-String' { Select-String @parameters }
    'Get-FileHash' { Get-FileHash @parameters }
    'Get-Process' { Get-Process @parameters }
    'Get-Command' { Get-Command @parameters }
    'Get-Location' { Get-Location @parameters }
    'Resolve-Path' { Resolve-Path @parameters }
    'Get-Date' { Get-Date @parameters }
    default { throw 'Command rejected by runtime allowlist' }
}
$result | Out-String -Width 240
"""


def _validate_powershell_parameters(
    command: str, parameters: dict[str, Any],
) -> dict[str, Any]:
    policy = POWERSHELL_PARAMETER_POLICY[command]
    result: dict[str, Any] = {}
    for name, value in parameters.items():
        kind = policy.get(name)
        if kind is None:
            raise PowerShellValidationError(
                f"Parameter not allowed for {command}: {name}"
            )
        if kind == "path":
            if not isinstance(value, str):
                raise PowerShellValidationError(f"{name} must be a string path")
            result[name] = str(safe_path(value))
        elif kind == "bool":
            if not isinstance(value, bool):
                raise PowerShellValidationError(f"{name} must be a boolean")
            result[name] = value
        elif kind == "int":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PowerShellValidationError(
                    f"{name} must be a non-negative integer"
                )
            result[name] = value
        elif kind == "str":
            if not isinstance(value, str):
                raise PowerShellValidationError(f"{name} must be a string")
            result[name] = value
        elif kind == "path_type":
            if value not in {"Any", "Container", "Leaf"}:
                raise PowerShellValidationError("PathType must be Any, Container, or Leaf")
            result[name] = value
        elif kind == "encoding":
            if value not in {"utf8", "utf8BOM", "unicode", "ascii", "default"}:
                raise PowerShellValidationError("Encoding is not allowed")
            result[name] = value
        elif kind == "hash_algorithm":
            if value not in {"SHA256", "SHA384", "SHA512"}:
                raise PowerShellValidationError("Algorithm is not allowed")
            result[name] = value
    return result


def _powershell_executable() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not executable.is_file():
        raise PowerShellUnavailable("Windows PowerShell 5.1 is unavailable")
    return executable


def run_powershell(
    command: str,
    parameters: dict[str, Any] | None = None,
    workdir: str = ".",
    timeout: int = 30,
) -> dict[str, Any]:
    """Run one allow-listed cmdlet from validated structured parameters."""
    if command not in POWERSHELL_PARAMETER_POLICY:
        raise PowerShellValidationError(f"PowerShell command not allowed: {command}")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise TypeError("parameters must be an object")
    working_directory = safe_path(workdir)
    if not working_directory.exists() or not working_directory.is_dir():
        raise ValueError(f"Working directory does not exist or is not a directory: {workdir}")
    validated = _validate_powershell_parameters(command, parameters)
    payload = base64.b64encode(json.dumps(
        {"command": command, "parameters": validated}, ensure_ascii=False,
    ).encode("utf-8")).decode("ascii")
    child_env = os.environ.copy()
    child_env["PLUS_LOCAL_AGENT_PS_PAYLOAD"] = payload
    encoded_script = base64.b64encode(POWERSHELL_SCRIPT.encode("utf-16-le")).decode("ascii")
    timeout = max(1, min(timeout, 300))
    process_args = [
        str(_powershell_executable()), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Restricted", "-EncodedCommand", encoded_script,
    ]
    try:
        completed = (controlled_run if CURRENT.get() else subprocess.run)(
            process_args, cwd=working_directory, capture_output=True,
            timeout=timeout, shell=False, env=child_env,
        )
        stdout = truncate_text(_decode_powershell_output(completed.stdout))
        stderr = truncate_text(_decode_powershell_output(completed.stderr))
        return {
            "status": "completed" if completed.returncode == 0 else "error",
            "command": command,
            "returncode": completed.returncode,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = truncate_text(_decode_powershell_output(exc.stdout))
        stderr = truncate_text(_decode_powershell_output(exc.stderr))
        return {
            "status": "error", "command": command, "timeout": True,
            "stdout": stdout["content"], "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }


def _patch_header_path(line: str) -> str:
    value = line[4:].split("\t", 1)[0]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _apply_unified_hunks(original: str, patch: str, expected_path: str) -> str:
    if not patch or len(patch) > MAX_PATCH_CHARACTERS:
        raise PatchApplyError("patch must be non-empty and at most 1,000,000 characters")
    if "\x00" in original or "\x00" in patch:
        raise PatchApplyError("binary file patches are not supported")
    patch_lines = patch.splitlines()
    index = 0
    if len(patch_lines) >= 2 and patch_lines[0].startswith("--- "):
        if not patch_lines[1].startswith("+++ "):
            raise PatchApplyError("unified diff is missing the +++ header")
        old_name = _patch_header_path(patch_lines[0])
        new_name = _patch_header_path(patch_lines[1])
        normalized = Path(expected_path).as_posix()
        if old_name != normalized or new_name != normalized:
            raise PatchApplyError("patch header path does not match the requested path")
        index = 2
    original_lines = original.splitlines()
    original_raw_lines = original.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in original else "\n"
    output: list[str] = []
    source_index = 0
    saw_hunk = False
    while index < len(patch_lines):
        header = HUNK_HEADER_PATTERN.match(patch_lines[index])
        if header is None:
            raise PatchApplyError(f"invalid patch hunk header: {patch_lines[index]!r}")
        saw_hunk = True
        old_start, old_count, new_start, new_count = header.groups()
        expected_old = int(old_count) if old_count is not None else 1
        expected_new = int(new_count) if new_count is not None else 1
        old_position = int(old_start) if expected_old == 0 else int(old_start) - 1
        if old_position < source_index or old_position > len(original_lines):
            raise PatchApplyError("patch hunk position is invalid or overlaps a prior hunk")
        output.extend(original_raw_lines[source_index:old_position])
        source_index = old_position
        expected_position = int(new_start) if expected_new == 0 else int(new_start) - 1
        if expected_position != len(output):
            raise PatchApplyError("patch new hunk position is invalid")
        consumed_old = 0
        produced_new = 0
        previous_marker = None
        index += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@ "):
            line = patch_lines[index]
            if line == r"\ No newline at end of file":
                if previous_marker is None:
                    raise PatchApplyError("newline marker must follow a patch content line")
                if previous_marker in {"-", " "}:
                    if source_index != len(original_lines) or original_raw_lines[source_index - 1].endswith(("\n", "\r")):
                        raise PatchApplyError("old newline marker does not match the source")
                if previous_marker in {"+", " "}:
                    output[-1] = output[-1].removesuffix(newline)
                previous_marker = None
                index += 1
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise PatchApplyError(f"invalid patch line: {line!r}")
            marker, content = line[0], line[1:]
            previous_marker = marker
            if marker in {" ", "-"}:
                if source_index >= len(original_lines) or original_lines[source_index] != content:
                    raise PatchApplyError("patch target content does not match")
                if marker == " ":
                    output.append(original_raw_lines[source_index])
                    produced_new += 1
                source_index += 1
                consumed_old += 1
            else:
                output.append(content + newline)
                produced_new += 1
            index += 1
        if consumed_old != expected_old or produced_new != expected_new:
            raise PatchApplyError("patch hunk line counts do not match its header")
    if not saw_hunk:
        raise PatchApplyError("patch contains no hunks")
    output.extend(original_raw_lines[source_index:])
    if any(not line.endswith(("\n", "\r")) for line in output[:-1]):
        raise PatchApplyError("no-newline marker is only valid at the end of the result")
    return "".join(output)


@workspace_mutation
def apply_patch(
    path: str, patch: str, expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Atomically apply a single-file unified text diff inside the workspace."""
    target = safe_path(path)
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    _validate_expected_sha256(target, expected_sha256)
    original = target.read_bytes().decode("utf-8")
    updated = _apply_unified_hunks(original, patch, _relative_path(target))
    _atomic_write_text(target, updated)
    _remove_cached_bytecode(target)
    return {
        "status": "completed",
        "path": _relative_path(target),
        "sha256": _file_sha256(target),
        "characters_before": len(original),
        "characters_after": len(updated),
        "changed": original != updated,
    }


def apply_changeset(changes: list[dict[str, str]]) -> dict[str, Any]:
    from changeset_manager import apply_changeset as execute
    return execute(changes)


LOCAL_TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "list_directory": list_directory,
    "read_text": read_text,
    "write_text": write_text,
    "replace_text": replace_text,
    "search_text": search_text,
    "run_process": run_process,
    "run_powershell": run_powershell,
    "apply_patch": apply_patch,
    "apply_changeset": apply_changeset,
}
