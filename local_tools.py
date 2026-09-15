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
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Timer, RLock
from functools import wraps
from xml.etree import ElementTree as ET

from runtime_context import CURRENT, checkpoint
from process_controller import controlled_run
from typing import Any, Callable, Literal
from typing_extensions import TypedDict
from workspace_manager import (
    RootPolicy,
    build_root_policy,
    load_workspace_roots,
    workspace_registry_remove,
    workspace_registry_status,
    workspace_registry_upsert,
)


PLA_ROOT = Path(
    os.environ.get("AGENT_PLA_ROOT", Path(__file__).resolve().parent)
).resolve()
WORKSPACE = Path(
    os.environ.get("AGENT_WORKSPACE", str(PLA_ROOT / "workspace"))
).resolve()
WORKSPACES_CONFIG_ENV = "AGENT_WORKSPACES_CONFIG"

def workspace_config_path() -> Path:
    configured = os.environ.get(WORKSPACES_CONFIG_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (PLA_ROOT / "config" / "workspaces.local.yaml").resolve()


ALLOWED_PROGRAMS = {
    "python", "python.exe", "pytest", "pytest.exe", "git", "git.exe"
}

MAX_TEXT_CHARACTERS = 20_000
MAX_DOCUMENT_CHARACTERS = 100_000
DOCUMENT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".json", ".csv", ".tsv",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".tex", ".rst", ".log",
}
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


def root_policy(root_hint: str | None = None) -> RootPolicy:
    """Build built-ins directly; load machine-local roots only when needed."""
    configured_roots = {}
    if root_hint not in {"workspace", "pla"}:
        configured_roots = load_workspace_roots(workspace_config_path(), PLA_ROOT)
    return build_root_policy(WORKSPACE, PLA_ROOT, configured_roots)


def available_roots() -> dict[str, dict[str, bool]]:
    return root_policy().capabilities()


def safe_path(path: str, root: str = "workspace", access: str = "read") -> Path:
    return root_policy(root).resolve(root, path, access).target


def _relative_path(target: Path, root: str = "workspace") -> str:
    return root_policy(root).resolve(root, str(target)).relative


def _file_sha256(target: Path) -> str:
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_expected_sha256(
    target: Path, expected_sha256: str | None, root: str = "workspace",
) -> None:
    if expected_sha256 is None:
        return
    if not isinstance(expected_sha256, str) or not SHA256_PATTERN.fullmatch(
        expected_sha256
    ):
        raise ValueError("expected_sha256 must be a 64-character hexadecimal string")
    actual = _file_sha256(target) if target.is_file() else None
    if actual != expected_sha256.lower():
        raise FileChangedSinceRead(
            f"File hash precondition failed for {_relative_path(target, root)}"
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


def list_directory(path: str = ".", root: str = "workspace") -> list[str]:
    target = safe_path(path, root)
    if not target.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not target.is_dir():
        raise ValueError(f"Not a directory: {path}")
    return [item.name + ("/" if item.is_dir() else "") for item in target.iterdir()]


def read_text(
    path: str, start_line: int = 1, end_line: int = 400,
    root: str = "workspace",
) -> dict[str, Any]:
    target = safe_path(path, root)
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
        "path": _relative_path(target, root),
        **truncate_text(rendered),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size": stat.st_size,
    }


