"""Bundled Skill Library: install planning, provenance and safe upgrade rollback."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import subprocess
import zipfile

import pytest

from desktop_runtime import installer as mod
from desktop_runtime.installer import ComponentInstaller
from desktop_runtime.manager import Manager


def reviewed_fixture(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    specs = resources / "provider-assets/provider_specs"
    specs.mkdir(parents=True)
    (specs / "skill-library.txt").write_text(
        "fastmcp==4.0.3\nmcp==2.2.0\nPyYAML==6.0.3\n", encoding="utf-8")
    directory = resources / "provider-assets/packages"
    directory.mkdir()
    wheel = directory / mod.SKILL_LIBRARY_WHEEL_NAME
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("chatgpt_skill_library-0.5.0.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: chatgpt-skill-library\nVersion: 0.5.0\n\n")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    monkeypatch.setattr(mod, "SKILL_LIBRARY_WHEEL_SHA256", digest)
    return tmp_path / "data", resources, wheel


def test_one_click_plan_binds_shipped_package_and_preserves_manual_source(tmp_path, monkeypatch):
    data, resources, wheel = reviewed_fixture(tmp_path, monkeypatch)
    installer = ComponentInstaller(data, resources)
    plan = installer.plan("skill-library")
    assert plan["skill_package_origin"] == "bundled"
    assert plan["skill_package"] == str(wheel.resolve())
    assert plan["skill_package_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert plan["activates_provider"] is False
    assert plan["installs_into_private_data"]
    alternative = installer.plan("skill-library", str(wheel))
    assert alternative["skill_package_origin"] == "local"
    assert alternative["skill_package"] == plan["skill_package"]
    (resources / 'provider-assets/provider_specs/computer.txt').write_text('fastmcp==4.0.3\n')
    with pytest.raises(ValueError, match="only for Skill Library"):
        installer.plan("computer", str(wheel))


def test_missing_or_tampered_wheel_fails_closed_without_private_data_modification(tmp_path, monkeypatch):
    data, resources, wheel = reviewed_fixture(tmp_path, monkeypatch)
    installer = ComponentInstaller(data, resources)
    original = wheel.read_bytes()
    wheel.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        installer.plan("skill-library")
    wheel.unlink()
    with pytest.raises(ValueError, match="wheel missing"):
        installer.plan("skill-library")
    assert not (data / 'components/.provider_envs/skill-library').exists()
    assert not (data / 'components/receipts/skill-library.json').exists()


class FakeChild:
    pid = 4343
    def poll(self):
        return None


def test_gui_sha_confirmation_is_stable_without_user_source(tmp_path, monkeypatch):
    data, resources, wheel = reviewed_fixture(tmp_path, monkeypatch)
    manager = Manager(data, resources)
    commands = []
    def fake_spawn(role, argv):
        commands.append((role, argv))
        manager.children[role] = FakeChild()
        class Lock:
            def release(self):
                pass
        manager._installer_locks[role] = Lock()
        return manager.children[role]
    monkeypatch.setattr(manager, "_spawn_installer", fake_spawn)
    preview = manager.dispatch("provider_install", {
        "provider_id": "skill-library", "skill_package": None,
        "confirmed": False, "expected_sha256": None})
    started = manager.dispatch("provider_install", {
        "provider_id": "skill-library", "skill_package": None,
        "confirmed": True, "expected_sha256": preview["sha256"]})
    assert started["started"] and commands[0][0] == "installer:skill-library"
    assert "--skill-package" not in commands[0][1]


def test_skill_upgrade_final_smoke_failure_restores_previous_environment(tmp_path, monkeypatch):
    data, resources, _ = reviewed_fixture(tmp_path, monkeypatch)
    installer = ComponentInstaller(data, resources)
    plan = installer.plan("skill-library")
    target = Path(plan["environment"])
    (target / "Scripts").mkdir(parents=True)
    (target / "previous.txt").write_text("keep original environment", encoding="utf-8")
    monkeypatch.setattr(installer, "ensure_uv", lambda: None)
    def fake_run(cmd):
        if cmd[1] == "venv":
            (Path(cmd[-1]) / "Scripts").mkdir(parents=True)
            (Path(cmd[-1]) / "Scripts/python.exe").write_bytes(b"mock")
        if cmd[1] == "-I" and str(cmd[0]).startswith(str(target / "Scripts")):
            raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(installer, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        installer._install_skill_library(plan, resources / "provider-assets/provider_specs/skill-library.txt")
    assert (target / "previous.txt").read_text(encoding="utf-8") == "keep original environment"
    assert not any(target.parent.glob(".skill-library-stage-*"))
    assert not any(target.parent.glob(".skill-library-previous-*"))


def test_skill_upgrade_dependency_failure_never_moves_previous_environment(tmp_path, monkeypatch):
    data, resources, _ = reviewed_fixture(tmp_path, monkeypatch)
    installer = ComponentInstaller(data, resources)
    plan = installer.plan("skill-library")
    target = Path(plan["environment"])
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("still installed")
    monkeypatch.setattr(installer, "ensure_uv", lambda: None)
    def fake_run(cmd):
        if cmd[1] == "venv":
            (Path(cmd[-1]) / "Scripts").mkdir(parents=True)
        if cmd[1] == "pip":
            raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(installer, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        installer._install_skill_library(plan, resources / "provider-assets/provider_specs/skill-library.txt")
    assert (target / "previous.txt").read_text() == "still installed"


def test_source_registry_is_outside_replaceable_python_environment(tmp_path, monkeypatch):
    data, resources, _ = reviewed_fixture(tmp_path, monkeypatch)
    installer = ComponentInstaller(data, resources)
    source_registry = installer.project.data / "state/skill-library/sources.json"
    source_registry.parent.mkdir(parents=True)
    source_registry.write_text('{"sources": []}', encoding="utf-8")
    plan = installer.plan("skill-library")
    monkeypatch.setattr(installer, "ensure_uv", lambda: None)
    def fake_run(cmd):
        if cmd[1] == "venv":
            (Path(cmd[-1]) / "Scripts").mkdir(parents=True)
    monkeypatch.setattr(installer, "run", fake_run)
    installer._install_skill_library(plan, resources / "provider-assets/provider_specs/skill-library.txt")
    assert source_registry.read_text(encoding="utf-8") == '{"sources": []}'
    assert Path(plan["environment"]).is_dir()
