"""Build-time contract for PLA and its independent Skill Library release license.

This does not imply that third-party dependencies are Apache licensed.
"""
from __future__ import annotations

from email.parser import Parser
import hashlib
from pathlib import Path
import zipfile

from desktop_runtime.installer import SKILL_LIBRARY_WHEEL_SHA256
from desktop.packaging.prepare_skill_library import SKILL_LIBRARY_SHA256

ROOT = Path(__file__).resolve().parents[1]
WHEEL = ROOT / "desktop/packaging/reviewed/chatgpt_skill_library-0.5.0-py3-none-any.whl"


def test_first_party_apache_license_is_canonical():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert len(license_text) > 11_000
    assert hashlib.sha256(license_text.encode("utf-8")).hexdigest() == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )


def test_skill_library_wheel_license_and_runtime_pin():
    content = WHEEL.read_bytes()
    assert hashlib.sha256(content).hexdigest() == SKILL_LIBRARY_SHA256
    assert SKILL_LIBRARY_SHA256 == SKILL_LIBRARY_WHEEL_SHA256

    with zipfile.ZipFile(WHEEL) as archive:
        members = archive.namelist()
        meta_path, = [name for name in members if name.endswith(".dist-info/METADATA")]
        license_path, = [name for name in members if name.endswith(".dist-info/licenses/LICENSE")]
        metadata = Parser().parsestr(archive.read(meta_path).decode("utf-8"))
        license_text = archive.read(license_path).decode("utf-8")
        assert metadata["Name"] == "chatgpt-skill-library"
        assert metadata["Version"] == "0.5.0"
        assert metadata["License-Expression"] == "Apache-2.0"
        assert "LICENSE" in metadata.get_all("License-File", [])
        assert license_text == (ROOT / "LICENSE").read_text(encoding="utf-8")
        assert not any(name.startswith("skills/") for name in members)


def test_desktop_installer_embeds_first_party_license():
    build = (ROOT / "desktop/packaging/build.ps1").read_text(encoding="utf-8")
    assert '"licenses\\PLA-APACHE-2.0-LICENSE.txt"' in build
    assert 'Join-Path $releaseRoot "LICENSE"' in build

def test_wps_patch_keeps_pinned_upstream_mit_license():
    import json
    specification = json.loads((ROOT / "provider_specs/wps-office.source.json").read_text(encoding="utf-8"))
    assert specification["repository_url"] == "https://github.com/lc2panda/wps-skills.git"
    assert specification["revision"] == "a82533662268b3245f93d8685bc45dffede048b6"
    assert specification["patches"] == ["provider_patches/wps-office/windows-com-compat.patch"]
    patch_root = ROOT / "provider_patches/wps-office"
    assert (patch_root / "windows-com-compat.patch").is_file()
    notice = (patch_root / "UPSTREAM-LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in notice
    assert "Copyright (c) 2026 lc2panda" in notice
    assert specification["revision"] in notice
    assert 'Copy-Item -LiteralPath (Join-Path $projectRoot "provider_patches")' in (
        ROOT / "desktop/packaging/build.ps1"
    ).read_text(encoding="utf-8")
