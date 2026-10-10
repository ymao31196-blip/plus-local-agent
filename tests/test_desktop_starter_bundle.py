"""Official MCP starter bundle preview, confirmation, state and UI contracts."""
import json
from pathlib import Path

import pytest

from desktop_runtime.installer import ComponentInstaller, CORE_BUNDLE_IDS, core_bundle_plan
from desktop_runtime.manager import Manager


def _resources(tmp_path):
    root = tmp_path / "resources"
    specs = root / "provider-assets/provider_specs"
    specs.mkdir(parents=True)
    for name in CORE_BUNDLE_IDS:
        (specs / (name + ".txt")).write_text("fastmcp==4.0.3\n", encoding="utf-8")
    return root


def test_starter_bundle_is_exactly_seven_bundled_specs_no_activation(tmp_path):
    m = Manager(tmp_path / "private", _resources(tmp_path))
    preview = m.dispatch("provider_bundle", {"confirmed": False, "expected_sha256": None})
    assert tuple(preview["providers"]) == CORE_BUNDLE_IDS
    assert len(preview["plans"]) == 7
    assert all(plan["source"] == "bundled" and not plan["activates_provider"] for plan in preview["plans"])
    assert preview["activated"] is False
    assert preview["started"] is False
    assert m.components.preferences()["enabled"] == []


def test_starter_bundle_refuses_changed_plan_or_non_bundled_spec(tmp_path, monkeypatch):
    m = Manager(tmp_path / "private", _resources(tmp_path))
    first = m.dispatch("provider_bundle", {"confirmed": False, "expected_sha256": None})
    with pytest.raises(ValueError, match="changed"):
        m.dispatch("provider_bundle", {"confirmed": True, "expected_sha256": "bad"})
    with pytest.raises(ValueError):
        m.dispatch("provider_bundle", {"confirmed": "yes", "expected_sha256": first["sha256"]})
    specs = m.resources / "provider-assets/provider_specs"
    (specs / "pdf.txt").unlink()
    with pytest.raises(ValueError):
        m.dispatch("provider_bundle", {"confirmed": False, "expected_sha256": None})
    assert not m.children


class _CompletedProcess:
    pid = 5555
    returncode = None
    def poll(self):
        return self.returncode


def test_starter_bundle_background_job_and_receipts(tmp_path, monkeypatch):
    m = Manager(tmp_path / "private", _resources(tmp_path))
    captured = []
    child = _CompletedProcess()
    def spawn(role, command, env):
        captured.append((role, command))
        m.children[role] = child
        return child
    monkeypatch.setattr(m, "_spawn", spawn)
    plan = m.dispatch("provider_bundle", {"confirmed": False, "expected_sha256": None})
    started = m.dispatch("provider_bundle", {"confirmed": True, "expected_sha256": plan["sha256"]})
    assert started["job"] == "installer:starter-pack" and started["started"]
    assert "--bundle" in captured[0][1]
    status = m.dispatch("installation_status", {})
    assert status["jobs"][0]["state"] == "running"
    child.returncode = 0
    assert m.dispatch("installation_status", {})["jobs"][0]["state"] == "failed"
    receipts = m.components.root / "receipts"
    receipts.mkdir(parents=True)
    for item in CORE_BUNDLE_IDS:
        (receipts / (item + ".json")).write_text(json.dumps({"status": "installed"}))
    assert m.dispatch("installation_status", {})["jobs"][0]["state"] == "installed"
    assert not m.components.preferences()["enabled"]


def test_installed_tauri_ui_contract_exposes_bundle_and_custom_packages():
    root = Path(__file__).resolve().parents[1]
    rust = (root / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    html = (root / "desktop/ui/index.html").read_text(encoding="utf-8")
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    assert '"provider_bundle"' in rust and '"provider_package"' in rust
    for ident in ("preview-bundle", "start-bundle", "bundle-preview",
                  "custom-package-id", "custom-package-kind", "custom-package-pins",
                  "custom-package-preview", "custom-package-save"):
        assert f'id="{ident}"' in html
        assert f"$('{ident}')" in js or f"output('{ident}'" in js
    assert "details" in html and "provider-card" in js
    assert "Skill Library已安装的文件不会丢失" in js
    assert "Skill Library已连接，但尚未添加Skill来源" in js
    assert "启停：${enableLabel}" in js and "连接：${currentStatus}" in js
