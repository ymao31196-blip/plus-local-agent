"""Regression coverage for the installed Desktop component lifecycle and feedback.

All tests use isolated temporary directories; no live Desktop configuration is touched.
"""
import json
from pathlib import Path

import pytest

from desktop_runtime.manager import Manager


def _wps_fixture(tmp_path):
    resources = tmp_path / "resources"
    templates = resources / "provider-assets" / "provider_manifests"
    templates.mkdir(parents=True)
    resources.joinpath("node.exe").write_bytes(b"node-fixture")
    templates.joinpath("wps-office.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "wps-office",
        "autostart": True,
        "runtime": {
            "kind": "executable_stdio",
            "command": "node.exe",
            "args": ["dist/index.js"],
            "cwd": ".provider_sources/wps-office/wps-office-mcp",
        },
        "tool_allowlist": ["ping"],
    }), encoding="utf-8")
    specs = resources / "provider-assets" / "provider_specs"
    specs.mkdir()
    (specs / "wps-office.source.json").write_text("{}", encoding="utf-8")
    return Manager(tmp_path / "private", resources)


def test_missing_wps_cwd_is_visible_and_cannot_be_enabled(tmp_path):
    manager = _wps_fixture(tmp_path)
    row = manager.dispatch("provider_catalog", {})["providers"][0]
    assert row["provider_id"] == "wps-office"
    assert row["install_supported"] is True
    assert row["command_exists"] is True
    assert row["directory_exists"] is False
    assert row["execution_files_present"] is False
    with pytest.raises(ValueError, match="working directory missing"):
        manager.dispatch("provider_action", {
            "action": "enable", "provider_id": "wps-office", "confirmed": True,
        })
    assert manager.components.preferences()["enabled"] == []


def test_missing_install_spec_explained_without_claiming_installability(tmp_path):
    manager = _wps_fixture(tmp_path)
    (manager.resources / "provider-assets/provider_specs/wps-office.source.json").unlink()
    row = manager.dispatch("provider_catalog", {})["providers"][0]
    assert row["install_supported"] is False
    assert row["execution_files_present"] is False


class _Child:
    def __init__(self, returncode):
        self.pid = 4567
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_install_status_uses_receipt_and_exposes_bounded_logs(tmp_path):
    manager = _wps_fixture(tmp_path)
    manager.children["installer:wps-office"] = _Child(0)
    manager.logs.append("[installer:wps-office] STEP installing")
    result = manager.dispatch("installation_status", {})
    assert result["jobs"][0]["state"] == "failed", "Exit 0 without receipt must not claim success"
    assert result["jobs"][0]["logs"] == ["[installer:wps-office] STEP installing"]

    receipts = manager.components.root / "receipts"
    receipts.mkdir()
    (receipts / "wps-office.json").write_text('{"status":"installed"}', encoding="utf-8")
    result = manager.dispatch("installation_status", {})
    assert result["jobs"][0]["state"] == "installed"
    assert result["installed_receipts"] == ["wps-office"]

    manager.children["installer:wps-office"] = _Child(None)
    assert manager.dispatch("installation_status", {})["jobs"][0]["state"] == "running"
    manager.children["installer:wps-office"] = _Child(1)
    assert manager.dispatch("installation_status", {})["jobs"][0]["state"] == "failed"


def test_install_and_skill_onboarding_buttons_have_real_handlers():
    # Static HTML/JS contract: dynamic browser behavior still needs native E2E.
    from html.parser import HTMLParser

    class IDs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.names = set()

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "id":
                    self.names.add(value)

    repo = Path(__file__).resolve().parents[1]
    ids = IDs()
    ids.feed((repo / "desktop/ui/index.html").read_text(encoding="utf-8"))
    scripts = (repo / "desktop/ui/components.js").read_text(encoding="utf-8")
    for element_id in (
        "install-panel", "install-live", "preview-install", "start-install",
        "installation-refresh", "skill-install-link", "skill-quick-add",
        "skill-quick-id", "skill-quick-path", "skill-list-items",
    ):
        assert element_id in ids.names
        assert f"$('{element_id}')" in scripts, element_id
    assert "watchedInstallJob" in scripts
    assert "refreshInstallationStatus" in scripts
    assert "action:'manage'" in scripts
    assert "action:'sync'" in scripts


def test_desktop_release_version_consistent_across_build_systems():
    import tomllib
    from desktop_runtime.config import VERSION

    root = Path(__file__).resolve().parents[1]
    desktop = root / "desktop"
    tauri = json.loads((desktop / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((desktop / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((desktop / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    locked = [item for item in cargo_lock["package"] if item["name"] == "pla-desktop"]
    assert len(locked) == 1
    assert {
        VERSION, tauri["version"], package["version"], npm_lock["version"],
        npm_lock["packages"][""]["version"], cargo["package"]["version"],
        locked[0]["version"],
    } == {"1.0.0-rc.7"}


def test_desktop_build_discovers_existing_rustup_and_mingw_without_persistent_path_change():
    script = (Path(__file__).resolve().parents[1] / "desktop/packaging/build.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $cargoHome "bin"' in script
    assert 'Join-Path $buildRoot "toolchain\\mingw64\\bin"' in script
    assert 'Get-Command cargo.exe -ErrorAction SilentlyContinue' in script
    assert 'Get-Command gcc.exe -ErrorAction SilentlyContinue' in script
    assert '$env:PATH = $originalPath' in script
    assert "setx " not in script.lower()


def test_desktop_build_uses_native_exit_codes_instead_of_powershell_51_stderr():
    script = (Path(__file__).resolve().parents[1] / "desktop/packaging/build.ps1").read_text(encoding="utf-8")
    assert '$ErrorActionPreference = "Stop"' in script
    assert 'function Invoke-LoggedNative' in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert '$ErrorActionPreference = $previousPreference' in script
    assert '. $Command' in script  # Native process must share the function scope for LASTEXITCODE.
    assert '$exitCode = $LASTEXITCODE' in script
    assert 'if ($null -eq $exitCode -or $exitCode -ne 0)' in script
    assert script.count('Invoke-LoggedNative "') == 7
    assert script.count('2>&1 |') == 7
    assert script.count('-Encoding utf8 -ErrorAction Stop') == 7
    assert '*>' not in script, "Old PowerShell native-output redirections must not reappear"
