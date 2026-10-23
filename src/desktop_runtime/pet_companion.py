"""Lifecycle and explicitly opt-in state for the local Desktop pet companion.

This component is deliberately NOT an MCP tool or a model-triggered provider.
Only the Desktop manager may start its owned GUI child.
"""
from __future__ import annotations

import json
from pathlib import Path

from desktop_runtime.config import atomic_write

PET_ID = "agent-pet"
PET_REQUIRED_ANIMATIONS = {
    "idle": 6,
    "work": 6,
    "waiting": 6,
    "review": 6,
    "failed": 8,
}


class PetCompanion:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).resolve()
        self.directory = self.data_root / "components" / PET_ID
        self.frames = self.directory / "frames3d"
        self.selection_file = self.data_root / "config" / "agent-pet.json"

    def enabled(self) -> bool:
        if not self.selection_file.exists():
            return False
        state = json.loads(self.selection_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or set(state) != {"enabled"} or type(state["enabled"]) is not bool:
            raise ValueError("Invalid agent-pet preference; no changes made")
        return state["enabled"]

    def set_enabled(self, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("Pet enabled must be boolean")
        if value and not self.installed():
            raise ValueError("Pet assets are missing; install your own licensed PNG frames before enabling")
        # Selection is private and local; not a provider, no shell command involved.
        atomic_write(self.selection_file, json.dumps({"enabled": value}).encode("utf-8"))
        return value

    def installed(self) -> bool:
        if not self.frames.is_dir() or self.frames.is_symlink():
            return False
        # Reject redirections so no arbitrary executable or external image path
        # can be introduced via the data-owned component directory.
        if self.directory.is_symlink():
            return False
        try:
            for animation, count in PET_REQUIRED_ANIMATIONS.items():
                for index in range(count):
                    file = self.frames / f"{animation}_{index}.png"
                    if not file.is_file() or file.is_symlink():
                        return False
                    if not file.resolve().is_relative_to(self.directory.resolve()):
                        return False
        except (OSError, RuntimeError):
            return False
        return True

    def status(self, *, runtime_alive: bool, process=None) -> dict:
        alive = process is not None and process.poll() is None
        error = ""
        try:
            selected = self.enabled()
        except (OSError, ValueError):
            # A damaged optional preference must not break Desktop status or
            # turn a healthy Runtime startup into a failed startup.
            selected = False
            error = "Invalid or unreadable pet preference; disable the pet to reset it"
        installed = self.installed()
        state = ("running" if alive else "not_installed" if not installed else
                 "disabled" if not selected else "waiting_runtime" if not runtime_alive
                 else "not_running")
        return {
            "component_id": PET_ID,
            "kind": "local_companion",
            "name": "PLA Agent Pet",
            "enabled": selected,
            "installed": installed,
            "runtime_alive": runtime_alive,
            "state": state,
            "pid": process.pid if alive else None,
            "assets_directory": str(self.directory),
            "error": error,
        }
