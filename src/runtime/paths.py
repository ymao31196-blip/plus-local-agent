"""Explicit desktop data paths; source/headless defaults remain compatible."""
from __future__ import annotations

import os
from pathlib import Path

SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    return Path(os.environ.get("PLA_DATA_ROOT", str(SOURCE_PROJECT_ROOT))).resolve()


def state_root() -> Path:
    return data_root() / "state"
