import asyncio
import json
from types import SimpleNamespace

import pytest

from capability_broker import CapabilityBroker
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
from external_observer_runtime import ExternalObserverRuntime, invoke_external_observer
from mcp_client_manager import MCPClientManager
from observer_hook_runtime import HookInvocationStore, ObserverHookRuntime
from observer_plugin_manifest import (
    load_external_observer_manifest,
)
from provider_runtime_capabilities import register_provider_runtime_capabilities


def _event(event_type="capability.succeeded", sequence=1):
    return {
        "sequence": sequence,
        "schema_version": 1,
        "event_id": f"event-{sequence}",
        "timestamp": "2026-09-13T00:00:00+00:00",
        "event_type": event_type,
        "source": "capability_broker",
        "subject": "fixture.read",
        "correlation_id": f"corr-{sequence}",
        "causation_id": None,
        "capability_id": "fixture.read",
        "provider_id": "fixture",
        "transaction_id": None,
        "task_id": None,
        "payload": {"result_sha256": "a" * 64},
        "payload_sha256": "b" * 64,
    }


def _prepare_runtime_files(project_root, observer_id="demo"):
    python_path = (
        project_root
        / ".observer_envs"
        / observer_id
        / "Scripts"
        / "python.exe"
    )
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fake-python")
    return python_path


def _write_manifest(
    project_root,
    *,
    observer_id="demo",
    autostart=True,
    event_types=None,
    timeout_seconds=2.0,
    module="demo_observer",
):
    manifest_dir = project_root / "observer_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / f"{observer_id}.json"
    payload = {
        "schema_version": 1,
        "id": observer_id,
        "autostart": autostart,
        "event_types": event_types or ["capability.succeeded"],
        "timeout_seconds": timeout_seconds,
        "runtime": {
            "kind": "isolated_python_module",
            "python": f".observer_envs/{observer_id}/Scripts/python.exe",
            "module": module,
            "cwd": ".",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_external_observer_manifest_accepts_isolated_python_module(tmp_path):
    _prepare_runtime_files(tmp_path)
    path = _write_manifest(tmp_path)

    manifest = load_external_observer_manifest(path, tmp_path)

    assert manifest.observer_id == "demo"
    assert manifest.hook_id == "external.demo"
    assert manifest.event_types == ("capability.succeeded",)
    assert manifest.timeout_seconds == 2.0
    assert manifest.python_path.name == "python.exe"
    assert manifest.module == "demo_observer"


def test_external_observer_manifest_rejects_python_outside_dedicated_env(tmp_path):
    outside = tmp_path / "python.exe"
    outside.write_bytes(b"x")
    path = _write_manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["runtime"]["python"] = "python.exe"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must be inside"):
        load_external_observer_manifest(path, tmp_path)


@pytest.mark.parametrize("module", ["-c", "bad/module", "x;evil"])
def test_external_observer_manifest_rejects_arbitrary_python_modes(tmp_path, module):
    _prepare_runtime_files(tmp_path)
    path = _write_manifest(tmp_path, module=module)

    with pytest.raises(ValueError, match="valid Python module"):
        load_external_observer_manifest(path, tmp_path)


def test_external_observer_manifest_rejects_bad_events_and_timeout(tmp_path):
    _prepare_runtime_files(tmp_path)
    path = _write_manifest(
        tmp_path,
        event_types=["capability.succeeded", "capability.succeeded"],
    )
    with pytest.raises(ValueError, match="duplicates"):
        load_external_observer_manifest(path, tmp_path)

    path = _write_manifest(tmp_path, timeout_seconds=5.1)
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_external_observer_manifest(path, tmp_path)


def test_selected_external_observer_dispatches_through_hook_store(tmp_path, monkeypatch):
    _prepare_runtime_files(tmp_path)
    _write_manifest(tmp_path, autostart=True)
    monkeypatch.setenv("PLA_EXTERNAL_OBSERVERS", "*")

    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)
    seen = []

    def runner(manifest, event):
        seen.append((manifest.observer_id, event["event_id"]))
        return {"observed": event["event_id"]}

    runtime = ExternalObserverRuntime(hooks, tmp_path, runner=runner)
    try:
        configured = runtime.configure_initial()
        assert list(configured) == ["demo"]
        status = runtime.status()
        assert status["active_observer_ids"] == ["demo"]
        assert status["hooks"][0]["hook_id"] == "external.demo"
        assert status["hooks"][0]["enabled"] is True

        result = hooks.dispatch(_event())
        assert result["completed_count"] == 1
        assert seen == [("demo", "event-1")]
        record = hook_store.query(hook_id="external.demo")["invocations"][0]
        assert record["status"] == "completed"
        assert record["event_id"] == "event-1"
    finally:
        hook_store.close()


def test_external_observer_failure_remains_fail_open(tmp_path, monkeypatch):
    _prepare_runtime_files(tmp_path)
    _write_manifest(tmp_path)
    monkeypatch.setenv("PLA_EXTERNAL_OBSERVERS", "*")

    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)

    def broken(_manifest, _event):
        raise RuntimeError("external observer failed")

    runtime = ExternalObserverRuntime(hooks, tmp_path, runner=broken)
    try:
        runtime.configure_initial()
        result = hooks.dispatch(_event())
        assert result["status"] == "partial"
        assert result["failed_count"] == 1
        record = hook_store.query(hook_id="external.demo")["invocations"][0]
        assert record["status"] == "failed"
        assert record["error_type"] == "RuntimeError"
        assert "external observer failed" not in str(record)
    finally:
        hook_store.close()


