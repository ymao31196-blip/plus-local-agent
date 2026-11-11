"""Consolidated Office UI and migration contract.

Legacy WPS and enhancement engines remain installable as backend dependencies,
but no longer appear as separate bundled providers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from desktop_runtime.components import ComponentProject
from desktop_runtime.installer import ComponentInstaller, CORE_BUNDLE_IDS

ROOT = Path(__file__).resolve().parents[1]


def _minimal_manifest(provider_id):
    return {
        "schema_version": 1, "id": provider_id, "autostart": False,
        "runtime": {
            "kind": "executable_stdio",
            "command": "C:/Windows/System32/cmd.exe",
            "args": [], "cwd": ".",
        },
        "tool_allowlist": ["ping"],
    }


def test_staging_retires_only_provenance_tracked_legacy_office_entries(tmp_path):
    resources = tmp_path / "resources"
    templates = resources / "provider-assets/provider_manifests"
    templates.mkdir(parents=True)
    (templates / "office.json").write_bytes(
        (ROOT / "provider_manifests/office.json").read_bytes()
    )
    project = ComponentProject(tmp_path / "private", resources)
    provenance = {}
    for name in ("wps-office", "office-enhancement"):
        raw = json.dumps(_minimal_manifest(name)).encode("utf-8")
        path = project.manifest_dir / (name + ".json")
        path.write_bytes(raw)
        provenance[name] = hashlib.sha256(raw).hexdigest()
    project.asset_record_path.write_text(json.dumps(provenance), encoding="utf-8")
    # Simulate a real NSIS in-place upgrade: obsolete bundled templates
    # survive next to the new unified office manifest.
    for name in ("wps-office", "office-enhancement"):
        (templates / (name + ".json")).write_bytes(
            (project.manifest_dir / (name + ".json")).read_bytes()
        )
    project.preferences_path.parent.mkdir(parents=True, exist_ok=True)
    project.preferences_path.write_text(
        json.dumps({"enabled": ["wps-office", "office-enhancement", "office"]}),
        encoding="utf-8",
    )
    # Backend engines are dependencies of office; retirement cannot touch them.
    backend = project.root / ".provider_sources/wps-office/wps-office-mcp/dist"
    backend.mkdir(parents=True)
    (backend / "index.js").write_text("fixture", encoding="utf-8")
    enhancement = project.root / ".provider_envs/office-enhancement"
    enhancement.mkdir(parents=True)
    (enhancement / "keep.txt").write_text("fixture", encoding="utf-8")

    one = project.stage(18931)
    assert {p["provider_id"] for p in one["providers"]} == {"office"}
    assert project.preferences()["enabled"] == ["office"]
    assert (backend / "index.js").read_text() == "fixture"
    assert (enhancement / "keep.txt").read_text() == "fixture"
    records = json.loads(project.asset_record_path.read_text())
    assert "wps-office" not in records and "office-enhancement" not in records
    two = project.stage(18931)
    assert {p["provider_id"] for p in two["providers"]} == {"office"}
    assert sorted(path.stem for path in templates.glob("*.json")) == [
        "office", "office-enhancement", "wps-office"
    ], "Migration must not write into read-only installed resources"


def test_staging_preserves_user_edited_legacy_manifest(tmp_path):
    resources = tmp_path / "resources"
    templates = resources / "provider-assets/provider_manifests"
    templates.mkdir(parents=True)
    (templates / "office.json").write_bytes(
        (ROOT / "provider_manifests/office.json").read_bytes()
    )
    project = ComponentProject(tmp_path / "private", resources)
    custom_path = project.manifest_dir / "wps-office.json"
    custom_path.write_text(json.dumps(_minimal_manifest("wps-office")), encoding="utf-8")
    project.asset_record_path.write_text(
        json.dumps({"wps-office": "a" * 64}), encoding="utf-8",
    )
    project.stage(18931)
    assert custom_path.is_file(), "User edits must never be deleted silently"


def test_unified_office_plan_includes_both_reviewed_backends():
    resources = ROOT / "desktop/src-tauri/resources"
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as folder:
        installer = ComponentInstaller(Path(folder), resources)
        plan = installer.plan("office")
    assert [p["provider_id"] for p in plan["dependencies"]] == [
        "office-enhancement", "wps-office"
    ]
    assert all(p["source"] == "bundled" for p in plan["dependencies"])
    assert plan["provider_id"] == "office"
    assert "office" in CORE_BUNDLE_IDS
    assert "office-enhancement" not in CORE_BUNDLE_IDS
    assert (resources / "provider-assets/provider_specs/office-enhancement.txt").is_file()
    assert (resources / "provider-assets/provider_specs/wps-office.source.json").is_file()


def test_only_office_is_bundled_as_a_public_provider():
    manifests = ROOT / "provider_manifests"
    packaged = ROOT / "desktop/src-tauri/resources/provider-assets/provider_manifests"
    for path in (manifests, packaged):
        assert (path / "office.json").is_file()
        assert not (path / "wps-office.json").exists()
        assert not (path / "office-enhancement.json").exists()



def test_nsis_postinstall_retires_only_old_public_office_manifests():
    hooks = (ROOT / "desktop/packaging/installer-hooks.nsh").read_text(encoding="utf-8")
    assert "!macro NSIS_HOOK_POSTINSTALL" in hooks
    for name in ("wps-office", "office-enhancement"):
        assert f"provider_manifests\\{name}.json" in hooks
    assert "provider_specs\\wps-office" not in hooks
    assert "provider_specs\\office-enhancement" not in hooks
    assert "NSIS_HOOK_PREUNINSTALL" in hooks


def test_retired_backend_receipts_are_hidden_but_preserved(tmp_path):
    from desktop_runtime.manager import Manager

    manager = Manager(tmp_path / "private",
                      ROOT / "desktop/src-tauri/resources")
    manager.components.stage(18931)
    receipts = manager.components.root / "receipts"
    receipts.mkdir(exist_ok=True)
    for name in ("office", "wps-office", "office-enhancement"):
        (receipts / (name + ".json")).write_text(
            '{"status":"installed"}', encoding="utf-8",
        )
    catalog = manager.dispatch("provider_catalog", {})
    ids = {row["provider_id"] for row in catalog["providers"]}
    assert "office" in ids
    assert "wps-office" not in ids
    assert "office-enhancement" not in ids
    displayed = manager.dispatch("installation_status", {})["installed_receipts"]
    assert "office" in displayed
    assert "wps-office" not in displayed
    assert "office-enhancement" not in displayed
    assert all((receipts / (name + ".json")).exists()
               for name in ("office", "wps-office", "office-enhancement"))
