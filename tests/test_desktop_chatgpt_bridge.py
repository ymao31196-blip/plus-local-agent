"""RC.7 ChatGPT Desktop package broker permission and concurrency regressions."""
import json
from pathlib import Path

import pytest

from desktop_runtime.component_lock import ComponentInstallLock
from desktop_runtime.broker_component import DesktopComponentBroker
from capabilities.provider_runtime_capabilities import desktop_component_descriptors


def test_desktop_package_capabilities_are_scoped_and_declared():
    catalog = {item.id: item for item in desktop_component_descriptors()}
    assert set(catalog) == {
        "runtime.desktop_package_preview", "runtime.desktop_package_commit",
        "runtime.desktop_install_preview", "runtime.desktop_install_status",
    }
    assert catalog["runtime.desktop_package_preview"].risk_level == "read"
    assert catalog["runtime.desktop_install_preview"].requires_confirmation is False
    assert catalog["runtime.desktop_package_commit"].requires_confirmation is True
    assert catalog["runtime.desktop_package_commit"].risk_level == "privileged"
    assert catalog["runtime.desktop_install_status"].risk_level == "read"


def test_desktop_component_broker_refuses_source_only_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PLA_DESKTOP_RUNTIME", raising=False)
    with pytest.raises(ValueError, match="Desktop Runtime"):
        DesktopComponentBroker(tmp_path / "components", tmp_path / "components/provider_manifests")


def test_desktop_component_broker_rejects_mismatched_private_project(tmp_path, monkeypatch):
    data = tmp_path / "private"
    resources = tmp_path / "resources"
    data.mkdir()
    resources.mkdir()
    monkeypatch.setenv("PLA_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("PLA_DATA_ROOT", str(data))
    monkeypatch.setenv("PLA_INSTALL_RESOURCES", str(resources))
    with pytest.raises(ValueError, match="do not match"):
        DesktopComponentBroker(tmp_path / "unrelated-components", tmp_path / "other-manifests")


def test_installer_lock_excludes_second_manager_or_chatgpt_process(tmp_path):
    first = ComponentInstallLock(tmp_path)
    second = ComponentInstallLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(ValueError, match="Another Desktop component installation"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_private_component_lock_not_shared_between_unrelated_users(tmp_path):
    one = ComponentInstallLock(tmp_path / "one")
    two = ComponentInstallLock(tmp_path / "two")
    one.acquire()
    try:
        two.acquire()
        two.release()
    finally:
        one.release()


def test_markitdown_legacy_mcp_compat_pin_and_coldstart_timeout():
    repo = Path(__file__).resolve().parents[1]
    pins = (repo / "provider_specs/markitdown.txt").read_text(encoding="utf-8")
    manifest = json.loads((repo / "provider_manifests/markitdown.json").read_text(encoding="utf-8"))
    assert "markitdown-mcp==0.0.1a4" in pins
    assert "mcp==1.8.1" in pins
    assert "pydantic==2.10.6" in pins
    assert manifest["runtime"]["discovery_timeout_seconds"] == 90


def test_chatgpt_runtime_setup_uses_digest_and_no_script_execution():
    repo = Path(__file__).resolve().parents[1]
    caps = (repo / "src/capabilities/provider_runtime_capabilities.py").read_text(encoding="utf-8")
    broker = (repo / "src/desktop_runtime/broker_component.py").read_text(encoding="utf-8")
    assert 'args.get("expected_sha256")' in caps
    assert '"runtime.desktop_install_preview"' in caps
    assert '"runtime.desktop_install_status"' in caps
    assert "subprocess.Popen(" in broker
    assert "shell=True" not in broker
    assert "expected_sha256" in broker
    assert "ComponentInstallLock" in broker