def extract_document_text(
    path: str,
    start: int = 1,
    end: int | None = None,
    max_chars: int = MAX_TEXT_CHARACTERS,
    root: str = "workspace",
) -> dict[str, Any]:
    """Extract bounded text from supported local documents.

    Unit semantics depend on the file type: PDF pages, DOCX paragraphs, and
    plain-text lines. PDF extraction requires the optional pypdf package.
    """
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        raise ValueError("start must be an integer >= 1")
    if end is not None and (
        not isinstance(end, int) or isinstance(end, bool) or end < start
    ):
        raise ValueError("end must be null or an integer >= start")
    if (
        not isinstance(max_chars, int)
        or isinstance(max_chars, bool)
        or not 1 <= max_chars <= MAX_DOCUMENT_CHARACTERS
    ):
        raise ValueError(
            f"max_chars must be between 1 and {MAX_DOCUMENT_CHARACTERS}"
        )

    target = safe_path(path, root)
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")

    suffix = target.suffix.lower()
    unit_name: str
    unit_count: int
    selected_start: int | None
    selected_end: int | None
    rendered: str

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "PDF extraction requires pypdf in the plus-local-agent environment"
            ) from exc

        reader = PdfReader(str(target))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise ValueError("Encrypted PDF could not be opened") from exc
            if not unlocked:
                raise ValueError("Encrypted PDF requires a password")

        unit_name = "page"
        unit_count = len(reader.pages)
        if unit_count == 0:
            selected_start = selected_end = None
            rendered = ""
        else:
            if start > unit_count:
                raise ValueError(
                    f"start exceeds document page count ({unit_count})"
                )
            selected_start = start
            selected_end = min(end if end is not None else unit_count, unit_count)
            parts = []
            for page_number in range(selected_start, selected_end + 1):
                page_text = reader.pages[page_number - 1].extract_text() or ""
                parts.append(f"[Page {page_number}]\n{page_text.strip()}")
            rendered = "\n\n".join(parts)

        format_name = "pdf"

    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(target) as archive:
                document_xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX is missing word/document.xml") from exc
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid DOCX container") from exc

        namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        try:
            xml_root = ET.fromstring(document_xml)
        except ET.ParseError as exc:
            raise ValueError("DOCX document.xml is not valid XML") from exc

        paragraphs = []
        paragraph_tag = f"{{{namespace}}}p"
        text_tag = f"{{{namespace}}}t"
        for paragraph in xml_root.iter(paragraph_tag):
            value = "".join(node.text or "" for node in paragraph.iter(text_tag))
            if value.strip():
                paragraphs.append(value)

        unit_name = "paragraph"
        unit_count = len(paragraphs)
        if unit_count == 0:
            selected_start = selected_end = None
            rendered = ""
        else:
            if start > unit_count:
                raise ValueError(
                    f"start exceeds document paragraph count ({unit_count})"
                )
            selected_start = start
            selected_end = min(end if end is not None else unit_count, unit_count)
            rendered = "\n".join(paragraphs[selected_start - 1:selected_end])

        format_name = "docx"

    elif suffix in DOCUMENT_TEXT_EXTENSIONS:
        try:
            text_value = target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Plain-text document is not valid UTF-8") from exc

        lines = text_value.splitlines()
        unit_name = "line"
        unit_count = len(lines)
        if unit_count == 0:
            selected_start = selected_end = None
            rendered = ""
        else:
            if start > unit_count:
                raise ValueError(f"start exceeds document line count ({unit_count})")
            selected_start = start
            selected_end = min(end if end is not None else unit_count, unit_count)
            rendered = "\n".join(lines[selected_start - 1:selected_end])

        format_name = "text"

    else:
        raise ValueError(
            "Unsupported document type. Supported: PDF, DOCX, and UTF-8 text formats"
        )

    original_length = len(rendered)
    content = rendered[:max_chars]
    stat = target.stat()
    return {
        "path": _relative_path(target, root),
        "format": format_name,
        "unit": unit_name,
        "unit_count": unit_count,
        "selected_start": selected_start,
        "selected_end": selected_end,
        "has_more_units": bool(
            selected_end is not None and selected_end < unit_count
        ),
        "content": content,
        "truncated": original_length > max_chars,
        "original_length": original_length,
        "sha256": _file_sha256(target),
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


def workspace_roots_get() -> dict[str, Any]:
    return workspace_registry_status(workspace_config_path(), PLA_ROOT)


@workspace_mutation
def workspace_root_upsert(
    name: str,
    path: str,
    read: bool,
    write: bool,
    execute: bool,
    expected_sha256: str | None,
) -> dict[str, Any]:
    return workspace_registry_upsert(
        workspace_config_path(),
        PLA_ROOT,
        name=name,
        path=path,
        read=read,
        write=write,
        execute=execute,
        expected_sha256=expected_sha256,
    )


@workspace_mutation
def workspace_root_remove(
    name: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    return workspace_registry_remove(
        workspace_config_path(),
        PLA_ROOT,
        name=name,
        expected_sha256=expected_sha256,
    )


@workspace_mutation
def write_text(
    path: str, content: str, expected_sha256: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    target = safe_path(path, root, "write")
    _validate_expected_sha256(target, expected_sha256, root)
    _atomic_write_text(target, content)
    _remove_cached_bytecode(target)
    return {
        "path": _relative_path(target, root),
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
    root: str = "workspace",
) -> dict[str, Any]:
    target = safe_path(path, root, "write")
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    _validate_expected_sha256(target, expected_sha256, root)
    text = target.read_text(encoding="utf-8")
    matches = text.count(old)
    if matches == 0:
        raise ValueError("Target text not found")
    updated = text.replace(old, new, count)
    _atomic_write_text(target, updated)
    _remove_cached_bytecode(target)
    return {
        "path": _relative_path(target, root),
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
    root: str = "workspace",
) -> dict[str, Any]:
    """Search workspace text through ripgrep and return bounded line matches."""
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    target = safe_path(path, root)
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
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        truncated = False
        deadline = datetime.now(timezone.utc).timestamp() + 30

        def fallback_files():
            if target.is_file():
                yield target
                return
            for directory, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = [name for name in dirnames if not name.startswith(".")]
                for filename in filenames:
                    if not filename.startswith("."):
                        yield Path(directory) / filename

        for candidate in fallback_files():
            if datetime.now(timezone.utc).timestamp() > deadline:
                raise TimeoutError("search_text exceeded its 30 second timeout")
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            try:
                root_policy(root).resolve(root, str(resolved))
            except ValueError:
                continue
            relative = _relative_path(resolved, root)
            if glob is not None and not Path(relative).match(glob):
                continue
            try:
                handle = resolved.open("r", encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            with handle:
                for line_number, line in enumerate(handle, start=1):
                    haystack = line if case_sensitive else line.casefold()
                    if needle not in haystack:
                        continue
                    if len(matches) == max_results:
                        truncated = True
                        break
                    matches.append({
                        "path": relative,
                        "line": line_number,
                        "text": line.rstrip("\r\n"),
                    })
            if truncated:
                break
        return {
            "status": "completed",
            "query": query,
            "path": _relative_path(target, root),
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
        }

    command = [executable, "--json", "--color", "never", "--fixed-strings"]
    if not case_sensitive:
        command.append("--ignore-case")
    if glob is not None:
        command.extend(["--glob", glob])
    command.extend(["--", query, str(target)])

    process = subprocess.Popen(
        command, cwd=root_policy(root).resolve(root, ".").target,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
            try:
                root_policy(root).resolve(root, str(matched_path))
            except ValueError:
                process.kill()
                raise ValueError(f"ripgrep returned a path outside root {root!r}")
            if len(matches) == max_results:
                truncated = True
                process.kill()
                break
            matches.append({
                "path": _relative_path(matched_path, root),
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
        "path": _relative_path(target, root),
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
    root: str = "workspace",
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
    if root == "pla" and program_name in {"git", "git.exe"}:
        raise ValueError("Git execution is not allowed through root 'pla'")
    working_directory = safe_path(cwd, root, "execute")
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
            "cwd": _relative_path(working_directory, root),
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
            "cwd": _relative_path(working_directory, root),
            "timeout": True,
            "stdout": stdout["content"],
            "stderr": stderr["content"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_original_length": stdout["original_length"],
            "stderr_original_length": stderr["original_length"],
        }


def _git_run(
    working_directory: Path,
    args: list[str],
    *,
    timeout: int = 30,
    allow_failure: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git executable is unavailable")
    command = [
        executable,
        "-c", "core.fsmonitor=false",
        "-c", "core.quotepath=false",
        *args,
    ]
    common = {
        "cwd": working_directory,
        "capture_output": True,
        "text": True,
        "timeout": max(1, min(timeout, 60)),
        "shell": False,
        "env": env,
    }
    if CURRENT.get():
        result = controlled_run(command, **common)
    else:
        result = subprocess.run(
            command, **common, encoding="utf-8", errors="replace"
        )
    if result.returncode != 0 and not allow_failure:
        message = (result.stderr or result.stdout).strip()
        if not message:
            message = f"git exited with code {result.returncode}"
        raise ValueError(message[:MAX_TEXT_CHARACTERS])
    return result


def _git_repo_context(cwd: str, root: str) -> tuple[Path, Path]:
    working_directory = safe_path(cwd, root, "execute")
    if not working_directory.exists():
        raise ValueError(f"Working directory does not exist: {cwd}")
    if not working_directory.is_dir():
        raise ValueError(f"Not a directory: {cwd}")
    probe = _git_run(
        working_directory,
        ["rev-parse", "--show-toplevel"],
        allow_failure=True,
    )
    if probe.returncode != 0:
        raise ValueError("Working directory is not inside a Git repository")
    repo_root = Path(probe.stdout.strip()).resolve()
    try:
        root_policy(root).resolve(root, str(repo_root), "read")
    except ValueError as exc:
        raise ValueError("Git repository root is outside the selected root") from exc
    return working_directory, repo_root


def _git_resolve_commit(working_directory: Path, revision: str) -> str:
    if not isinstance(revision, str) or not revision:
        raise ValueError("revision must be a non-empty string")
    if len(revision) > 256:
        raise ValueError("revision cannot exceed 256 characters")
    if revision.startswith("-") or "\x00" in revision or "\n" in revision or "\r" in revision:
        raise ValueError("revision is not allowed")
    result = _git_run(
        working_directory,
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        allow_failure=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Revision does not resolve to a commit: {revision}")
    resolved = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", resolved):
        raise ValueError("Git returned an invalid commit id")
    return resolved.lower()


def git_status(cwd: str = ".", root: str = "workspace") -> dict[str, Any]:
    """Return structured read-only Git status for a repository inside an allowed root."""
    working_directory, repo_root = _git_repo_context(cwd, root)

    branch_result = _git_run(
        working_directory,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        allow_failure=True,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

    head_result = _git_run(
        working_directory,
        ["rev-parse", "--verify", "HEAD"],
        allow_failure=True,
    )
    head = head_result.stdout.strip() if head_result.returncode == 0 else None

    upstream_result = _git_run(
        working_directory,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        allow_failure=True,
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
    ahead = behind = None
    if upstream:
        counts = _git_run(
            working_directory,
            ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"],
        ).stdout.strip().split()
        if len(counts) == 2:
            ahead, behind = int(counts[0]), int(counts[1])

    porcelain_result = _git_run(
        working_directory,
        [
            "-c", "status.relativePaths=false",
            "status", "--porcelain=v1", "-z", "--untracked-files=normal",
        ],
    )
    porcelain = porcelain_result.stdout
    if getattr(porcelain, "original_length", len(porcelain)) > len(porcelain):
        raise ValueError("Git status output exceeds the bounded process capture limit")

    records = porcelain.split("\0")
    files: list[dict[str, Any]] = []
    total_files = 0
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("Unexpected git status porcelain record")
        code = record[:2]
        path = record[3:]
        item: dict[str, Any] = {
            "path": path,
            "code": code,
            "index_status": code[0],
            "worktree_status": code[1],
        }
        if code[0] in {"R", "C"} or code[1] in {"R", "C"}:
            if index >= len(records) or not records[index]:
                raise ValueError("Malformed rename/copy status record")
            item["original_path"] = records[index]
            index += 1
        total_files += 1
        if len(files) < MAX_SEARCH_RESULTS:
            files.append(item)

    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "branch": branch,
        "detached": branch is None,
        "head": head,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "clean": total_files == 0,
        "file_count": total_files,
        "files": files,
        "truncated": total_files > len(files),
    }


def git_diff(
    cwd: str = ".",
    staged: bool = False,
    path: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    """Return bounded read-only Git diff without external diff or textconv execution."""
    if not isinstance(staged, bool):
        raise TypeError("staged must be a boolean")
    if path is not None and (not isinstance(path, str) or not path):
        raise ValueError("path must be a non-empty string when provided")

    working_directory, repo_root = _git_repo_context(cwd, root)
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if staged:
        args.append("--cached")

    rendered_path = None
    if path is not None:
        target = safe_path(path, root, "read")
        try:
            repo_relative = target.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError("Diff path is outside the selected Git repository") from exc
        rendered_path = _relative_path(target, root)
        args.extend(["--", repo_relative.as_posix()])

    result = _git_run(working_directory, args)
    diff = truncate_text(result.stdout)
    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "staged": staged,
        "path": rendered_path,
        "diff": diff["content"],
        "truncated": diff["truncated"],
        "original_length": diff["original_length"],
    }


def git_log(
    cwd: str = ".",
    limit: int = 20,
    path: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    """Return structured recent commit history without executing repository helpers."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if path is not None and (not isinstance(path, str) or not path):
        raise ValueError("path must be a non-empty string when provided")

    working_directory, repo_root = _git_repo_context(cwd, root)
    rendered_path = None
    args = [
        "log",
        f"--max-count={limit + 1}",
        "--date=iso-strict",
        "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s%x1e",
    ]
    if path is not None:
        target = safe_path(path, root, "read")
        try:
            repo_relative = target.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError("Log path is outside the selected Git repository") from exc
        rendered_path = _relative_path(target, root)
        args.extend(["--", repo_relative.as_posix()])

    result = _git_run(working_directory, args)
    entries: list[dict[str, Any]] = []
    for raw_record in result.stdout.split("\x1e"):
        record = raw_record.strip("\r\n")
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) != 7:
            raise ValueError("Unexpected git log record")
        commit, parents, author_name, author_email, authored_at, committed_at, subject = fields
        entries.append({
            "commit": commit,
            "parents": parents.split() if parents else [],
            "author_name": author_name,
            "author_email": author_email,
            "authored_at": authored_at,
            "committed_at": committed_at,
            "subject": subject,
        })

    truncated = len(entries) > limit
    entries = entries[:limit]
    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "path": rendered_path,
        "limit": limit,
        "entry_count": len(entries),
        "entries": entries,
        "truncated": truncated,
    }


def git_show(
    revision: str = "HEAD",
    cwd: str = ".",
    path: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    """Return commit metadata and a bounded patch for one verified commit."""
    if path is not None and (not isinstance(path, str) or not path):
        raise ValueError("path must be a non-empty string when provided")
    working_directory, repo_root = _git_repo_context(cwd, root)
    commit = _git_resolve_commit(working_directory, revision)

    metadata_result = _git_run(
        working_directory,
        [
            "show", "-s", "--date=iso-strict",
            "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s",
            commit,
        ],
    )
    fields = metadata_result.stdout.rstrip("\r\n").split("\x1f")
    if len(fields) != 7:
        raise ValueError("Unexpected git show metadata")
    shown_commit, parents, author_name, author_email, authored_at, committed_at, subject = fields

    args = [
        "show", "--format=", "--no-ext-diff", "--no-textconv", "--no-color",
        commit,
    ]
    rendered_path = None
    if path is not None:
        target = safe_path(path, root, "read")
        try:
            repo_relative = target.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError("Show path is outside the selected Git repository") from exc
        rendered_path = _relative_path(target, root)
        args.extend(["--", repo_relative.as_posix()])

    patch_result = _git_run(working_directory, args)
    patch = truncate_text(patch_result.stdout)
    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "revision": revision,
        "commit": shown_commit,
        "parents": parents.split() if parents else [],
        "author_name": author_name,
        "author_email": author_email,
        "authored_at": authored_at,
        "committed_at": committed_at,
        "subject": subject,
        "path": rendered_path,
        "patch": patch["content"],
        "truncated": patch["truncated"],
        "original_length": patch["original_length"],
    }


class GitStageRequest(TypedDict):
    path: str
    expected_sha256: str


class AcceptanceCheckRequest(TypedDict):
    id: str
    description: str
    evidence_kinds: list[str]


class AcceptanceBindingRequest(TypedDict):
    check_id: str
    evidence_ids: list[str]


class FileSha256Verification(TypedDict):
    type: Literal["file_sha256"]
    path: str


class PytestVerification(TypedDict):
    type: Literal["pytest"]
    args: list[str]
    cwd: str
    timeout: int


VerificationSpec = FileSha256Verification | PytestVerification


@workspace_mutation
def git_stage(
    changes: list[GitStageRequest],
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    """Stage exact current bytes for explicit tracked or new regular files without clean filters."""
    if not isinstance(changes, list) or not 1 <= len(changes) <= 64:
        raise ValueError("changes must contain 1–64 explicit files")
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_head):
        raise ValueError("expected_head must be a full 40–64 character hexadecimal commit id")
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "expected_sha256"}:
            raise ValueError("Each stage change requires exactly path and expected_sha256")
        if not isinstance(change["path"], str) or not change["path"]:
            raise ValueError("each stage path must be a non-empty string")
        expected = change["expected_sha256"]
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            raise ValueError("expected_sha256 is required and must be 64 hexadecimal characters")

    working_directory, repo_root = _git_repo_context(cwd, root)
    root_base = root_policy(root).resolve(root, ".", "write").target.resolve()
    git_dir = Path(
        _git_run(working_directory, ["rev-parse", "--absolute-git-dir"]).stdout.strip()
    ).resolve()
    try:
        common = os.path.commonpath(
            (os.path.normcase(str(root_base)), os.path.normcase(str(git_dir)))
        )
    except ValueError as exc:
        raise ValueError("Git metadata directory is outside the selected root") from exc
    if common != os.path.normcase(str(root_base)):
        raise ValueError("Git metadata directory is outside the selected root")

    current_head = _git_resolve_commit(working_directory, "HEAD")
    if current_head != expected_head.lower():
        raise ValueError(
            f"HEAD changed since inspection: expected {expected_head.lower()}, current {current_head}"
        )
    for marker in (
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
        "rebase-merge", "rebase-apply", "sequencer",
    ):
        if (git_dir / marker).exists():
            raise ValueError(f"Structured stage is disabled while repository state {marker} exists")

    index_path = git_dir / "index"
    if not index_path.is_file():
        raise ValueError("Structured stage requires an existing Git index")
    lock_path = git_dir / "index.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Git index is locked by another operation") from exc

    candidate_path: Path | None = None
    lock_open = True
    try:
        snapshot = index_path.read_bytes()
        snapshot_sha = hashlib.sha256(snapshot).hexdigest()
        if _git_resolve_commit(working_directory, "HEAD") != current_head:
            raise ValueError("HEAD changed while acquiring the Git index lock")

        already_staged = [
            item
            for item in _git_run(
                working_directory,
                ["diff", "--cached", "--name-only", "-z", current_head],
            ).stdout.split("\x00")
            if item
        ]
        if already_staged:
            raise ValueError(
                "Git index already contains staged changes; commit or clear them before structured staging"
            )

        prepared: list[tuple[str, str, str, Path, str]] = []
        seen: set[str] = set()
        for change in changes:
            path = change["path"]
            target = safe_path(path, root, "read")
            if not target.exists() or not target.is_file():
                raise ValueError(f"Structured stage v1 requires an existing regular file: {path}")
            try:
                repo_relative = target.relative_to(repo_root)
            except ValueError as exc:
                raise ValueError(f"Stage path is outside the selected Git repository: {path}") from exc
            repo_path = repo_relative.as_posix()
            if repo_path in {"", "."}:
                raise ValueError("Stage paths must name explicit files, not the repository root")
            if repo_path in seen:
                raise ValueError(f"Duplicate stage path: {path}")
            seen.add(repo_path)
            _validate_expected_sha256(target, change["expected_sha256"], root)

            entry_result = _git_run(
                working_directory,
                ["ls-files", "--stage", "-z", "--", repo_path],
            )
            entries = [item for item in entry_result.stdout.split("\x00") if item]
            if entries:
                if len(entries) != 1 or "\t" not in entries[0]:
                    raise ValueError(
                        f"Structured stage supports only non-conflicted regular files: {path}"
                    )
                metadata, listed_path = entries[0].split("\t", 1)
                fields = metadata.split()
                if len(fields) != 3 or fields[2] != "0" or listed_path != repo_path:
                    raise ValueError(
                        f"Structured stage supports only non-conflicted regular files: {path}"
                    )
                mode, _old_oid, _stage = fields
                if mode not in {"100644", "100755"}:
                    raise ValueError(
                        f"Structured stage supports only regular files: {path}"
                    )
            else:
                ignored = _git_run(
                    working_directory,
                    ["check-ignore", "--quiet", "--", repo_path],
                    allow_failure=True,
                )
                if ignored.returncode == 0:
                    raise ValueError(
                        f"Structured stage refuses ignored untracked files: {path}"
                    )
                mode = (
                    "100644"
                    if os.name == "nt" or not (target.stat().st_mode & 0o111)
                    else "100755"
                )
            prepared.append(
                (repo_path, mode, change["expected_sha256"].lower(), target, _relative_path(target, root))
            )

        with tempfile.NamedTemporaryFile(
            dir=git_dir, prefix=".pla-stage-index-", suffix=".tmp", delete=False
        ) as candidate:
            candidate.write(snapshot)
            candidate.flush()
            os.fsync(candidate.fileno())
            candidate_path = Path(candidate.name)

        candidate_env = os.environ.copy()
        candidate_env["GIT_INDEX_FILE"] = str(candidate_path)
        update_args = ["update-index", "--add"]
        object_ids: dict[str, str] = {}
        for repo_path, mode, _expected, _target, _rendered in prepared:
            oid = _git_run(
                working_directory,
                ["hash-object", "-w", "--no-filters", "--", repo_path],
            ).stdout.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
                raise ValueError("Git returned an invalid blob id")
            object_ids[repo_path] = oid
            update_args.extend(["--cacheinfo", f"{mode},{oid},{repo_path}"])

        _git_run(working_directory, update_args, env=candidate_env)
        staged_paths = [
            item
            for item in _git_run(
                working_directory,
                ["diff", "--cached", "--name-only", "-z", current_head],
                env=candidate_env,
            ).stdout.split("\x00")
            if item
        ]
        expected_paths = [item[0] for item in prepared]
        if set(staged_paths) != set(expected_paths) or len(staged_paths) != len(expected_paths):
            raise ValueError(
                "Structured stage requires every selected file to differ from HEAD and no other staged paths"
            )

        tree = _git_run(working_directory, ["write-tree"], env=candidate_env).stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise ValueError("Git returned an invalid tree id")

        for _repo_path, _mode, expected, target, _rendered in prepared:
            _validate_expected_sha256(target, expected, root)
        if _git_resolve_commit(working_directory, "HEAD") != current_head:
            raise ValueError("HEAD changed during structured stage; Git index was not replaced")
        if hashlib.sha256(index_path.read_bytes()).hexdigest() != snapshot_sha:
            raise ValueError("Git index changed outside its lock; refusing to replace it")

        candidate_bytes = candidate_path.read_bytes()
        with os.fdopen(lock_fd, "wb", closefd=False) as lock_handle:
            lock_handle.write(candidate_bytes)
            lock_handle.flush()
            os.fsync(lock_handle.fileno())
        os.close(lock_fd)
        lock_open = False
        os.replace(lock_path, index_path)

        actual_staged = [
            item
            for item in _git_run(
                working_directory,
                ["diff", "--cached", "--name-only", "-z", current_head],
            ).stdout.split("\x00")
            if item
        ]
        if set(actual_staged) != set(expected_paths) or len(actual_staged) != len(expected_paths):
            raise RuntimeError("Structured stage could not verify the installed Git index")

        return {
            "status": "completed",
            "repo_root": _relative_path(repo_root, root),
            "cwd": _relative_path(working_directory, root),
            "head": current_head,
            "tree": tree,
            "paths": [item[4] for item in prepared],
            "file_count": len(prepared),
            "blobs": {
                item[4]: object_ids[item[0]]
                for item in prepared
            },
            "preflight": {
                "expected_head_matched": True,
                "expected_sha256_matched": True,
                "index_initially_clean": True,
                "regular_files_only": True,
                "new_files_allowed": True,
                "clean_filters_disabled": True,
                "index_lock_acquired": True,
                "mode": "raw_bytes_explicit_files",
            },
        }
    finally:
        if lock_open:
            os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)
@workspace_mutation
def git_commit(
    message: str,
    paths: list[str],
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    """Commit an exact already-staged explicit-file set with an atomic HEAD compare-and-swap."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    if len(message) > MAX_TEXT_CHARACTERS or "\x00" in message:
        raise ValueError(f"message must be at most {MAX_TEXT_CHARACTERS} characters and contain no NUL")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
        raise ValueError("paths must contain 1–64 explicit file paths")
    if any(not isinstance(path, str) or not path for path in paths):
        raise ValueError("each commit path must be a non-empty string")
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_head):
        raise ValueError("expected_head must be a full 40–64 character hexadecimal commit id")

    working_directory, repo_root = _git_repo_context(cwd, root)
    root_base = root_policy(root).resolve(root, ".", "write").target.resolve()
    git_dir_result = _git_run(working_directory, ["rev-parse", "--absolute-git-dir"])
    git_dir = Path(git_dir_result.stdout.strip()).resolve()
    try:
        common = os.path.commonpath((os.path.normcase(str(root_base)), os.path.normcase(str(git_dir))))
    except ValueError as exc:
        raise ValueError("Git metadata directory is outside the selected root") from exc
    if common != os.path.normcase(str(root_base)):
        raise ValueError("Git metadata directory is outside the selected root")

    branch_result = _git_run(
        working_directory, ["symbolic-ref", "--quiet", "HEAD"], allow_failure=True,
    )
    if branch_result.returncode != 0:
        raise ValueError("Structured commit requires a named branch; detached HEAD is not allowed")
    branch_ref = branch_result.stdout.strip()
    if not branch_ref.startswith("refs/heads/"):
        raise ValueError("HEAD does not point to a local branch")
    branch = branch_ref.removeprefix("refs/heads/")

    current_head = _git_resolve_commit(working_directory, "HEAD")
    if current_head != expected_head.lower():
        raise ValueError(
            f"HEAD changed since inspection: expected {expected_head.lower()}, current {current_head}"
        )

    for marker in (
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
        "rebase-merge", "rebase-apply", "sequencer",
    ):
        if (git_dir / marker).exists():
            raise ValueError(f"Structured commit is disabled while repository state {marker} exists")

    repo_paths: list[str] = []
    rendered_paths: list[str] = []
    seen: set[str] = set()
    for path in paths:
        target = safe_path(path, root, "read")
        try:
            repo_relative = target.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f"Commit path is outside the selected Git repository: {path}") from exc
        repo_path = repo_relative.as_posix()
        if repo_path in {"", "."}:
            raise ValueError("Commit paths must name explicit files, not the repository root")
        if repo_path in seen:
            raise ValueError(f"Duplicate commit path: {path}")
        seen.add(repo_path)
        repo_paths.append(repo_path)
        rendered_paths.append(_relative_path(target, root))

    staged_result = _git_run(
        working_directory,
        ["diff", "--cached", "--name-only", "-z", current_head],
    )
    staged_paths = [item for item in staged_result.stdout.split("\x00") if item]
    if set(staged_paths) != set(repo_paths) or len(staged_paths) != len(repo_paths):
        raise ValueError(
            "Staged paths must exactly match paths; stage only the intended explicit files before committing"
        )

    disallowed_result = _git_run(
        working_directory,
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=CDRTUXB", current_head],
    )
    disallowed = [item for item in disallowed_result.stdout.split("\x00") if item]
    if disallowed:
        raise ValueError(
            "Structured commit supports only explicit regular-file additions or modifications; "
            f"unsupported staged paths: {', '.join(disallowed[:10])}"
        )

    unstaged_result = _git_run(
        working_directory,
        ["diff", "--name-only", "-z", "--", *repo_paths],
    )
    unstaged = [item for item in unstaged_result.stdout.split("\x00") if item]
    if unstaged:
        raise ValueError(
            "Selected paths still have unstaged changes; stage the exact intended content before committing"
        )

    tree = _git_run(working_directory, ["write-tree"]).stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise ValueError("Git returned an invalid tree id")
    tree_paths_result = _git_run(
        working_directory,
        ["diff", "--name-only", "-z", current_head, tree],
    )
    tree_paths = [item for item in tree_paths_result.stdout.split("\x00") if item]
    if set(tree_paths) != set(repo_paths) or len(tree_paths) != len(repo_paths):
        raise RuntimeError("Git index changed during commit preflight; no branch ref was updated")

    commit = _git_run(
        working_directory,
        ["commit-tree", tree, "-p", current_head, "-m", message],
    ).stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("Git returned an invalid commit id")

    update = _git_run(
        working_directory,
        ["update-ref", "-m", "PLA structured commit", branch_ref, commit, current_head],
        allow_failure=True,
    )
    if update.returncode != 0:
        raise ValueError(
            "HEAD changed while committing; branch ref was not updated. "
            "An unreachable commit object may remain and can be garbage-collected by Git."
        )
    if _git_resolve_commit(working_directory, "HEAD") != commit:
        raise RuntimeError("Structured commit could not verify the updated HEAD")

    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "branch": branch,
        "previous_head": current_head,
        "commit": commit,
        "tree": tree,
        "subject": message.splitlines()[0],
        "paths": rendered_paths,
        "file_count": len(rendered_paths),
        "preflight": {
            "expected_head_matched": True,
            "staged_paths_exact": True,
            "selected_worktree_clean": True,
            "repository_state": "normal",
            "mode": "staged_explicit_files",
        },
    }



def _validate_git_ref_component(value: str, label: str, prefix: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{label} must be a non-empty string up to 128 characters")
    if value.startswith("-") or any(ch in value for ch in ("\x00", "\n", "\r")):
        raise ValueError(f"{label} is not allowed")
    probe = f"{prefix}/{value}"
    return probe


def _git_release_preflight(
    working_directory: Path,
    expected_head: str,
) -> tuple[str, str]:
    if not isinstance(expected_head, str) or not re.fullmatch(
        r"[0-9a-fA-F]{40,64}", expected_head
    ):
        raise ValueError(
            "expected_head must be a full 40–64 character hexadecimal commit id"
        )

    branch_result = _git_run(
        working_directory,
        ["symbolic-ref", "--quiet", "HEAD"],
        allow_failure=True,
    )
    if branch_result.returncode != 0:
        raise ValueError("Release Git operations require a named branch")
    branch_ref = branch_result.stdout.strip()
    if not branch_ref.startswith("refs/heads/"):
        raise ValueError("HEAD does not point to a local branch")
    branch = branch_ref.removeprefix("refs/heads/")

    current_head = _git_resolve_commit(working_directory, "HEAD")
    if current_head != expected_head.lower():
        raise ValueError(
            f"HEAD changed since inspection: expected {expected_head.lower()}, current {current_head}"
        )

    status = _git_run(
        working_directory,
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
    )
    if status.stdout:
        raise ValueError(
            "Release Git operations require a clean worktree and clean index"
        )

    git_dir = Path(
        _git_run(
            working_directory,
            ["rev-parse", "--absolute-git-dir"],
        ).stdout.strip()
    ).resolve()
    for marker in (
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
        "rebase-merge", "rebase-apply", "sequencer",
    ):
        if (git_dir / marker).exists():
            raise ValueError(
                f"Release Git operations are disabled while repository state {marker} exists"
            )

    return current_head, branch


@workspace_mutation
def git_tag(
    tag: str,
    expected_head: str,
    cwd: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    """Atomically create one lightweight tag at the exact clean expected HEAD."""
    working_directory, repo_root = _git_repo_context(cwd, root)
    current_head, branch = _git_release_preflight(
        working_directory,
        expected_head,
    )

    tag_ref = _validate_git_ref_component(tag, "tag", "refs/tags")
    check = _git_run(
        working_directory,
        ["check-ref-format", tag_ref],
        allow_failure=True,
    )
    if check.returncode != 0:
        raise ValueError(f"Invalid Git tag name: {tag}")

    exists = _git_run(
        working_directory,
        ["show-ref", "--verify", "--quiet", tag_ref],
        allow_failure=True,
    )
    if exists.returncode == 0:
        raise ValueError(f"Git tag already exists: {tag}")

    zero_oid = "0" * len(current_head)
    update = _git_run(
        working_directory,
        ["update-ref", "-m", "PLA controlled release tag", tag_ref, current_head, zero_oid],
        allow_failure=True,
    )
    if update.returncode != 0:
        message = (update.stderr or update.stdout).strip()
        raise ValueError(
            (message or f"Failed to create Git tag: {tag}")[:MAX_TEXT_CHARACTERS]
        )

    resolved = _git_resolve_commit(working_directory, tag_ref)
    if resolved != current_head:
        raise RuntimeError("Controlled Git tag verification failed")

    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "branch": branch,
        "head": current_head,
        "tag": tag,
        "tag_ref": tag_ref,
        "tag_commit": resolved,
        "preflight": {
            "expected_head_matched": True,
            "worktree_clean": True,
            "index_clean": True,
            "repository_state": "normal",
            "existing_tag_rejected": True,
            "mode": "lightweight_atomic_ref",
        },
    }


@workspace_mutation
def git_push(
    remote: str,
    branch: str,
    expected_head: str,
    tags: list[str] | None = None,
    confirmation: str | None = None,
    cwd: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    """Atomically push the exact current branch and explicit lightweight tags."""
    if confirmation != "PUSH":
        raise PermissionError("git_push requires confirmation='PUSH'")
    if (
        not isinstance(remote, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", remote)
    ):
        raise ValueError("remote must be an existing simple Git remote name")
    if not isinstance(tags, list):
        tags = [] if tags is None else tags
    if len(tags) > 8 or any(not isinstance(tag, str) or not tag for tag in tags):
        raise ValueError("tags must contain at most 8 explicit non-empty tag names")
    if len(tags) != len(set(tags)):
        raise ValueError("tags must not contain duplicates")

    working_directory, repo_root = _git_repo_context(cwd, root)
    current_head, current_branch = _git_release_preflight(
        working_directory,
        expected_head,
    )
    if branch != current_branch:
        raise ValueError(
            f"branch must equal the current named branch: {current_branch}"
        )

    branch_ref = _validate_git_ref_component(
        branch, "branch", "refs/heads"
    )
    branch_check = _git_run(
        working_directory,
        ["check-ref-format", branch_ref],
        allow_failure=True,
    )
    if branch_check.returncode != 0:
        raise ValueError(f"Invalid Git branch name: {branch}")

    remote_check = _git_run(
        working_directory,
        ["remote", "get-url", "--all", remote],
        allow_failure=True,
    )
    if remote_check.returncode != 0 or not remote_check.stdout.strip():
        raise ValueError(f"Unknown or unconfigured Git remote: {remote}")

    tag_refs: list[str] = []
    for tag in tags:
        tag_ref = _validate_git_ref_component(tag, "tag", "refs/tags")
        check = _git_run(
            working_directory,
            ["check-ref-format", tag_ref],
            allow_failure=True,
        )
        if check.returncode != 0:
            raise ValueError(f"Invalid Git tag name: {tag}")
        resolved = _git_resolve_commit(working_directory, tag_ref)
        if resolved != current_head:
            raise ValueError(
                f"Release tag {tag} does not point to expected HEAD {current_head}"
            )
        tag_refs.append(tag_ref)

    refspecs = [f"{branch_ref}:{branch_ref}"] + [
        f"{tag_ref}:{tag_ref}" for tag_ref in tag_refs
    ]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    push = _git_run(
        working_directory,
        ["push", "--porcelain", "--atomic", "--no-verify", remote, *refspecs],
        timeout=60,
        allow_failure=True,
        env=env,
    )
    if push.returncode != 0:
        message = (push.stderr or push.stdout).strip()
        raise ValueError(
            (message or f"Git push failed with code {push.returncode}")[
                :MAX_TEXT_CHARACTERS
            ]
        )

    verify_refs = [branch_ref, *tag_refs]
    verification = _git_run(
        working_directory,
        ["ls-remote", remote, *verify_refs],
        timeout=60,
        env=env,
    )
    remote_refs: dict[str, str] = {}
    for line in verification.stdout.splitlines():
        if "\t" not in line:
            continue
        oid, ref = line.split("\t", 1)
        remote_refs[ref] = oid.lower()

    missing_or_wrong = [
        ref for ref in verify_refs
        if remote_refs.get(ref) != current_head
    ]
    if missing_or_wrong:
        raise RuntimeError(
            "Remote verification failed for refs: "
            + ", ".join(missing_or_wrong)
        )

    stdout = truncate_text(push.stdout)
    stderr = truncate_text(push.stderr)
    return {
        "status": "completed",
        "repo_root": _relative_path(repo_root, root),
        "cwd": _relative_path(working_directory, root),
        "remote": remote,
        "branch": branch,
        "head": current_head,
        "tags": list(tags),
        "verified_remote_refs": {
            ref: remote_refs[ref] for ref in verify_refs
        },
        "stdout": stdout["content"],
        "stderr": stderr["content"],
        "stdout_truncated": stdout["truncated"],
        "stderr_truncated": stderr["truncated"],
        "preflight": {
            "confirmation": "PUSH",
            "expected_head_matched": True,
            "current_branch_matched": True,
            "worktree_clean": True,
            "index_clean": True,
            "repository_state": "normal",
            "configured_remote_only": True,
            "force_disabled": True,
            "hooks_disabled": True,
            "atomic_push": True,
            "remote_refs_verified": True,
        },
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
    command: str, parameters: dict[str, Any], root: str,
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
            result[name] = str(safe_path(value, root))
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
    root: str = "workspace",
) -> dict[str, Any]:
    """Run one allow-listed cmdlet from validated structured parameters."""
    if command not in POWERSHELL_PARAMETER_POLICY:
        raise PowerShellValidationError(f"PowerShell command not allowed: {command}")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise TypeError("parameters must be an object")
    working_directory = safe_path(workdir, root, "execute")
    if not working_directory.exists() or not working_directory.is_dir():
        raise ValueError(f"Working directory does not exist or is not a directory: {workdir}")
    validated = _validate_powershell_parameters(command, parameters, root)
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
    root: str = "workspace",
) -> dict[str, Any]:
    """Atomically apply a single-file unified text diff inside the workspace."""
    target = safe_path(path, root, "write")
    if not target.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")
    _validate_expected_sha256(target, expected_sha256, root)
    original = target.read_bytes().decode("utf-8")
    updated = _apply_unified_hunks(original, patch, _relative_path(target, root))
    _atomic_write_text(target, updated)
    _remove_cached_bytecode(target)
    return {
        "status": "completed",
        "path": _relative_path(target, root),
        "sha256": _file_sha256(target),
        "characters_before": len(original),
        "characters_after": len(updated),
        "changed": original != updated,
    }


def apply_changeset(
    changes: list[dict[str, str]], root: str = "workspace",
) -> dict[str, Any]:
    from changeset_manager import apply_changeset as execute
    return execute(changes, root)


def project_state_init(
    project_path: str = ".",
    project_name: str | None = None,
    objective: str = "",
    root: str = "workspace",
) -> dict[str, Any]:
    from project_state import init_project_state
    return init_project_state(project_path, project_name, objective, root)


def project_state_get(
    project_path: str = ".", root: str = "workspace",
) -> dict[str, Any]:
    from project_state import get_project_state
    return get_project_state(project_path, root)


def project_state_update(
    expected_revision: int,
    project_path: str = ".",
    objective: str | None = None,
    lifecycle: str | None = None,
    current_phase: str | None = None,
    next_action: str | None = None,
    blockers: list[str] | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    from project_state import update_project_state
    return update_project_state(
        expected_revision, project_path, objective, lifecycle,
        current_phase, next_action, blockers, root,
    )


def project_checkpoint(
    label: str, summary: str, expected_revision: int,
    project_path: str = ".", checks: list[str] | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    from project_state import create_checkpoint
    return create_checkpoint(label, summary, expected_revision, project_path, checks, root)


def project_decision_record(
    title: str, decision: str, rationale: str, expected_revision: int,
    project_path: str = ".", root: str = "workspace",
) -> dict[str, Any]:
    from project_state import record_decision
    return record_decision(title, decision, rationale, expected_revision, project_path, root)


def project_decisions_get(
    project_path: str = ".", max_chars: int = 20000, root: str = "workspace",
) -> dict[str, Any]:
    from project_state import get_decisions
    return get_decisions(project_path, max_chars, root)


def project_evidence_record(
    kind: str, status: str, summary: str, source: str, expected_revision: int,
    project_path: str = ".", details: str = "", artifact_sha256: str | None = None,
    verification: VerificationSpec | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    from project_state import record_evidence
    return record_evidence(
        kind, status, summary, source, expected_revision,
        project_path, details, artifact_sha256, verification, root,
    )


def project_evidence_get(
    project_path: str = ".", limit: int = 20,
    kind: str | None = None, status: str | None = None,
    root: str = "workspace",
) -> dict[str, Any]:
    from project_state import get_evidence
    return get_evidence(project_path, limit, kind, status, root)


def project_acceptance_set(
    title: str,
    checks: list[AcceptanceCheckRequest],
    expected_state_revision: int,
    expected_contract_revision: int = 0,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    from acceptance_contract import set_contract
    return set_contract(
        title, checks, expected_state_revision, expected_contract_revision,
        project_path, root,
    )


def project_acceptance_get(
    project_path: str = ".", root: str = "workspace",
) -> dict[str, Any]:
    from acceptance_contract import get_contract
    return get_contract(project_path, root)


def project_acceptance_evaluate(
    bindings: list[AcceptanceBindingRequest],
    expected_state_revision: int,
    expected_contract_revision: int,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    from acceptance_contract import evaluate_contract
    return evaluate_contract(
        bindings, expected_state_revision, expected_contract_revision,
        project_path, root,
    )


def project_acceptance_evaluations_get(
    project_path: str = ".", limit: int = 20, root: str = "workspace",
) -> dict[str, Any]:
    from acceptance_contract import get_evaluations
    return get_evaluations(project_path, limit, root)


def project_verify_acceptance(
    evaluation_id: str,
    expected_evaluation_sha256: str,
    expected_state_revision: int,
    expected_contract_revision: int,
    project_path: str = ".",
    root: str = "workspace",
) -> dict[str, Any]:
    from independent_verifier import verify_acceptance
    return verify_acceptance(
        evaluation_id, expected_evaluation_sha256,
        expected_state_revision, expected_contract_revision,
        project_path, root,
    )


def project_verifications_get(
    project_path: str = ".", limit: int = 20, root: str = "workspace",
) -> dict[str, Any]:
    from independent_verifier import get_verifications
    return get_verifications(project_path, limit, root)


LOCAL_TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "list_directory": list_directory,
    "read_text": read_text,
    "extract_document_text": extract_document_text,
    "write_text": write_text,
    "replace_text": replace_text,
    "search_text": search_text,
    "git_status": git_status,
    "git_diff": git_diff,
    "git_log": git_log,
    "git_show": git_show,
    "git_stage": git_stage,
    "git_commit": git_commit,
    "git_tag": git_tag,
    "git_push": git_push,
    "project_state_init": project_state_init,
    "project_state_get": project_state_get,
    "project_state_update": project_state_update,
    "project_checkpoint": project_checkpoint,
    "project_decision_record": project_decision_record,
    "project_decisions_get": project_decisions_get,
    "project_evidence_record": project_evidence_record,
    "project_evidence_get": project_evidence_get,
    "project_acceptance_set": project_acceptance_set,
    "project_acceptance_get": project_acceptance_get,
    "project_acceptance_evaluate": project_acceptance_evaluate,
    "project_acceptance_evaluations_get": project_acceptance_evaluations_get,
    "project_verify_acceptance": project_verify_acceptance,
    "project_verifications_get": project_verifications_get,
    "run_process": run_process,
    "run_powershell": run_powershell,
    "apply_patch": apply_patch,
    "apply_changeset": apply_changeset,
}
