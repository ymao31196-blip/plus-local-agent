"""Optional QMD-backed project-local Markdown index for authorized PLA roots.

QMD is a dependency, not a general command runner. Only fixed subcommands are
allowed. A generated, tightly validated QMD config keeps untrusted project
files from turning an index refresh into execution of user-controlled hooks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
from urllib.parse import unquote

import yaml

from runtime.runtime_context import checkpoint
from tooling.file_operations import _resolve, _is_link

INDEX_DIR = ".qmd"
MARKER = "PLA workspace local index - managed by PLA"
COLLECTION = "pla"
PATTERN = "**/*.md"
IGNORED_DIRS = frozenset({
    ".qmd", ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", "dist",
    "build", ".next", ".cache", ".idea", ".vscode",
})
IGNORES = [f"**/{name}/**" for name in sorted(IGNORED_DIRS)] + [
    f"{name}/**" for name in sorted(IGNORED_DIRS)
]
MAX_ENTRIES = 4000
MAX_MARKDOWN_FILES = 1200
MAX_DOC_BYTES = 512 * 1024
MAX_TOTAL_DOC_BYTES = 24 * 1024 * 1024
MAX_QUERY = 120
MAX_RESULTS = 20
MAX_STDOUT = 128 * 1024
DEFAULT_MODELS = {
    "embed": "hf:ggml-org/embeddinggemma-300M-GGUF/embeddinggemma-300M-Q8_0.gguf",
    "generate": "hf:tobil/qmd-query-expansion-1.7B-gguf/qmd-query-expansion-1.7B-q4_k_m.gguf",
    "rerank": "hf:ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf",
}


def _workdir(root: str, directory: str, *, write: bool) -> Path:
    if root.casefold() == "pla":
        raise PermissionError("Indexing the protected PLA source root is not supported")
    target = _resolve(directory, root, "write" if write else "read")
    if not target.is_dir() or _is_link(target):
        raise ValueError("Workspace directory is missing or linked")
    return target


def _config() -> dict:
    return {
        "global_context": MARKER,
        "collections": {COLLECTION: {
            "path": ".", "pattern": PATTERN, "ignore": IGNORES,
        }},
    }


def _config_path(directory: Path) -> Path:
    hidden = directory / INDEX_DIR
    if _is_link(hidden):
        raise ValueError("Index directory cannot be a link")
    return hidden / "index.yml"


def _verified_config(directory: Path) -> None:
    config = _config_path(directory)
    if not config.is_file() or _is_link(config) or config.stat().st_size > 32 * 1024:
        raise ValueError("PLA-managed QMD index is not initialized")
    try:
        parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValueError("Invalid project index config") from exc
    if not isinstance(parsed, dict) or set(parsed) - {"global_context", "collections", "models"}:
        raise PermissionError("Unreviewed QMD configuration is not permitted")
    if parsed.get("global_context") != MARKER or parsed.get("collections") != _config()["collections"]:
        raise PermissionError("Unreviewed QMD collections or hooks are not permitted")
    if "models" in parsed and parsed["models"] != DEFAULT_MODELS:
        raise PermissionError("Unreviewed QMD model sources are not permitted")


def _scan(directory: Path, root: str) -> int:
    """Bound corpus and reject links. Ignore build/dependency directories."""
    todo = [directory]
    count, docs, total = 0, 0, 0
    while todo:
        checkpoint()
        parent = todo.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError("Workspace index scan exceeds entry limit")
                path = Path(entry.path)
                if entry.is_symlink() or _is_link(path):
                    raise ValueError("Workspace index does not follow symlinks or reparse points")
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in IGNORED_DIRS:
                        todo.append(path)
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".md"):
                    # Recheck PLA authorization for each indexed file.
                    if _resolve(str(path), root, "read") != path.resolve():
                        raise PermissionError("Index input changed during scan")
                    size = entry.stat(follow_symlinks=False).st_size
                    docs += 1
                    total += size
                    if docs > MAX_MARKDOWN_FILES or size > MAX_DOC_BYTES or total > MAX_TOTAL_DOC_BYTES:
                        raise ValueError("Workspace index exceeds Markdown count or size limit")
    return docs


def _engine() -> tuple[Path, Path]:
    data = Path(os.environ.get("PLA_DATA_ROOT") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "io.pla.desktop")
    if not data.is_dir():
        raise RuntimeError("PLA Desktop data directory is unavailable for QMD")
    root = data / "components" / ".provider_envs"
    candidates = ["qmd", "qmd-pilot"]
    for name in candidates:
        cli = root / name / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
        if cli.is_file() and not _is_link(cli):
            break
    else:
        raise RuntimeError("Optional QMD engine is not installed in PLA Desktop")
    resources = os.environ.get("PLA_INSTALL_RESOURCES")
    node = Path(resources) / "node.exe" if resources else None
    if node is None or not node.is_file():
        found = shutil.which("node")
        node = Path(found) if found else None
    if node is None or not node.is_file():
        raise RuntimeError("Node.js runtime for QMD is unavailable")
    return node.resolve(), cli.resolve()


def _execute(directory: Path, action: str, query: str = "", limit: int = 10) -> str:
    checkpoint()
    node, cli = _engine()
    args = [str(node), str(cli), action]
    if action == "search":
        args.extend([query, "--json", "-n", str(limit)])
    # Do not carry Tunnel credentials, QMD trust flags, or arbitrary model URLs.
    env = {k: os.environ[k] for k in (
        "SystemRoot", "WINDIR", "PATH", "TEMP", "TMP",
        "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA",
    ) if k in os.environ}
    env["HF_HUB_OFFLINE"] = "1"
    try:
        result = subprocess.run(
            args, cwd=str(directory), env=env, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("QMD index command timed out") from exc
    except OSError as exc:
        raise RuntimeError("QMD runtime could not be launched") from exc
    output = result.stdout
    if len(output) > MAX_STDOUT or len(result.stderr) > MAX_STDOUT:
        raise ValueError("QMD index output exceeded limit")
    if result.returncode != 0:
        # Avoid returning arbitrary file content/paths from QMD stderr.
        raise RuntimeError(f"QMD {action} failed (exit {result.returncode})")
    checkpoint()
    return output.decode("utf-8", errors="replace")


def index_init(*, root: str = "workspace", directory: str) -> dict:
    target = _workdir(root, directory, write=True)
    _scan(target, root)
    # A missing optional engine must not leave a newly-created .qmd directory.
    _engine()
    config = _config_path(target)
    if config.exists() or config.parent.exists():
        raise FileExistsError("Workspace already has a .qmd directory; refusing to overwrite")
    config.parent.mkdir()
    try:
        with config.open("x", encoding="utf-8") as f:
            f.write(yaml.safe_dump(_config(), allow_unicode=True, sort_keys=False))
    except Exception:
        config.parent.rmdir()
        raise
    _verified_config(target)
    output = _execute(target, "update")
    _verified_config(target)
    return {
        "status": "completed", "operation": "init",
        "root": root, "directory": directory, "engine": "qmd",
        "indexed_files_preflight": _scan(target, root),
        "summary": output[-3000:],
    }


def index_refresh(*, root: str = "workspace", directory: str) -> dict:
    target = _workdir(root, directory, write=True)
    _verified_config(target)
    scanned = _scan(target, root)
    output = _execute(target, "update")
    _verified_config(target)
    return {
        "status": "completed", "operation": "refresh",
        "root": root, "directory": directory,
        "indexed_files_preflight": scanned, "summary": output[-3000:],
    }


def index_status(*, root: str = "workspace", directory: str) -> dict:
    target = _workdir(root, directory, write=False)
    _verified_config(target)
    output = _execute(target, "status")
    return {"status": "completed", "operation": "status",
            "root": root, "directory": directory, "summary": output[-5000:]}


def index_search(*, root: str = "workspace", directory: str,
                 query: str, limit: int = 10) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        raise ValueError("Search query must be 1–120 characters")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError("Search result limit must be between 1 and 20")
    target = _workdir(root, directory, write=False)
    _verified_config(target)
    raw = _execute(target, "search", query, limit)
    try:
        entries = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid QMD search response") from exc
    if not isinstance(entries, list) or len(entries) > limit:
        raise ValueError("Unbounded QMD search response")
    output = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid QMD search entry")
        uri = entry.get("file")
        prefix = f"qmd://{COLLECTION}/"
        if not isinstance(uri, str) or not uri.startswith(prefix):
            raise PermissionError("QMD result collection does not match workspace")
        name = unquote(uri[len(prefix):])
        pp = PurePosixPath(name)
        if (not name or name.startswith("/") or "\\" in name
                or pp.is_absolute() or any(v in ("", ".", "..") for v in pp.parts)
                or pp.suffix.lower() != ".md"
                or any(part in IGNORED_DIRS for part in pp.parts)):
            raise PermissionError("QMD result path is not allowed")
        resolved = _resolve(str(target / Path(*pp.parts)), root, "read")
        if not resolved.is_file() or _is_link(resolved) or not resolved.is_relative_to(target):
            # Stale indexed files must not be presented as existing references.
            continue
        output.append({
            "path": (Path(directory) / Path(*pp.parts)).as_posix(),
            "relative_path": pp.as_posix(),
            "title": str(entry.get("title", ""))[:240],
            "line": entry.get("line") if type(entry.get("line")) is int else None,
            "score": entry.get("score") if type(entry.get("score")) in (float, int) else None,
            "snippet": str(entry.get("snippet", ""))[:1000],
        })
    return {"status": "completed", "operation": "search",
            "root": root, "directory": directory,
            "query": query, "results": output, "count": len(output)}
