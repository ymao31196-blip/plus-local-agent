"""Regression for the Desktop-only ChatGPT Skill Library installer bridge."""
from pathlib import Path
import pytest
from desktop_runtime.broker_component import DesktopComponentBroker
from capabilities.provider_runtime_capabilities import desktop_component_descriptors, provider_runtime_descriptors


def _fixture(tmp_path, monkeypatch):
    data = tmp_path / "data"
    resources = tmp_path / "resources"
    specs = resources / "provider-assets/provider_specs"
    specs.mkdir(parents=True)
    (specs / "skill-library.txt").write_text("fastmcp==4.0.3\nmcp==2.2.0\n", encoding="utf-8")
    source = tmp_path / "user-skill-source"
    (source / "src/skill_library").mkdir(parents=True)
    (source / "src/skill_library/server.py").write_text("mcp=None\n", encoding="utf-8")
    (source / "pyproject.toml").write_text('[project]\nname="chatgpt-skill-library"\nversion="0.5.0"\n', encoding="utf-8")
    monkeypatch.setenv("PLA_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("PLA_DATA_ROOT", str(data))
    monkeypatch.setenv("PLA_INSTALL_RESOURCES", str(resources))
    return DesktopComponentBroker(data / "components", data / "components/provider_manifests"), source


def test_skill_source_preview_requires_package_and_binds_source_bytes(tmp_path, monkeypatch):
    broker, source = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Select the reviewed Skill Library"):
        broker.install_preview("skill-library")
    reviewed = broker.install_preview("skill-library", str(source))
    assert reviewed["provider_id"] == "skill-library"
    assert reviewed["skill_package"] == str(source.resolve())
    assert reviewed["skill_package_sha256"]
    assert not reviewed["started"]
    (source / "src/skill_library/server.py").write_text("mcp='modified'\n", encoding="utf-8")
    changed = broker.install_preview("skill-library", str(source))
    assert changed["sha256"] != reviewed["sha256"]


def test_source_package_rejected_for_unrelated_provider_and_bundle(tmp_path, monkeypatch):
    broker, source = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="never accepts"):
        broker.install_preview("starter-pack", str(source))
    specs = broker.project.resources / "provider-assets/provider_specs"
    (specs / "computer.txt").write_text("fastmcp==4.0.3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="only for Skill Library"):
        broker.install_preview("computer", str(source))


def test_skill_bridge_source_and_broker_schemas():
    descriptors = {item.id: item for item in desktop_component_descriptors()}
    skill_arg = descriptors["runtime.desktop_install_preview"].input_schema["properties"]["skill_package"]
    assert skill_arg["type"] == ["string", "null"]
    source_descriptors = {item.id: item for item in provider_runtime_descriptors()}
    assert "skill_package" in source_descriptors["runtime.provider_setup"].input_schema["properties"]