def test_external_observer_hotplug_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("PLA_EXTERNAL_OBSERVERS", "*")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)
    seen = []

    def runner(manifest, event):
        seen.append((manifest.observer_id, event["event_type"]))
        return {"ok": True}

    runtime = ExternalObserverRuntime(hooks, tmp_path, runner=runner)
    try:
        assert runtime.configure_initial() == {}

        _prepare_runtime_files(tmp_path)
        path = _write_manifest(tmp_path, event_types=["capability.succeeded"])
        added = runtime.rescan()
        assert added["added"] == ["demo"]

        hooks.dispatch(_event())
        assert seen == [("demo", "capability.succeeded")]

        runtime.disable("demo")
        hooks.dispatch(_event(sequence=2))
        assert seen == [("demo", "capability.succeeded")]
        assert runtime.status()["hooks"][0]["enabled"] is False

        runtime.enable("demo")
        hooks.dispatch(_event(sequence=3))
        assert len(seen) == 2
        assert runtime.status()["hooks"][0]["enabled"] is True

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["event_types"] = ["capability.failed"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        runtime.reload("demo")

        hooks.dispatch(_event("capability.succeeded", sequence=4))
        assert len(seen) == 2
        hooks.dispatch(_event("capability.failed", sequence=5))
        assert seen[-1] == ("demo", "capability.failed")

        path.unlink()
        removed = runtime.rescan()
        assert removed["removed"] == ["demo"]
        assert runtime.status()["active_observer_ids"] == []
        assert all(
            item["hook_id"] != "external.demo"
            for item in hooks.status()["hooks"]
        )
    finally:
        hook_store.close()


def test_external_observer_process_boundary_is_bounded(tmp_path, monkeypatch):
    _prepare_runtime_files(tmp_path)
    path = _write_manifest(tmp_path)
    manifest = load_external_observer_manifest(path, tmp_path)
    monkeypatch.setenv("PLA_TEST_SECRET", "must-not-leak")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"ok":true}',
            stderr="",
        )

    monkeypatch.setattr("external_observer_runtime.subprocess.run", fake_run)

    result = invoke_external_observer(manifest, _event())

    assert result == {"ok": True}
    assert captured["argv"] == [
        str(manifest.python_path),
        "-m",
        "demo_observer",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 2.0
    assert json.loads(captured["kwargs"]["input"])["event_id"] == "event-1"
    assert "PLA_TEST_SECRET" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["env"]["PYTHONUTF8"] == "1"


def test_runtime_observer_control_capabilities_do_not_self_observe(tmp_path, monkeypatch):
    monkeypatch.delenv("PLA_EXTERNAL_OBSERVERS", raising=False)
    event_store = EventStore(tmp_path / "events.sqlite3")
    hook_store = HookInvocationStore(tmp_path / "hooks.sqlite3")
    hooks = ObserverHookRuntime(hook_store)
    observer_runtime = ExternalObserverRuntime(hooks, tmp_path, runner=lambda *_: {})

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    broker = CapabilityBroker(registry, manager, event_store, hooks)

    provider_runtime = SimpleNamespace(
        status=lambda: {"status": "ready"},
        rescan=lambda: {"status": "completed"},
        reload=lambda _provider_id: {"status": "completed"},
        enable=lambda _provider_id: {"status": "completed"},
        disable=lambda _provider_id: {"status": "completed"},
    )
    register_provider_runtime_capabilities(
        registry,
        broker,
        provider_runtime,
        observer_runtime,
    )

    try:
        status = asyncio.run(
            broker.invoke("runtime.observer_status", {})
        )
        assert status["data"]["active_observer_ids"] == []
        assert event_store.query()["returned_count"] == 0
        assert hook_store.query()["returned_count"] == 0

        with pytest.raises(PermissionError, match="confirmation='INVOKE'"):
            asyncio.run(
                broker.invoke("runtime.observer_rescan", {})
            )
        assert event_store.query()["returned_count"] == 0
        assert hook_store.query()["returned_count"] == 0
    finally:
        hook_store.close()
        event_store.close()
