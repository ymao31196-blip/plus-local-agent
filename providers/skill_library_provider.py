from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "skill-library.json"
SKILL_LIBRARY_ROOT = PROJECT_ROOT / "workspace" / "skill-library"

config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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

sys.path.insert(0, str(SKILL_LIBRARY_ROOT / "src"))
os.environ["SKILL_LIBRARY_REPO"] = str(config["repo"])
os.environ["SKILL_LIBRARY_BRANCH"] = str(config.get("branch", "main"))
os.environ["SKILL_LIBRARY_TRANSPORT"] = str(config.get("transport", "git"))
os.environ["SKILL_LIBRARY_DATA_DIR"] = str(data_dir)

from skill_library.server import main  # noqa: E402


if __name__ == "__main__":
    main()
