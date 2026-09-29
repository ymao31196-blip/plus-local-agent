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

sys.path.insert(0, str(SKILL_LIBRARY_ROOT / "src"))
os.environ["SKILL_LIBRARY_REPO"] = str(config["repo"])
os.environ["SKILL_LIBRARY_BRANCH"] = str(config.get("branch", "main"))
os.environ["SKILL_LIBRARY_TRANSPORT"] = str(config.get("transport", "git"))
os.environ["SKILL_LIBRARY_DATA_DIR"] = str(data_dir)

from skill_library.server import main  # noqa: E402


if __name__ == "__main__":
    main()
