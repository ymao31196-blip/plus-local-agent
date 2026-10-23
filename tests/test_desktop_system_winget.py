"""Windows App Installer WinGet MCP discovery and Desktop staging rules."""
from __future__ import annotations
import json
from pathlib import Path

from desktop_runtime.system_components import official_winget_mcp, APP_INSTALLER_FAMILY
from desktop_runtime.components import ComponentProject


def _candidate(parent):
    return parent / "Microsoft" / "WindowsApps" / APP_INSTALLER_FAMILY / "WindowsPackageManagerMCPServer.exe"


def test_exact_official_appinstaller_family_only(tmp_path):
    assert official_winget_mcp(tmp_path) is None
    # A neighbouring unreviewed binary must never be selected.
    other = tmp_path / "Microsoft/WindowsApps/BadAppInstaller/WindowsPackageManagerMCPServer.exe"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"malicious")
    assert official_winget_mcp(tmp_path) is None
    correct = _candidate(tmp_path)
    correct.parent.mkdir(parents=True)
    correct.write_bytes(b"Windows application execution alias")
    assert official_winget_mcp(tmp_path) == correct.resolve()


def test_non_absolute_local_appdata_rejected():
    assert official_winget_mcp(Path("relative/path")) is None


def test_stager_picks_official_component_but_never_enters_install_flow(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    candidate = _candidate(local)
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"alias")
    from desktop_runtime import system_components
    monkeypatch.setattr(system_components, "official_winget_mcp", lambda: candidate)
    resources = tmp_path / "resources"
    source = resources / "provider-assets/provider_manifests"
    source.mkdir(parents=True)
    (source / "winget.json").write_text(json.dumps({
        "schema_version": 1, "id": "winget", "autostart": True,
        "runtime": {"kind": "executable_stdio", "command": "WindowsPackageManagerMCPServer.exe", "args": [], "cwd": "."},
        "tool_allowlist": ["find-winget-packages", "install-winget-package"]}))
    project = ComponentProject(tmp_path / "private", resources)
    result = project.stage(18931)
    row = next(x for x in result["providers"] if x["provider_id"] == "winget")
    assert row["command_exists"] is True
    assert row["requested_enabled"] is False
    stored = json.loads((project.manifest_dir / "winget.json").read_text())
    assert stored["runtime"]["command"] == str(candidate)


def test_stager_falls_back_to_explicit_unconfigured_placeholder(tmp_path, monkeypatch):
    from desktop_runtime import system_components
    monkeypatch.setattr(system_components, "official_winget_mcp", lambda: None)
    resources = tmp_path / "resources"
    source = resources / "provider-assets/provider_manifests"
    source.mkdir(parents=True)
    (source / "winget.json").write_text(json.dumps({
        "schema_version": 1, "id": "winget",
        "runtime": {"kind": "executable_stdio", "command": "WindowsPackageManagerMCPServer.exe", "args": [], "cwd": "."},
        "tool_allowlist": ["find-winget-packages"]}))
    project = ComponentProject(tmp_path / "private", resources)
    result = project.stage(18931)
    row = next(x for x in result["providers"] if x["provider_id"] == "winget")
    assert row["command_exists"] is False
    assert "system-components" in row["command"]


def test_upgrade_from_rc7_placeholder_to_rc8_official_alias(tmp_path, monkeypatch):
    """Tracked generated manifests migrate, but user edits are never overwritten."""
    from desktop_runtime import system_components
    resources = tmp_path / "resources"
    source = resources / "provider-assets/provider_manifests"
    source.mkdir(parents=True)
    (source / "winget.json").write_text(json.dumps({
        "schema_version": 1, "id": "winget", "autostart": True,
        "runtime": {"kind": "executable_stdio",
                    "command": "WindowsPackageManagerMCPServer.exe", "args": [], "cwd": "."},
        "tool_allowlist": ["find-winget-packages", "install-winget-package"],
    }))
    project = ComponentProject(tmp_path / "private", resources)
    monkeypatch.setattr(system_components, "official_winget_mcp", lambda: None)
    old = project.stage(18931)
    old_row = next(row for row in old["providers"] if row["provider_id"] == "winget")
    assert "system-components" in old_row["command"]
    official = _candidate(tmp_path / "Local")
    official.parent.mkdir(parents=True)
    official.write_bytes(b"alias")
    monkeypatch.setattr(system_components, "official_winget_mcp", lambda: official)
    updated = project.stage(18931)
    new_row = next(row for row in updated["providers"] if row["provider_id"] == "winget")
    assert new_row["command"] == str(official.resolve())
    assert new_row["requested_enabled"] is False
    manifest = project.manifest_dir / "winget.json"
    custom = json.loads(manifest.read_text())
    custom["runtime"]["args"] = ["--custom"]
    manifest.write_text(json.dumps(custom), encoding="utf-8")
    project.stage(18931)
    assert json.loads(manifest.read_text())["runtime"]["args"] == ["--custom"]
