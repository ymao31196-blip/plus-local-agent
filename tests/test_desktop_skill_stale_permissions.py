"""Regression for a stale Skill Library permission root after workspace changes.

A stale named root previously killed the MCP stdio child at import time,
surfacing only "Connection closed" in ChatGPT. Repair may revoke grants but
must never automatically add or broaden any root permissions.
"""
import json

import pytest

from desktop_runtime.manager import Manager
from host.workspace_manager import workspace_registry_remove, workspace_registry_status


def _fixture(tmp_path):
    manager = Manager(tmp_path / "private-data", tmp_path / "application-resources")
    folder = tmp_path / "user-skills"
    folder.mkdir()
    manager.dispatch("workspace_save", {
        "mode": "create", "name": "skills", "path": str(folder),
        "read": True, "write": True, "execute": False,
        "expected_sha256": None,
    })
    manager.dispatch("skill_permissions", {
        "local_roots": ["skills", "workspace"],
        "writable_roots": ["skills", "workspace"], "confirmed": True,
    })
    return manager


def _skill_file(manager):
    return manager.components.root / "config/skill-library.desktop.json"


def test_workspace_removal_revokes_stale_skill_access(tmp_path):
    manager = _fixture(tmp_path)
    original = json.loads(_skill_file(manager).read_text("utf-8"))
    assert set(original["local_roots"]) == {"skills", "workspace"}
    sha = workspace_registry_status(manager.config.workspace_path, manager.resources)["config_sha256"]
    manager.dispatch("workspace_remove", {"name": "skills", "expected_sha256": sha})
    value = json.loads(_skill_file(manager).read_text("utf-8"))
    assert value == {"local_roots": ["workspace"], "writable_roots": ["workspace"]}
    assert any("Revoked stale Skill Library" in msg for msg in manager.logs)


def test_existing_invalid_root_is_pruned_before_runtime_spawn(tmp_path):
    manager = _fixture(tmp_path)
    status = workspace_registry_status(manager.config.workspace_path, manager.resources)
    workspace_registry_remove(manager.config.workspace_path, manager.resources,
                              name="skills", expected_sha256=status["config_sha256"])
    assert "skills" in json.loads(_skill_file(manager).read_text("utf-8"))["local_roots"]
    env = manager._environment()
    assert env["PLA_DESKTOP_RUNTIME"] == "1"
    assert json.loads(_skill_file(manager).read_text("utf-8")) == {
        "local_roots": ["workspace"], "writable_roots": ["workspace"]}


def test_workspace_write_permission_reduction_prunes_only_skill_write(tmp_path):
    manager = _fixture(tmp_path)
    status = workspace_registry_status(manager.config.workspace_path, manager.resources)
    folder = tmp_path / "user-skills"
    manager.dispatch("workspace_save", {
        "mode": "update", "name": "skills", "path": str(folder),
        "read": True, "write": False, "execute": False,
        "expected_sha256": status["config_sha256"],
    })
    value = json.loads(_skill_file(manager).read_text("utf-8"))
    assert set(value["local_roots"]) == {"skills", "workspace"}
    assert value["writable_roots"] == ["workspace"]


def test_invalid_skill_permissions_are_not_silently_overwritten(tmp_path):
    manager = _fixture(tmp_path)
    path = _skill_file(manager)
    broken = b'{"local_roots": "not a list", "writable_roots":[]}'
    path.write_bytes(broken)
    with pytest.raises(ValueError, match="Invalid desktop Skill permissions"):
        manager._environment()
    assert path.read_bytes() == broken


def test_valid_skill_permissions_remain_unchanged_when_all_roots_exist(tmp_path):
    manager = _fixture(tmp_path)
    path = _skill_file(manager)
    original = path.read_bytes()
    result = manager._reconcile_skill_permissions()
    assert result == {"revoked_read": [], "revoked_write": []}
    assert path.read_bytes() == original
