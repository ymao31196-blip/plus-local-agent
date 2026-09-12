from pathlib import Path

import server
from provider_manifest import load_provider_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_provider_catalog():
    assert server.mcp.version == "1.1.0"
    manifests = load_provider_manifests(PROJECT_ROOT)
    assert set(manifests) == {
        "docx",
        "markitdown",
        "pdf",
        "software-migration",
        "winget",
        "windows-management",
    }
    assert manifests["winget"].runtime_kind == "executable_stdio"
    assert manifests["windows-management"].runtime_kind == "isolated_python_stdio"
    assert manifests["software-migration"].runtime_kind == "isolated_python_stdio"


def test_release_entrypoint_documents_exist():
    assert (PROJECT_ROOT / "README.md").is_file()
    assert (PROJECT_ROOT / "docs" / "v1_overview.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.0.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.1.md").is_file()
    assert (PROJECT_ROOT / "CHANGELOG.md").is_file()
    assert (PROJECT_ROOT / "requirements-core.txt").is_file()
    assert (PROJECT_ROOT / "requirements-dev.txt").is_file()


def test_manual_agent_fixture_is_intentionally_broken():
    calculator = (
        PROJECT_ROOT / "workspace" / "agent_test" / "calculator.py"
    ).read_text(encoding="utf-8")
    test_file = (
        PROJECT_ROOT / "workspace" / "agent_test" / "test_calculator.py"
    ).read_text(encoding="utf-8")
    assert "return a - b" in calculator
    assert "assert add(2, 3) == 5" in test_file
