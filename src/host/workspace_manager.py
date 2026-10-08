"""Named-root path resolution and runtime workspace registry policy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePath
import re
from threading import RLock
import tempfile
from typing import Literal

import yaml


Access = Literal["read", "write", "execute"]
WORKSPACE_CONFIG_VERSION = 1
WORKSPACE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RESERVED_ROOT_NAMES = frozenset({"workspace", "pla"})
_WORKSPACE_CONFIG_LOCK = RLock()


@dataclass(frozen=True)
class RootDefinition:
    name: str
    path: Path
    read: bool = True
    write: bool = True
    execute: bool = True


@dataclass(frozen=True)
class ResolvedPath:
    root: RootDefinition
    target: Path
    relative: str


def _is_within(base: Path, target: Path) -> bool:
    try:
        common = os.path.commonpath(
            (os.path.normcase(str(base)), os.path.normcase(str(target)))
        )
    except ValueError:
        return False
    return common == os.path.normcase(str(base))


def _paths_overlap(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def load_workspace_roots(
    config_path: Path,
    pla_root: Path,
) -> dict[str, RootDefinition]:
    """Load machine-local user roots without changing the PLA source tree."""
    config_path = config_path.resolve()
    if not config_path.exists():
        return {}
    if not config_path.is_file():
        raise ValueError(f"Workspace config is not a file: {config_path}")

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read workspace config: {exc}") from exc

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Workspace config must contain a mapping")
    if set(payload) - {"version", "roots"}:
        raise ValueError("Workspace config contains unsupported top-level fields")
    if payload.get("version", WORKSPACE_CONFIG_VERSION) != WORKSPACE_CONFIG_VERSION:
        raise ValueError(
            f"Workspace config version must be {WORKSPACE_CONFIG_VERSION}"
        )

    roots_value = payload.get("roots", {})
    if not isinstance(roots_value, dict):
        raise ValueError("Workspace config roots must be a mapping")

    pla_root = pla_root.resolve()
    definitions: dict[str, RootDefinition] = {}
    for name, spec in roots_value.items():
        if not isinstance(name, str) or not WORKSPACE_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid workspace root name: {name!r}")
        if name in _RESERVED_ROOT_NAMES:
            raise ValueError(f"Workspace root name is reserved: {name}")
        if not isinstance(spec, dict):
            raise ValueError(f"Workspace root {name!r} must be a mapping")
        if set(spec) - {"path", "read", "write", "execute"}:
            raise ValueError(f"Workspace root {name!r} contains unsupported fields")

        raw_path = spec.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"Workspace root {name!r} requires a non-empty path")
        expanded = os.path.expandvars(os.path.expanduser(raw_path.strip()))
        target = Path(expanded)
        if not target.is_absolute():
            raise ValueError(f"Workspace root {name!r} path must be absolute")
        target = target.resolve()

        # A user root must never alias, contain, or sit inside the PLA source tree.
        # Otherwise selecting that root could bypass the special PLA private-path policy.
        if _paths_overlap(target, pla_root):
            raise ValueError(
                f"Workspace root {name!r} must not overlap the PLA source tree"
            )

        permissions: dict[str, bool] = {}
        defaults = {"read": True, "write": False, "execute": False}
        for permission, default in defaults.items():
            value = spec.get(permission, default)
            if not isinstance(value, bool):
                raise ValueError(
                    f"Workspace root {name!r} {permission} must be boolean"
                )
            permissions[permission] = value
        if not any(permissions.values()):
            raise ValueError(
                f"Workspace root {name!r} must allow at least one access mode"
            )

        definitions[name] = RootDefinition(
            name=name,
            path=target,
            read=permissions["read"],
            write=permissions["write"],
            execute=permissions["execute"],
        )
    return definitions


def _config_sha256(config_path: Path) -> str | None:
    if not config_path.is_file():
        return None
    return hashlib.sha256(config_path.read_bytes()).hexdigest()


def _read_workspace_payload(config_path: Path) -> dict:
    if not config_path.exists():
        return {"version": WORKSPACE_CONFIG_VERSION, "roots": {}}
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read workspace config: {exc}") from exc
    if payload is None:
        return {"version": WORKSPACE_CONFIG_VERSION, "roots": {}}
    if not isinstance(payload, dict):
        raise ValueError("Workspace config must contain a mapping")
    return payload


def workspace_registry_status(
    config_path: Path,
    pla_root: Path,
) -> dict:
    with _WORKSPACE_CONFIG_LOCK:
        definitions = load_workspace_roots(config_path, pla_root)
        return {
            "config_exists": config_path.is_file(),
            "config_sha256": _config_sha256(config_path),
            "roots": {
                name: {
                    "path": str(definition.path),
                    "read": definition.read,
                    "write": definition.write,
                    "execute": definition.execute,
                }
                for name, definition in sorted(definitions.items())
            },
        }


def _assert_workspace_config_sha256(
    config_path: Path,
    expected_sha256: str | None,
) -> None:
    current = _config_sha256(config_path)
    if current != expected_sha256:
        raise ValueError(
            "Workspace registry changed since inspection; refresh status and retry"
        )


def _render_validated_workspace_config(
    payload: dict,
    config_path: Path,
    pla_root: Path,
) -> str:
    rendered = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=config_path.parent,
            prefix=".workspaces-validate-",
            suffix=".yaml",
        ) as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        load_workspace_roots(temporary_path, pla_root)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return rendered


def _atomic_write_workspace_config(config_path: Path, content: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=config_path.parent,
            prefix=".workspaces-",
            suffix=".tmp",
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, config_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def workspace_registry_upsert(
    config_path: Path,
    pla_root: Path,
    *,
    name: str,
    path: str,
    read: bool,
    write: bool,
    execute: bool,
    expected_sha256: str | None,
) -> dict:
    with _WORKSPACE_CONFIG_LOCK:
        _assert_workspace_config_sha256(config_path, expected_sha256)
        payload = _read_workspace_payload(config_path)
        roots = payload.setdefault("roots", {})
        if not isinstance(roots, dict):
            raise ValueError("Workspace config roots must be a mapping")
        payload["version"] = WORKSPACE_CONFIG_VERSION
        roots[name] = {
            "path": path,
            "read": read,
            "write": write,
            "execute": execute,
        }
        rendered = _render_validated_workspace_config(
            payload, config_path, pla_root
        )
        _atomic_write_workspace_config(config_path, rendered)
        result = workspace_registry_status(config_path, pla_root)
        result["updated_root"] = name
        return result


def workspace_registry_remove(
    config_path: Path,
    pla_root: Path,
    *,
    name: str,
    expected_sha256: str | None,
) -> dict:
    with _WORKSPACE_CONFIG_LOCK:
        _assert_workspace_config_sha256(config_path, expected_sha256)
        payload = _read_workspace_payload(config_path)
        roots = payload.setdefault("roots", {})
        if not isinstance(roots, dict):
            raise ValueError("Workspace config roots must be a mapping")
        if name not in roots:
            raise ValueError(f"Workspace root is not configured: {name}")
        del roots[name]
        payload["version"] = WORKSPACE_CONFIG_VERSION
        rendered = _render_validated_workspace_config(
            payload, config_path, pla_root
        )
        _atomic_write_workspace_config(config_path, rendered)
        result = workspace_registry_status(config_path, pla_root)
        result["removed_root"] = name
        return result


class RootPolicy:
    """Resolve paths against an explicit root registry and enforce its policy."""

    _PLA_PRIVATE_COMPONENTS = frozenset({
        ".git", "state", "cache", "caches", "tmp", "temp", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
    })
    _SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".secret"})
    _DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
    _PLA_PRIVATE_FILES = frozenset({
        ("config", "workspaces.local.yaml"),
        ("config", "windows_actions.local.json"),
    })

    def __init__(self, roots: dict[str, RootDefinition]) -> None:
        if not roots or any(name != definition.name for name, definition in roots.items()):
            raise ValueError("Root registry names must match their definitions")
        self._roots = dict(roots)

    def capabilities(self) -> dict[str, dict[str, bool]]:
        return {
            name: {
                "read": definition.read,
                "write": definition.write,
                "execute": definition.execute,
            }
            for name, definition in self._roots.items()
        }

    def resolve(self, root_name: str, path: str, access: Access = "read") -> ResolvedPath:
        if not isinstance(root_name, str) or root_name not in self._roots:
            raise ValueError(f"Unknown root: {root_name!r}")
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if access not in {"read", "write", "execute"}:
            raise ValueError(f"Unknown path access: {access}")
        if path.startswith(("\\\\", "//")):
            if root_name == "workspace":
                raise ValueError("Path outside workspace is not allowed: UNC path")
            raise ValueError("UNC paths are not allowed")

        definition = self._roots[root_name]
        if not getattr(definition, access):
            raise ValueError(f"Root {root_name!r} does not allow {access} access")
        base = definition.path.resolve()
        candidate = Path(path)
        if candidate.drive and not candidate.is_absolute():
            if root_name == "workspace":
                raise ValueError("Path outside workspace is not allowed: drive-relative path")
            raise ValueError(f"Path outside root {root_name!r} is not allowed: drive-relative path")
        target = (candidate if candidate.is_absolute() else base / candidate).resolve()
        if not self._is_within(base, target):
            if root_name == "workspace":
                raise ValueError(f"Path outside workspace is not allowed: {target}")
            raise ValueError(f"Path outside root {root_name!r} is not allowed: {target}")

        relative_path = target.relative_to(base)
        relative = "." if relative_path == Path(".") else relative_path.as_posix()
        if root_name == "pla":
            self._enforce_pla_policy(relative_path, access)
        return ResolvedPath(definition, target, relative)

    @staticmethod
    def _is_within(base: Path, target: Path) -> bool:
        return _is_within(base, target)

    @classmethod
    def _enforce_pla_policy(cls, relative: Path, access: Access) -> None:
        if relative == Path("."):
            return
        parts = tuple(part.casefold() for part in relative.parts)
        name = parts[-1]
        suffix = PurePath(name).suffix.casefold()
        secret_like = (
            name == ".env"
            or name.startswith(".env.")
            or "credential" in name
            or "secret" in name
            or suffix in cls._SECRET_SUFFIXES
        )
        private = (
            parts in cls._PLA_PRIVATE_FILES
            or any(part in cls._PLA_PRIVATE_COMPONENTS for part in parts)
            or suffix in cls._DATABASE_SUFFIXES
            or secret_like
        )
        if private:
            raise ValueError(
                f"Protected path is not available through root 'pla': {relative.as_posix()}"
            )
        if access == "write" and parts == ("config", "tunnel.yaml"):
            raise ValueError("Protected path is read-only through root 'pla': config/tunnel.yaml")


def build_root_policy(
    workspace: Path,
    pla: Path,
    configured_roots: dict[str, RootDefinition] | None = None,
) -> RootPolicy:
    roots = {
        "workspace": RootDefinition("workspace", workspace),
        "pla": RootDefinition("pla", pla),
    }
    if configured_roots:
        overlap = set(roots).intersection(configured_roots)
        if overlap:
            raise ValueError(
                f"Configured roots conflict with reserved roots: {sorted(overlap)!r}"
            )
        roots.update(configured_roots)
    return RootPolicy(roots)
