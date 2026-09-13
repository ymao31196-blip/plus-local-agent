"""Manifest model for isolated external Observer plugins in PLA v1.2 Phase 4."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capability_models import PROVIDER_ID_RE


SCHEMA_VERSION = 1
RUNTIME_KIND = "isolated_python_module"
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
MAX_EVENT_TYPES = 32
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ExternalObserverManifest:
    observer_id: str
    path: Path
    autostart: bool
    event_types: tuple[str, ...]
    timeout_seconds: float
    python_path: Path
    module: str
    cwd: Path

    @property
    def hook_id(self) -> str:
        return f"external.{self.observer_id}"

    def summary(self) -> dict[str, Any]:
        return {
            "observer_id": self.observer_id,
            "hook_id": self.hook_id,
            "manifest": str(self.path),
            "autostart": self.autostart,
            "event_types": list(self.event_types),
            "timeout_seconds": self.timeout_seconds,
            "runtime_kind": RUNTIME_KIND,
            "python": str(self.python_path),
            "python_exists": self.python_path.is_file(),
            "module": self.module,
            "cwd": str(self.cwd),
            "cwd_exists": self.cwd.is_dir(),
        }


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    if "\x00" in value:
        raise ValueError(f"{label} cannot contain NUL")
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


def load_external_observer_manifest(
    path: Path,
    project_root: Path,
) -> ExternalObserverManifest:
    path = path.resolve()
    project_root = project_root.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid observer manifest JSON: {path.name}") from exc
    payload = _require_object(payload, f"Manifest {path.name}")

    unknown = set(payload) - {
        "schema_version",
        "id",
        "autostart",
        "event_types",
        "timeout_seconds",
        "runtime",
    }
    if unknown:
        raise ValueError(
            f"Unknown observer manifest fields in {path.name}: "
            + ", ".join(sorted(unknown))
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported observer manifest schema_version in {path.name}: "
            f"{payload.get('schema_version')!r}"
        )

    observer_id = _require_string(payload.get("id"), f"{path.name}.id").casefold()
    if not PROVIDER_ID_RE.fullmatch(observer_id):
        raise ValueError(f"Invalid observer id in {path.name}: {observer_id!r}")

    autostart = payload.get("autostart", False)
    if not isinstance(autostart, bool):
        raise ValueError(f"{path.name}.autostart must be boolean")

    raw_event_types = payload.get("event_types")
    if (
        not isinstance(raw_event_types, list)
        or not raw_event_types
        or len(raw_event_types) > MAX_EVENT_TYPES
        or not all(isinstance(item, str) and item.strip() for item in raw_event_types)
    ):
        raise ValueError(
            f"{path.name}.event_types must contain 1-{MAX_EVENT_TYPES} non-empty strings"
        )
    event_types = tuple(item.strip() for item in raw_event_types)
    if len(set(event_types)) != len(event_types):
        raise ValueError(f"{path.name}.event_types contains duplicates")
    if any(len(item) > 256 or "\x00" in item for item in event_types):
        raise ValueError(f"{path.name}.event_types contains an invalid event type")

    timeout_seconds = payload.get("timeout_seconds", 2.0)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not MIN_TIMEOUT_SECONDS <= float(timeout_seconds) <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"{path.name}.timeout_seconds must be between "
            f"{MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )

    runtime = _require_object(payload.get("runtime"), f"{path.name}.runtime")
    runtime_unknown = set(runtime) - {"kind", "python", "module", "cwd"}
    if runtime_unknown:
        raise ValueError(
            f"Unknown observer runtime fields in {path.name}: "
            + ", ".join(sorted(runtime_unknown))
        )
    if runtime.get("kind") != RUNTIME_KIND:
        raise ValueError(
            f"{path.name}.runtime.kind must be {RUNTIME_KIND!r}"
        )

    python_relative = _require_string(
        runtime.get("python"), f"{path.name}.runtime.python"
    )
    python_path = _resolve_inside(
        project_root,
        python_relative,
        f"{path.name}.runtime.python",
    )
    observer_env = (project_root / ".observer_envs" / observer_id).resolve()
    try:
        python_path.relative_to(observer_env)
    except ValueError as exc:
        raise ValueError(
            f"{path.name}.runtime.python must be inside "
            f".observer_envs/{observer_id}"
        ) from exc
    if python_path.name.casefold() not in {"python.exe", "python"}:
        raise ValueError(
            f"{path.name}.runtime.python must point to a Python interpreter"
        )

    module = _require_string(runtime.get("module"), f"{path.name}.runtime.module")
    if not MODULE_RE.fullmatch(module):
        raise ValueError(f"{path.name}.runtime.module is not a valid Python module")

    cwd_relative = _require_string(
        runtime.get("cwd", "."),
        f"{path.name}.runtime.cwd",
    )
    cwd = _resolve_inside(project_root, cwd_relative, f"{path.name}.runtime.cwd")

    return ExternalObserverManifest(
        observer_id=observer_id,
        path=path,
        autostart=autostart,
        event_types=event_types,
        timeout_seconds=float(timeout_seconds),
        python_path=python_path,
        module=module,
        cwd=cwd,
    )


def load_external_observer_manifests(
    project_root: Path,
    manifest_dir: Path | None = None,
) -> dict[str, ExternalObserverManifest]:
    project_root = project_root.resolve()
    directory = (
        manifest_dir.resolve()
        if manifest_dir is not None
        else (project_root / "observer_manifests").resolve()
    )
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError(f"Observer manifest path is not a directory: {directory}")

    manifests: dict[str, ExternalObserverManifest] = {}
    for path in sorted(directory.glob("*.json")):
        manifest = load_external_observer_manifest(path, project_root)
        if manifest.observer_id in manifests:
            raise ValueError(
                f"Duplicate observer id across manifests: {manifest.observer_id}"
            )
        manifests[manifest.observer_id] = manifest
    return manifests
