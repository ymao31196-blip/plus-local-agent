from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


FULL_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
SOURCE_SPEC_SCHEMA_VERSION = 1
SOURCE_KIND_GIT_NPM = "git_npm"


def _run_checked(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    failure_message: str,
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    stdout = completed.stdout[-20000:]
    stderr = completed.stderr[-20000:]
    if completed.returncode != 0:
        raise RuntimeError(
            stderr.strip()
            or stdout.strip()
            or f"{failure_message} (exit code {completed.returncode})"
        )
    return completed, stdout, stderr


def _safe_relative(base: Path, raw_value: Any, label: str) -> Path:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    raw = Path(raw_value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError(f"{label} must stay inside the provider source directory")
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the provider source directory") from exc
    return resolved


def load_source_spec(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid source provider spec JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Source provider spec must be an object: {path.name}")

    allowed = {
        "schema_version",
        "kind",
        "repository_url",
        "revision",
        "package_subdir",
        "entrypoint",
        "node_min_major",
        "patches",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"Unknown source provider spec fields in {path.name}: "
            + ", ".join(sorted(unknown))
        )
    if payload.get("schema_version") != SOURCE_SPEC_SCHEMA_VERSION:
        raise ValueError(f"Unsupported source provider spec schema: {path.name}")
    if payload.get("kind") != SOURCE_KIND_GIT_NPM:
        raise ValueError(f"Unsupported source provider kind: {payload.get('kind')!r}")

    repository_url = payload.get("repository_url")
    if not isinstance(repository_url, str) or not repository_url.strip():
        raise ValueError("repository_url must be a non-empty HTTPS GitHub URL")
    parsed = urlparse(repository_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or len([part for part in parsed.path.split("/") if part]) != 2
    ):
        raise ValueError("repository_url must be an HTTPS github.com owner/repo URL")

    revision = payload.get("revision")
    if not isinstance(revision, str) or not FULL_GIT_SHA.fullmatch(revision):
        raise ValueError("revision must be a pinned 40-character Git commit SHA")

    package_subdir = payload.get("package_subdir", ".")
    entrypoint = payload.get("entrypoint")
    if not isinstance(package_subdir, str) or not package_subdir.strip():
        raise ValueError("package_subdir must be a non-empty relative path")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise ValueError("entrypoint must be a non-empty relative path")

    node_min_major = payload.get("node_min_major", 18)
    if (
        isinstance(node_min_major, bool)
        or not isinstance(node_min_major, int)
        or not 18 <= node_min_major <= 100
    ):
        raise ValueError("node_min_major must be an integer between 18 and 100")

    patches = payload.get("patches", [])
    if (
        not isinstance(patches, list)
        or not all(isinstance(item, str) and item.strip() for item in patches)
        or len(patches) != len(set(patches))
    ):
        raise ValueError("patches must be a unique array of non-empty relative paths")

    return {
        "repository_url": repository_url.rstrip("/"),
        "revision": revision.lower(),
        "package_subdir": package_subdir,
        "entrypoint": entrypoint,
        "node_min_major": node_min_major,
        "patches": patches,
    }


def _canonical_git_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.casefold()


def setup_git_npm_source(
    root: Path,
    provider: str,
    spec_path: Path,
    *,
    timeout_seconds: int,
    managed_node: Path | None = None,
    managed_npm_cli: Path | None = None,
) -> dict[str, Any]:
    spec = load_source_spec(spec_path)
    git = shutil.which("git.exe") or shutil.which("git")
    if not git:
        raise RuntimeError("git executable was not found")
    if (managed_node is None) != (managed_npm_cli is None):
        raise ValueError("Managed Node and npm CLI must be supplied together")
    if managed_node is not None:
        # Desktop ships a vetted Node/npm pair. npm.cmd on Windows may resolve
        # a separate installation or fail under the sanitized frozen runtime.
        if not managed_node.is_file() or not managed_npm_cli.is_file():
            raise RuntimeError("Bundled Node or npm CLI is missing")
        node = str(managed_node.resolve())
        npm_command = [node, str(managed_npm_cli.resolve())]
    else:
        node = shutil.which("node.exe") or shutil.which("node")
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not node:
            raise RuntimeError("node executable was not found")
        if not npm:
            raise RuntimeError("npm executable was not found")
        npm_command = [npm]

    sources_dir = (root / ".provider_sources").resolve()
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_root = (sources_dir / provider).resolve()
    source_root.relative_to(sources_dir)

    repository_url = spec["repository_url"]
    revision = spec["revision"]
    if source_root.exists():
        if not (source_root / ".git").is_dir():
            raise RuntimeError(
                f"Provider source directory exists but is not a Git repository: {source_root}"
            )
        _, remote_stdout, _ = _run_checked(
            [git, "-C", str(source_root), "remote", "get-url", "origin"],
            cwd=root,
            timeout_seconds=timeout_seconds,
            failure_message="Failed to inspect provider source remote",
        )
        if _canonical_git_url(remote_stdout) != _canonical_git_url(repository_url):
            raise RuntimeError(
                "Provider source remote does not match reviewed repository_url"
            )
    else:
        _run_checked(
            [
                git,
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                repository_url,
                str(source_root),
            ],
            cwd=root,
            timeout_seconds=timeout_seconds,
            failure_message="Failed to clone provider source",
        )

    _run_checked(
        [git, "-C", str(source_root), "fetch", "--depth", "1", "origin", revision],
        cwd=root,
        timeout_seconds=timeout_seconds,
        failure_message="Failed to fetch pinned provider revision",
    )
    _run_checked(
        [git, "-C", str(source_root), "checkout", "--detach", revision],
        cwd=root,
        timeout_seconds=timeout_seconds,
        failure_message="Failed to checkout pinned provider revision",
    )
    _run_checked(
        [git, "-C", str(source_root), "reset", "--hard", revision],
        cwd=root,
        timeout_seconds=timeout_seconds,
        failure_message="Failed to reset provider source to pinned revision",
    )
    _, head_stdout, _ = _run_checked(
        [git, "-C", str(source_root), "rev-parse", "HEAD"],
        cwd=root,
        timeout_seconds=timeout_seconds,
        failure_message="Failed to verify provider revision",
    )
    actual_revision = head_stdout.strip().casefold()
    if actual_revision != revision:
        raise RuntimeError(
            f"Provider revision mismatch: expected {revision}, got {actual_revision}"
        )

    patch_root = (root / "provider_patches" / provider).resolve()
    for patch_relative in spec["patches"]:
        patch_path = _safe_relative(root, patch_relative, "patch path")
        try:
            patch_path.relative_to(patch_root)
        except ValueError as exc:
            raise ValueError(
                f"patch path must stay inside provider_patches/{provider}"
            ) from exc
        if not patch_path.is_file():
            raise RuntimeError(f"Provider patch is missing: {patch_path}")
        _run_checked(
            [git, "-C", str(source_root), "apply", "--check", str(patch_path)],
            cwd=root,
            timeout_seconds=timeout_seconds,
            failure_message=f"Provider patch preflight failed: {patch_path.name}",
        )
        _run_checked(
            [
                git,
                "-C",
                str(source_root),
                "apply",
                "--whitespace=nowarn",
                str(patch_path),
            ],
            cwd=root,
            timeout_seconds=timeout_seconds,
            failure_message=f"Provider patch apply failed: {patch_path.name}",
        )

    package_root = _safe_relative(
        source_root,
        spec["package_subdir"],
        "package_subdir",
    )
    if not (package_root / "package.json").is_file():
        raise RuntimeError(f"Provider package.json is missing: {package_root}")
    if not (package_root / "package-lock.json").is_file():
        raise RuntimeError(f"Provider package-lock.json is missing: {package_root}")

    _, node_stdout, _ = _run_checked(
        [node, "--version"],
        cwd=package_root,
        timeout_seconds=timeout_seconds,
        failure_message="Failed to inspect Node.js version",
    )
    node_version = node_stdout.strip().lstrip("v")
    try:
        node_major = int(node_version.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Unable to parse Node.js version: {node_stdout.strip()!r}"
        ) from exc
    if node_major < spec["node_min_major"]:
        raise RuntimeError(
            f"Node.js {spec['node_min_major']}+ is required; found {node_stdout.strip()}"
        )

    _run_checked(
        [*npm_command, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=package_root,
        timeout_seconds=timeout_seconds,
        failure_message="Provider npm ci failed",
    )
    _run_checked(
        [*npm_command, "run", "build", "--ignore-scripts"],
        cwd=package_root,
        timeout_seconds=timeout_seconds,
        failure_message="Provider npm build failed",
    )

    entrypoint = _safe_relative(package_root, spec["entrypoint"], "entrypoint")
    if not entrypoint.is_file():
        raise RuntimeError(f"Provider entrypoint was not built: {entrypoint}")

    return {
        "kind": SOURCE_KIND_GIT_NPM,
        "repository_url": repository_url,
        "revision": revision,
        "source_root": str(source_root),
        "package_root": str(package_root),
        "entrypoint": str(entrypoint),
        "node_version": node_stdout.strip(),
        "patches": list(spec["patches"]),
    }
