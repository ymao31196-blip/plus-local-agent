"""Keep Desktop CI running on source PRs and both active push branches."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_ci_guards_master_and_integration_requests():
    workflow = (ROOT / ".github/workflows/desktop-build.yml").read_text(encoding="utf-8")
    assert '  pull_request:\n    branches: ["master"]' in workflow
    assert '  push:\n    branches: ["master", "codex/pla-desktop-v1"]' in workflow
    assert workflow.count('"requirements-dev.txt"') == 2
    assert "python -m pytest -q --tb=short" in workflow
    assert 'Freeze, verify packaged tools and build NSIS' in workflow
    assert 'actions/upload-artifact@v4' in workflow
