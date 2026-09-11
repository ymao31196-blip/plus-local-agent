"""Named-root path resolution and access policy for local capabilities."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePath
from typing import Literal


Access = Literal["read", "write", "execute"]


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


class RootPolicy:
    """Resolve paths against an explicit root registry and enforce its policy."""

    _PLA_PRIVATE_COMPONENTS = frozenset({
        ".git", "state", "cache", "caches", "tmp", "temp", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
    })
    _SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".secret"})
    _DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})

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
        try:
            common = os.path.commonpath((os.path.normcase(str(base)), os.path.normcase(str(target))))
        except ValueError:
            return False
        return common == os.path.normcase(str(base))

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
            any(part in cls._PLA_PRIVATE_COMPONENTS for part in parts)
            or suffix in cls._DATABASE_SUFFIXES
            or secret_like
        )
        if private:
            raise ValueError(f"Protected path is not available through root 'pla': {relative.as_posix()}")
        if access == "write" and parts == ("config", "tunnel.yaml"):
            raise ValueError("Protected path is read-only through root 'pla': config/tunnel.yaml")


def build_root_policy(
    workspace: Path, pla: Path, rerun_thesis: Path | None = None,
) -> RootPolicy:
    roots = {
        "workspace": RootDefinition("workspace", workspace),
        "pla": RootDefinition("pla", pla),
    }
    if rerun_thesis is not None:
        roots["rerun_thesis"] = RootDefinition("rerun_thesis", rerun_thesis)
    return RootPolicy(roots)
