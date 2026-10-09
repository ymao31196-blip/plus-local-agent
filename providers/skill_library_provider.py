from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "skill-library.json"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "skill-library.local.json"
SKILL_LIBRARY_ROOT = PROJECT_ROOT / "workspace" / "skill-library"

config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
if LOCAL_CONFIG_PATH.is_file():
    local_config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(local_config, dict) or set(local_config) - {
        "repo", "branch", "transport", "data_dir", "local_roots", "writable_roots"
    }:
        raise ValueError("Unrecognized fields in skill-library.local.json")
    config.update(local_config)
data_dir = Path(config.get("data_dir", "state/skill-library"))
if not data_dir.is_absolute():
    data_dir = PROJECT_ROOT / data_dir

local_roots = config.get("local_roots", [])
if not isinstance(local_roots, list) or not all(
    isinstance(root, str) and root.strip() for root in local_roots
):
    raise ValueError("skill-library.local_roots must be a list of paths")
approved_roots = []
for relative_root in local_roots:
    raw_root = Path(relative_root)
    resolved = (PROJECT_ROOT / raw_root).resolve()
    if raw_root.is_absolute() or not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("skill-library.local_roots must stay inside PLA root")
    approved_roots.append(str(resolved))
if approved_roots:
    os.environ["SKILL_LIBRARY_LOCAL_ROOTS"] = os.pathsep.join(approved_roots)
else:
    os.environ.pop("SKILL_LIBRARY_LOCAL_ROOTS", None)

writable_roots = config.get("writable_roots", [])
if not isinstance(writable_roots, list) or not all(
    isinstance(root, str) and root.strip() for root in writable_roots
):
    raise ValueError("skill-library.writable_roots must be a list of paths")
approved_write_roots = []
for relative_root in writable_roots:
    raw_root = Path(relative_root)
    resolved = (PROJECT_ROOT / raw_root).resolve()
    if raw_root.is_absolute() or not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("skill-library.writable_roots must stay inside PLA root")
    if not any(resolved == read_root or read_root in resolved.parents
               for read_root in map(Path, approved_roots)):
        raise ValueError("skill-library.writable_roots must be inside local_roots")
    approved_write_roots.append(str(resolved))
if approved_write_roots:
    os.environ["SKILL_LIBRARY_WRITE_ROOTS"] = os.pathsep.join(approved_write_roots)
else:
    os.environ.pop("SKILL_LIBRARY_WRITE_ROOTS", None)

source_package = SKILL_LIBRARY_ROOT / "src" / "skill_library"
if source_package.is_dir():
    sys.path.insert(0, str(SKILL_LIBRARY_ROOT / "src"))
if config.get("repo"):
    os.environ["SKILL_LIBRARY_REPO"] = str(config["repo"])
os.environ["SKILL_LIBRARY_BRANCH"] = str(config.get("branch", "main"))
os.environ["SKILL_LIBRARY_TRANSPORT"] = str(config.get("transport", "git"))
os.environ["SKILL_LIBRARY_DATA_DIR"] = str(data_dir)

try:
    from skill_library.server import main  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "skill_library":
        raise RuntimeError(
            "Skill Library is not installed. Install chatgpt-skill-library>=0.3.0 "
            "in the provider environment or supply a checkout under workspace/skill-library."
        ) from exc
    raise


if __name__ == "__main__":
    main()
