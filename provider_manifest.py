"""Declarative provider manifest loading for PLA v0.17.

Provider manifests describe external MCP processes and capability policies. The
runtime intentionally supports only isolated Python stdio providers in v0.17;
additional transport kinds can be added without changing capability semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capability_models import PROVIDER_ID_RE


SCHEMA_VERSION = 1
RUNTIME_KIND = "isolated_python_stdio"


@dataclass(frozen=True)
class ProviderManifest:
    provider_id: str
    path: Path
    autostart: bool
    mode: str
    python_path: Path
    args: tuple[str, ...]
    cwd: Path
    tool_allowlist: tuple[str, ...] | None
    tool_overrides: dict[str, dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "manifest": str(self.path),
            "autostart": self.autostart,
            "mode": self.mode,
            "python": str(self.python_path),
            "python_exists": self.python_path.is_file(),
            "cwd": str(self.cwd),
            "tool_allowlist": (
                list(self.tool_allowlist)
                if self.tool_allowlist is not None
                else None
            ),
        }


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _resolve_inside(base: Path, relative: str, label: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute():
        raise ValueError(f"{label} must be relative to the PLA project root")
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the PLA project root") from exc
    return resolved


def load_provider_manifest(path: Path, project_root: Path) -> ProviderManifest:
    path = path.resolve()
    project_root = project_root.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid provider manifest JSON: {path.name}") from exc
    payload = _require_object(payload, f"Manifest {path.name}")

    unknown = set(payload) - {
        "schema_version",
        "id",
        "autostart",
        "mode",
        "runtime",
        "tool_allowlist",
        "tool_overrides",
    }
    if unknown:
        raise ValueError(
            f"Unknown provider manifest fields in {path.name}: "
            + ", ".join(sorted(unknown))
        )

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported provider manifest schema_version in {path.name}: "
            f"{payload.get('schema_version')!r}"
        )

    provider_id = _require_string(payload.get("id"), f"{path.name}.id")
    if not PROVIDER_ID_RE.fullmatch(provider_id):
        raise ValueError(f"Invalid provider id in {path.name}: {provider_id!r}")

    autostart = payload.get("autostart", False)
    if not isinstance(autostart, bool):
        raise ValueError(f"{path.name}.autostart must be boolean")

    mode = payload.get("mode", "auto")
    if mode not in {"auto", "legacy"}:
        raise ValueError(f"{path.name}.mode must be 'auto' or 'legacy'")

    runtime = _require_object(payload.get("runtime"), f"{path.name}.runtime")
    runtime_unknown = set(runtime) - {"kind", "python", "args", "cwd"}
    if runtime_unknown:
        raise ValueError(
            f"Unknown runtime fields in {path.name}: "
            + ", ".join(sorted(runtime_unknown))
        )
    if runtime.get("kind") != RUNTIME_KIND:
        raise ValueError(
            f"Unsupported runtime kind in {path.name}: {runtime.get('kind')!r}"
        )

    python_relative = _require_string(
        runtime.get("python"), f"{path.name}.runtime.python"
    )
    python_path = _resolve_inside(
        project_root, python_relative, f"{path.name}.runtime.python"
    )
    provider_env = (
        project_root / ".provider_envs" / provider_id
    ).resolve()
    try:
        python_path.relative_to(provider_env)
    except ValueError as exc:
        raise ValueError(
            f"{path.name}.runtime.python must be inside "
            f".provider_envs/{provider_id}"
        ) from exc
    if python_path.name.casefold() not in {"python.exe", "python"}:
        raise ValueError(
            f"{path.name}.runtime.python must point to a Python interpreter"
        )

    raw_args = runtime.get("args", [])
    if not isinstance(raw_args, list) or not all(
        isinstance(item, str) for item in raw_args
    ):
        raise ValueError(f"{path.name}.runtime.args must be an array of strings")

    cwd_relative = runtime.get("cwd", ".")
    cwd_relative = _require_string(cwd_relative, f"{path.name}.runtime.cwd")
    cwd = _resolve_inside(project_root, cwd_relative, f"{path.name}.runtime.cwd")

    raw_allowlist = payload.get("tool_allowlist")
    tool_allowlist: tuple[str, ...] | None
    if raw_allowlist is None:
        tool_allowlist = None
    else:
        if not isinstance(raw_allowlist, list) or not all(
            isinstance(item, str) and item for item in raw_allowlist
        ):
            raise ValueError(
                f"{path.name}.tool_allowlist must be an array of tool names"
            )
        if len(raw_allowlist) != len(set(raw_allowlist)):
            raise ValueError(
                f"{path.name}.tool_allowlist contains duplicate tool names"
            )
        tool_allowlist = tuple(raw_allowlist)

    tool_overrides = payload.get("tool_overrides", {})
    if not isinstance(tool_overrides, dict):
        raise ValueError(f"{path.name}.tool_overrides must be an object")
    for tool_name, override in tool_overrides.items():
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError(f"{path.name}.tool_overrides has invalid tool name")
        if not isinstance(override, dict):
            raise ValueError(
                f"{path.name}.tool_overrides.{tool_name} must be an object"
            )

    if tool_allowlist is not None:
        unknown_overrides = set(tool_overrides) - set(tool_allowlist)
        if unknown_overrides:
            raise ValueError(
                f"{path.name} overrides tools outside tool_allowlist: "
                + ", ".join(sorted(unknown_overrides))
            )

    return ProviderManifest(
        provider_id=provider_id,
        path=path,
        autostart=autostart,
        mode=mode,
        python_path=python_path,
        args=tuple(raw_args),
        cwd=cwd,
        tool_allowlist=tool_allowlist,
        tool_overrides=dict(tool_overrides),
    )


def load_provider_manifests(
    project_root: Path,
    manifest_dir: Path | None = None,
) -> dict[str, ProviderManifest]:
    project_root = project_root.resolve()
    directory = (
        manifest_dir.resolve()
        if manifest_dir is not None
        else (project_root / "provider_manifests").resolve()
    )
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError(f"Provider manifest path is not a directory: {directory}")

    manifests: dict[str, ProviderManifest] = {}
    for path in sorted(directory.glob("*.json")):
        manifest = load_provider_manifest(path, project_root)
        if manifest.provider_id in manifests:
            raise ValueError(
                f"Duplicate provider id across manifests: {manifest.provider_id}"
            )
        manifests[manifest.provider_id] = manifest
    return manifests
