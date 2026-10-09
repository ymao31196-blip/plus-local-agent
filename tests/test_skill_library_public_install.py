"""Skill Library public installation contracts for single-user PLA deployments."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_optional_v03_installer_supports_local_reviewed_package_only() -> None:
    installer = (ROOT / "setup_skill_library.ps1").read_text(encoding="utf-8")
    assert "[switch]$ValidateOnly" in installer
    assert "[string]$SourcePath" in installer
    assert 'Join-Path $projectRoot "workspace\\skill-library"' in installer
    assert "chatgpt_skill_library-0\\.3\\.0-" in installer
    assert "chatgpt-skill-library" in installer
    assert "pyproject.toml" in installer
    assert '"-m", "pip", "install", "--no-deps", $package' in installer
    assert "SKILL_LIBRARY_PACKAGE_READY 0.3.0" in installer
    assert 'git clone' not in installer
    assert "SKILL_LIBRARY_WRITE_ROOTS" not in installer


def test_main_installer_requires_opt_in_to_install_optional_server() -> None:
    installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "[string]$SkillLibraryPackage" in installer
    assert 'if (-not [string]::IsNullOrWhiteSpace($SkillLibraryPackage))' in installer
    assert '"-SourcePath", $SkillLibraryPackage' in installer
    assert '-SkillLibraryPackage cannot be used with -SkipProviders.' in installer


def test_public_config_and_manifest_fail_closed() -> None:
    config = json.loads((ROOT / "config" / "skill-library.json").read_text(encoding="utf-8"))
    assert config == {
        "data_dir": "state/skill-library",
        "local_roots": [],
        "writable_roots": [],
    }
    manifest = json.loads(
        (ROOT / "provider_manifests" / "skill-library.json").read_text(encoding="utf-8")
    )
    assert len(manifest["tool_allowlist"]) == 13
    assert manifest["tool_overrides"]["skill_apply_local"]["requires_confirmation"] is True
    assert manifest["tool_overrides"]["skill_prepare"]["requires_confirmation"] is False


def test_docs_describe_install_and_write_permission_boundary() -> None:
    text = (ROOT / "docs" / "skill_library_provider.md").read_text(encoding="utf-8")
    assert "setup_skill_library.ps1" in text
    assert "13项工具" in text
    assert "writable_roots" in text
    assert "skill-library.apply-local" in text
