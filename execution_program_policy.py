"""Shared executable policy for PLA process backends.

This module contains only stable program-name policy and intentionally avoids
importing runtime or transport modules so both the HTTP control plane and the
out-of-process Execution Runner can consume exactly the same allowlist.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping


ALLOWED_PROGRAMS = frozenset({
    "python",
    "python.exe",
    "pytest",
    "pytest.exe",
    "git",
    "git.exe",
    "gh",
    "gh.exe",
    "latexmk",
    "latexmk.exe",
    "xelatex",
    "xelatex.exe",
    "wsl",
    "wsl.exe",
})
SESSION_ALLOWED_PROGRAMS = ALLOWED_PROGRAMS - {"wsl", "wsl.exe"}
MAX_ENV_OVERRIDES = 32
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BLOCKED_ENV_OVERRIDES = frozenset({"PATH", "PATHEXT", "COMSPEC", "PYTHONHOME", "PYTHONPATH"})


def validate_env_overrides(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, Mapping):
        raise TypeError("env must be an object")
    if len(env) > MAX_ENV_OVERRIDES:
        raise ValueError(f"env cannot contain more than {MAX_ENV_OVERRIDES} overrides")
    result: dict[str, str] = {}
    for name, value in env.items():
        if not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid environment variable name: {name!r}")
        if name.upper() in BLOCKED_ENV_OVERRIDES:
            raise ValueError(f"Environment variable override is not allowed: {name}")
        if not isinstance(value, str):
            raise TypeError(f"Environment variable {name} must have a string value")
        if len(value) > 32_768:
            raise ValueError(f"Environment variable {name} value is too long")
        result[name] = value
    return result


def normalized_program_name(program: str) -> str:
    if not isinstance(program, str) or not program:
        raise ValueError("program must be a non-empty string")
    return Path(program).name.casefold()


def require_allowed_program(program: str, *, session: bool = False) -> str:
    name = normalized_program_name(program)
    allowed = SESSION_ALLOWED_PROGRAMS if session else ALLOWED_PROGRAMS
    if name not in allowed:
        raise ValueError(f"Program not allowed: {program}")
    return name
