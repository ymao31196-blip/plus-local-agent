"""Desktop-managed Q-style Agent Pet lifecycle and opt-in tests."""
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import time

import pytest

from desktop_runtime import pet_viewer
from runtime.event_runtime import EventStore

from desktop_runtime.manager import Manager
from desktop_runtime.pet_companion import PetCompanion, PET_REQUIRED_ANIMATIONS


def test_pet_footer_time_transitions_to_stable_clock():
    local = datetime(2026, 10, 25, 20, 0)
    now = local.timestamp()
    label = pet_viewer.last_call_label
    assert label(None, now) == "尚无调用记录"
    assert label(now - 10, now) == "刚刚调用"
    assert label(now - 45, now) == "上次调用 · 不到1分钟前"
    assert label(now - 8 * 60, now) == "上次调用 · 8分钟前"
    assert label(now - 59 * 60, now) == "上次调用 · 今天 19:01"
    assert label((local - timedelta(days=1)).timestamp(), now) == "上次调用 · 昨天 20:00"
    assert label((local - timedelta(days=3)).timestamp(), now) == "上次调用 · 10-22 20:00"


def test_pet_registered_dialogue_count_uses_runtime_boundary(tmp_path):
    db = tmp_path / "events.sqlite3"
    store = EventStore(db)
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    try:
        store.emit("conversation.tool_started", source="conversation_transport",
                   subject="read_text", payload={"fingerprint": "a" * 64})
        store.emit("conversation.tool_finished", source="conversation_transport",
                   subject="read_text", payload={"fingerprint": "a" * 64})
        store.emit("conversation.tool_started", source="conversation_transport",
                   subject="run_process", payload={"fingerprint": "b" * 64})
        assert pet_viewer.registered_dialogues_label(db, started_at) == "活跃 2"
        store.emit("conversation.tool_started", source="conversation_transport",
                   subject="run_process", payload={"identity": "missing"})
        assert pet_viewer.registered_dialogues_label(db, started_at) == "活跃 2 · 未识别1"
        assert pet_viewer.registered_dialogues_label(
            db, (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        ) == "活跃 0"
        assert pet_viewer.registered_dialogues_label(db, None) == "会话未知"
    finally:
        store.close()


def test_pet_registered_dialogue_unavailable_is_explicit(tmp_path):
    db = tmp_path / "events.sqlite3"
    db.write_bytes(b"corrupt-sqlite")
    assert pet_viewer.registered_dialogues_label(
        db, datetime.now(timezone.utc).isoformat()
    ) == "会话不可用"


def test_pet_one_line_label_uses_rendered_width():
    width = lambda s: sum(2 if ord(c) > 127 else 1 for c in s)
    assert pet_viewer.fit_text_width("Skill Library", width, 20) == "Skill Library"
    assert pet_viewer.fit_text_width("特别长的中文组件名称", width, 9) == "特别长…"
    assert pet_viewer.fit_text_width("abcdef", len, 4) == "abc…"


def test_pet_activity_labels_match_desktop_event_types(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        def emit(kind, source, name, call_id, **kwargs):
            return store.emit(kind, source=source, subject=name,
                              correlation_id=call_id, **kwargs)

        emit("native_tool.started", "native_mcp", "run_process", "python-1",
             payload={"program": "python.exe"})
        emit("native_tool.succeeded", "native_mcp", "run_process", "python-1")
        emit("local_action.started", "local_action", "run_process", "git-2",
             payload={"program": "git"})
        emit("local_action.succeeded", "local_action", "run_process", "git-2")
        emit("capability.before_invoke", "capability_broker", "drawio.render", "draw-3",
             capability_id="drawio.render", provider_id="drawio")
        emit("capability.succeeded", "capability_broker", "drawio.render", "draw-3",
             capability_id="drawio.render", provider_id="drawio")
        snapshot = pet_viewer.build_snapshot(pet_viewer.fetch_rows(tmp_path / "events.sqlite3"),
                                             time.time())
        assert len(snapshot.calls) == 3
        assert pet_viewer.describe_call(snapshot.calls[0]) == ("draw.io", "render · 组件调用")
        assert pet_viewer.describe_call(snapshot.calls[1]) == ("程序 · git", "run_process · 本地执行")
        assert pet_viewer.describe_call(snapshot.calls[2]) == ("程序 · python.exe", "run_process · 本地执行")
        assert all(item.status == "succeeded" for item in snapshot.calls)
        assert snapshot.mode == "active"
    finally:
        store.close()


def test_pet_denied_capability_and_untrusted_payload_are_safe(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        store.emit("capability.gate_denied", source="capability_broker",
                   subject="browser.navigate", capability_id="browser.navigate",
                   provider_id="browser", correlation_id="denied")
        store.emit("native_tool.started", source="native_mcp",
                   subject="run_process", correlation_id="malicious",
                   payload={"program": "C:/secret/tokens/python.exe",
                            "args": ["SECRET_ARGUMENT"]})
        snapshot = pet_viewer.read_snapshot(tmp_path / "events.sqlite3")
        assert len(snapshot.calls) == 2
        assert snapshot.calls[0].program is None
        assert pet_viewer.describe_call(snapshot.calls[0]) == (
            "程序 · 未知程序", "run_process · 本地执行"
        )
        assert snapshot.calls[1].status == "failed"
        assert pet_viewer.describe_call(snapshot.calls[1])[0] == "Browser"
        assert "SECRET_ARGUMENT" not in str(snapshot)
    finally:
        store.close()


def test_pet_recent_success_does_not_wait_on_old_unfinished_call(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    try:
        store.emit("native_tool.started", source="native_mcp",
                   subject="read_text", correlation_id="old")
        store.emit("native_tool.started", source="native_mcp",
                   subject="write_text", correlation_id="new")
        store.emit("native_tool.succeeded", source="native_mcp",
                   subject="write_text", correlation_id="new")
        snapshot = pet_viewer.read_snapshot(tmp_path / "events.sqlite3")
        assert snapshot.mode == "active"
        assert snapshot.calls[0].name == "write_text"
        assert snapshot.calls[0].status == "succeeded"
    finally:
        store.close()


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
        assert env["PLA_PET_RUNTIME_STARTED_AT"] == (manager.runtime_started_at or "")
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
        if role == "agent-pet":
            assert env["PLA_PET_RUNTIME_STARTED_AT"] == manager.runtime_started_at
            assert manager.runtime_started_at is not None
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
