"""Desktop v1.2.0 release metadata and upgrade contracts."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_release_version_consistent():
    desktop = ROOT / "desktop"
    version = "1.2.0"
    assert json.loads((desktop / "package.json").read_text(encoding="utf-8"))["version"] == version
    lock = json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == version
    assert lock["packages"][""]["version"] == version
    config = json.loads((desktop / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert config["version"] == version
    cargo = (desktop / "src-tauri/Cargo.toml").read_text(encoding="utf-8")
    assert re.search(r'(?m)^version = "1\.2\.0"$', cargo)
    cargo_lock = (desktop / "src-tauri/Cargo.lock").read_text(encoding="utf-8")
    assert re.search(r'(?m)^name = "pla-desktop"\nversion = "1\.2\.0"$', cargo_lock)


def test_release_docs_point_to_v120_and_preserve_signed_update_caveat():
    tag = "desktop-v1.2.0"
    installer = "PLA.Desktop_1.2.0_x64-setup.exe"
    notes = ROOT / "docs/desktop-v1.2.0-release-notes.md"
    assert notes.is_file()
    for name in ("README.md", "README.zh-CN.md"):
        intro = "\n".join((ROOT / name).read_text(encoding="utf-8").splitlines()[:35])
        assert tag in intro
        assert installer in intro
        assert "SHA256SUMS.txt" in intro
        assert "v1.2.0" in intro
    content = notes.read_text(encoding="utf-8")
    assert tag in content and installer in content
    assert "source_dirty=false" in content
    assert "updater_signature=true" in content


def test_office_inplace_upgrade_hook_and_backend_preservation():
    nsis = (ROOT / "desktop/packaging/installer-hooks.nsh").read_text(encoding="utf-8")
    assert "NSIS_HOOK_POSTINSTALL" in nsis
    for retired in ("wps-office", "office-enhancement"):
        assert "provider_manifests\\" + retired + ".json" in nsis
    assert "provider_specs\\wps-office" not in nsis
    assert (ROOT / "provider_specs/wps-office.source.json").is_file()
    assert (ROOT / "provider_specs/office-enhancement.txt").is_file()
    assert (ROOT / "provider_manifests/office.json").is_file()
    assert not (ROOT / "provider_manifests/wps-office.json").exists()
    assert not (ROOT / "provider_manifests/office-enhancement.json").exists()
