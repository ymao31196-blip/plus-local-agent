"""Desktop-managed Q-style Agent Pet lifecycle and opt-in tests."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_runtime.manager import Manager
from desktop_runtime.pet_companion import PetCompanion, PET_REQUIRED_ANIMATIONS


def install_test_frames(root: Path):
    frames = root / "components" / "agent-pet" / "frames3d"
    frames.mkdir(parents=True)
    for label, count in PET_REQUIRED_ANIMATIONS.items():
        for index in range(count):
            (frames / f"{label}_{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


class FakeProcess:
    def __init__(self, pid=12345):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


def test_pet_opt_in_and_asset_checks(tmp_path):
    pet = PetCompanion(tmp_path)
    assert not pet.enabled()
    assert not pet.installed()
    with pytest.raises(ValueError, match="assets"):
        pet.set_enabled(True)
    install_test_frames(tmp_path)
    assert pet.installed()
    assert pet.set_enabled(True)
    assert PetCompanion(tmp_path).enabled()
    assert pet.set_enabled(False) is False
    assert not PetCompanion(tmp_path).enabled()


def test_pet_rejects_unexpected_preference_and_symlink(tmp_path):
    pet = PetCompanion(tmp_path)
    pet.selection_file.parent.mkdir(parents=True)
    pet.selection_file.write_text('{"enabled":"true"}', encoding="utf-8")
    with pytest.raises(ValueError):
        pet.enabled()


def test_manager_explicit_enable_disable_owns_only_pet(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    install_test_frames(manager.config.root)
    runtime = FakeProcess(101)
    manager.children["runtime"] = runtime
    spawned = []
    killed = []

    def fake_spawn(role, argv, env):
        assert role == "agent-pet"
        assert argv[-1] == "pet"
        assert Path(env["PLA_PET_FRAMES_DIR"]).is_dir()
        assert env["PLA_PET_EVENT_DB"].endswith("events.sqlite3")
        process = FakeProcess(202)
        spawned.append(process)
        manager.children[role] = process
        return process

    monkeypatch.setattr(manager, "_environment", lambda: {})
    monkeypatch.setattr(manager, "_spawn", fake_spawn)
    monkeypatch.setattr("desktop_runtime.manager.terminate_owned_process_tree", lambda child: killed.append(child.pid))
    with pytest.raises(ValueError, match="confirmation"):
        manager.dispatch("pet_action", {"enabled": True, "confirmed": False})
    assert not manager.pet.enabled()
    out = manager.dispatch("pet_action", {"enabled": True, "confirmed": True})
    assert out["state"] == "running"
    assert out["pid"] == 202
    assert len(spawned) == 1
    assert manager.dispatch("pet_status", {})["state"] == "running"
    out = manager.dispatch("pet_action", {"enabled": False, "confirmed": True})
    assert out["state"] == "disabled"
    assert killed == [202]
    assert manager.children["runtime"] is runtime
    assert not manager.pet.enabled()


def test_pet_starts_when_runtime_ready_and_stops_on_runtime_stop(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    install_test_frames(manager.config.root)
    manager.pet.set_enabled(True)
    roles = []
    terminated = []

    def fake_spawn(role, argv, env):
        roles.append(role)
        child = FakeProcess(501 if role == "runtime" else 502)
        manager.children[role] = child
        return child

    monkeypatch.setattr(manager, "_environment", lambda: {})
    monkeypatch.setattr(manager, "_spawn", fake_spawn)
    monkeypatch.setattr(manager, "_free_port", lambda port: None)
    async def mock_probe(verify=False):
        return True
    monkeypatch.setattr(manager, "_probe", mock_probe)
    monkeypatch.setattr(manager, "status", lambda: {"runtime": "ready"})
    monkeypatch.setattr("desktop_runtime.manager.terminate_owned_process_tree", lambda proc: terminated.append(proc.pid))
    assert manager.start()["runtime"] == "ready"
    assert roles == ["runtime", "agent-pet"]
    assert manager._alive("agent-pet")
    # Not duplicated on repeated starts.
    assert manager.start()["runtime"] == "ready"
    assert roles == ["runtime", "agent-pet"]
    manager.stop()
    assert terminated == [502, 501]
    assert manager.pet.enabled()  # choice persists across stops
    assert manager.dispatch("pet_status", {})["state"] == "waiting_runtime"


def test_pet_manually_closed_is_not_respawned_same_runtime(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    install_test_frames(manager.config.root)
    manager.pet.set_enabled(True)
    manager.children["runtime"] = FakeProcess(10)
    manager.children["agent-pet"] = FakeProcess(20)
    manager.children["agent-pet"].returncode = 0
    monkeypatch.setattr(manager, "_spawn", lambda *a, **k: pytest.fail("should not respawn"))
    assert manager._start_pet_if_selected() is False


def test_invalid_pet_preference_does_not_break_runtime_start(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    manager.pet.selection_file.parent.mkdir(parents=True, exist_ok=True)
    manager.pet.selection_file.write_text('{"enabled":"true"}', encoding="utf-8")
    spawned = []
    def spawn(role, argv, env):
        spawned.append(role)
        manager.children[role] = FakeProcess()
        return manager.children[role]
    async def probe(verify=False):
        return True
    monkeypatch.setattr(manager, "_spawn", spawn)
    monkeypatch.setattr(manager, "_probe", probe)
    monkeypatch.setattr(manager, "_free_port", lambda port: None)
    monkeypatch.setattr(manager, "_environment", lambda: {})
    monkeypatch.setattr(manager, "status", lambda: manager.pet.status(runtime_alive=True))
    result = manager.start()
    assert result["enabled"] is False
    assert result["error"]
    assert spawned == ["runtime"]
    assert manager.pet.set_enabled(False) is False
    assert manager.pet.status(runtime_alive=True)["error"] == ""


def test_explicit_reenable_after_manual_close(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "data", tmp_path / "resources")
    install_test_frames(manager.config.root)
    manager.pet.set_enabled(True)
    manager.children["runtime"] = FakeProcess(10)
    closed = FakeProcess(20)
    closed.returncode = 0
    manager.children["agent-pet"] = closed
    monkeypatch.setattr("desktop_runtime.manager.terminate_owned_process_tree", lambda proc: None)
    monkeypatch.setattr(manager, "_environment", lambda: {})
    def spawn(role, argv, env):
        manager.children[role] = FakeProcess(30)
        return manager.children[role]
    monkeypatch.setattr(manager, "_spawn", spawn)
    result = manager.dispatch("pet_action", {"enabled": True, "confirmed": True})
    assert result["state"] == "running"
    assert result["pid"] == 30
