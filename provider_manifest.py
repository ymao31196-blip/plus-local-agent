"""Declarative provider manifest loading for the PLA v1 capability runtime.

Provider manifests describe external MCP processes and capability policies.
Runtime kinds share the same capability semantics and stdio transport boundary.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from capability_models import PROVIDER_ID_RE


SCHEMA_VERSION = 1
ISOLATED_PYTHON_STDIO = "isolated_python_stdio"
EXECUTABLE_STDIO = "executable_stdio"
STREAMABLE_HTTP = "streamable_http"
RUNTIME_KINDS = frozenset(
    {ISOLATED_PYTHON_STDIO, EXECUTABLE_STDIO, STREAMABLE_HTTP}
)


@dataclass(frozen=True)
class ProviderManifest:
    provider_id: str
    path: Path
    autostart: bool
    mode: str
    routing_authority: str
    runtime_kind: str
    command_path: Path | None
    endpoint_url: str | None
    python_path: Path | None
    args: tuple[str, ...]
    cwd: Path
    discovery_timeout_seconds: float | None
    invoke_timeout_seconds: float | None
    persistent_session: bool
    tool_allowlist: tuple[str, ...] | None
    tool_overrides: dict[str, dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        value = {
            "provider_id": self.provider_id,
            "manifest": str(self.path),
            "autostart": self.autostart,
            "mode": self.mode,
            "routing_authority": self.routing_authority,
            "runtime_kind": self.runtime_kind,
            "cwd": str(self.cwd),
            "discovery_timeout_seconds": self.discovery_timeout_seconds,
            "invoke_timeout_seconds": self.invoke_timeout_seconds,
            "persistent_session": self.persistent_session,
            "tool_allowlist": (
                list(self.tool_allowlist)
                if self.tool_allowlist is not None
                else None
            ),
        }
        if self.command_path is not None:
            value["command"] = str(self.command_path)
            value["command_exists"] = self.command_path.is_file()
        if self.endpoint_url is not None:
            value["url"] = self.endpoint_url
        if self.python_path is not None:
            value["python"] = str(self.python_path)
            value["python_exists"] = self.python_path.is_file()
        return value


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


def _resolve_command(project_root: Path, raw_value: str, label: str) -> Path:
    raw = Path(raw_value)
    if raw.is_absolute():
        return raw.resolve()
    if raw.parent == Path("."):
        discovered = shutil.which(raw_value)
        if discovered is None:
            raise ValueError(f"{label} was not found on PATH: {raw_value!r}")
        return Path(discovered).resolve()
    return _resolve_inside(project_root, raw_value, label)


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
        "routing_authority",
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

    routing_authority = payload.get("routing_authority", "recommendation")
    if routing_authority not in {"recommendation", "preferred"}:
        raise ValueError(
            f"{path.name}.routing_authority must be "
            "'recommendation' or 'preferred'"
        )

    runtime = _require_object(payload.get("runtime"), f"{path.name}.runtime")
    runtime_unknown = set(runtime) - {
        "kind",
        "python",
        "command",
        "url",
        "args",
        "cwd",
        "discovery_timeout_seconds",
        "invoke_timeout_seconds",
        "persistent_session",
    }
    if runtime_unknown:
        raise ValueError(
            f"Unknown runtime fields in {path.name}: "
            + ", ".join(sorted(runtime_unknown))
        )

    runtime_kind = runtime.get("kind")
    if runtime_kind not in RUNTIME_KINDS:
        raise ValueError(
            f"Unsupported runtime kind in {path.name}: {runtime_kind!r}"
        )

    python_path: Path | None = None
    command_path: Path | None = None
    endpoint_url: str | None = None

    if runtime_kind == ISOLATED_PYTHON_STDIO:
        if "command" in runtime:
            raise ValueError(
                f"{path.name}.runtime.command is not valid for "
                "isolated_python_stdio"
            )
        if "url" in runtime:
            raise ValueError(
                f"{path.name}.runtime.url is not valid for "
                "isolated_python_stdio"
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
        command_path = python_path
    elif runtime_kind == EXECUTABLE_STDIO:
        if "python" in runtime:
            raise ValueError(
                f"{path.name}.runtime.python is not valid for executable_stdio"
            )
        if "url" in runtime:
            raise ValueError(
                f"{path.name}.runtime.url is not valid for executable_stdio"
            )
        command_value = _require_string(
            runtime.get("command"), f"{path.name}.runtime.command"
        )
        command_path = _resolve_command(
            project_root, command_value, f"{path.name}.runtime.command"
        )
    else:
        if "python" in runtime or "command" in runtime:
            raise ValueError(
                f"{path.name}.runtime python/command is not valid for streamable_http"
            )
        endpoint_url = _require_string(
            runtime.get("url"), f"{path.name}.runtime.url"
        )
        parsed = urlparse(endpoint_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                f"{path.name}.runtime.url must be a loopback HTTP endpoint"
            )

    raw_args = runtime.get("args", [])
    if not isinstance(raw_args, list) or not all(
        isinstance(item, str) for item in raw_args
    ):
        raise ValueError(f"{path.name}.runtime.args must be an array of strings")
    if runtime_kind == STREAMABLE_HTTP and raw_args:
        raise ValueError(
            f"{path.name}.runtime.args is not valid for streamable_http"
        )

    cwd_relative = runtime.get("cwd", ".")
    cwd_relative = _require_string(cwd_relative, f"{path.name}.runtime.cwd")
    cwd = _resolve_inside(project_root, cwd_relative, f"{path.name}.runtime.cwd")

    def optional_timeout(field: str) -> float | None:
        raw = runtime.get(field)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{path.name}.runtime.{field} must be numeric")
        value = float(raw)
        if value <= 0 or value > 300:
            raise ValueError(
                f"{path.name}.runtime.{field} must be > 0 and <= 300 seconds"
            )
        return value

    discovery_timeout_seconds = optional_timeout("discovery_timeout_seconds")
    invoke_timeout_seconds = optional_timeout("invoke_timeout_seconds")

    persistent_session = runtime.get("persistent_session", False)
    if not isinstance(persistent_session, bool):
        raise ValueError(
            f"{path.name}.runtime.persistent_session must be boolean"
        )
    if persistent_session and runtime_kind != STREAMABLE_HTTP:
        raise ValueError(
            f"{path.name}.runtime.persistent_session is only valid for "
            "streamable_http"
        )

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
        routing_authority=routing_authority,
        runtime_kind=runtime_kind,
        command_path=command_path,
        endpoint_url=endpoint_url,
        python_path=python_path,
        args=tuple(raw_args),
        cwd=cwd,
        discovery_timeout_seconds=discovery_timeout_seconds,
        invoke_timeout_seconds=invoke_timeout_seconds,
        persistent_session=persistent_session,
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
