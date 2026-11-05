"""Isolated Desktop staging contracts for reviewed unified Office (no live user profile)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from desktop_runtime.components import ComponentProject
from desktop_runtime.installer import ComponentInstaller


ROOT = Path(__file__).resolve().parents[1]


def _sandbox(tmp_path):
    resources = tmp_path / "resources"
    templates = resources / "provider-assets"
    for folder, names in {
        "provider_manifests": ("office.json",),
        "provider_specs": ("office.txt",),
        "providers": ("office_server.py",),
    }.items():
        dest = templates / folder
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(ROOT / folder / name, dest / name)
    return tmp_path / "private", resources


def test_office_stages_as_disabled_reviewed_component(tmp_path):
    data, resources = _sandbox(tmp_path)
    project = ComponentProject(data, resources)
    catalog = project.stage(18931)
    office = next(row for row in catalog["providers"] if row["provider_id"] == "office")
    assert office["requested_enabled"] is False
    assert (project.root / "providers/office_server.py").is_file()
    assert (project.root / "provider_specs/office.txt").is_file()
    runtime = json.loads((project.manifest_dir / "office.json").read_text(encoding="utf-8"))
    assert runtime["autostart"] is False
    assert runtime.get("tool_allowlist") is None
    plan = ComponentInstaller(data, resources).plan("office")
    assert plan["source"] == "bundled"
    assert plan["activates_provider"] is False
    assert plan["specs"][0]["name"] == "office.txt"


def test_office_staging_preserves_existing_user_modified_manifest(tmp_path):
    data, resources = _sandbox(tmp_path)
    project = ComponentProject(data, resources)
    project.stage(18931)
    target = project.manifest_dir / "office.json"
    original = json.loads(target.read_text(encoding="utf-8"))
    original["routing_authority"] = "recommendation"
    modified = json.dumps(original, indent=2)
    target.write_text(modified, encoding="utf-8")
    project.stage(18931)
    assert target.read_text(encoding="utf-8") == modified
