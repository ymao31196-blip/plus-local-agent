"""Guard the Desktop-only executable permission surface."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "desktop" / "ui"
SCRIPT = UI / "program-permissions.js"


def test_controls_are_bundled_by_tauri():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'src="program-permissions.js"' in html
    for control in (
        "program-permissions-path",
        "program-permissions-add",
        "program-permissions-list",
        "program-permissions-refresh",
    ):
        assert f'id="{control}"' in html
    assert '"frontendDist": "../ui"' in (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")


def test_private_bridge_but_no_mcp_permission_mutator():
    rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    manager = (ROOT / "src/desktop_runtime/manager.py").read_text(encoding="utf-8")
    server = (ROOT / "src/server.py").read_text(encoding="utf-8")
    assert '"program_permissions"' in rust
    assert 'if command == "program_permissions"' in manager
    assert 'PLA_PROGRAM_PERMISSIONS_FILE' in manager
    assert "def program_permissions(" not in server
    assert "program_permissions" not in (ROOT / "src/tooling/internal_tool_executor.py").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_program_permission_ui_javascript_parses():
    result = subprocess.run(
        [shutil.which("node"), "--check", str(SCRIPT)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
