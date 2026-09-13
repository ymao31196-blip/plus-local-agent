import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from capability_broker import CapabilityBroker
from capability_registry import CapabilityRegistry
from external_provider_runtime import ExternalProviderRuntime
from mcp_client_manager import MCPClientManager
from provider_runtime_capabilities import register_provider_runtime_capabilities


def _tool(name: str):
    return SimpleNamespace(
        name=name,
        title=name,
        description=f"Tool {name}",
        meta={},
        input_schema={"type": "object", "properties": {}},
        output_schema={},
    )


def _write_manifest(root: Path, provider_id: str, tool_name: str) -> Path:
    manifest_dir = root / "provider_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": provider_id,
        "autostart": True,
        "mode": "auto",
        "runtime": {
            "kind": "executable_stdio",
            "command": sys.executable,
            "args": [],
            "cwd": ".",
        },
        "tool_allowlist": [tool_name],
        "tool_overrides": {
            tool_name: {
                "risk_level": "read",
                "requires_confirmation": False,
                "tags": ["hotplug-test"],
            }
        },
    }
    path = manifest_dir / f"{provider_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_hotplug_rescan_add_change_disable_enable_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "*")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    runtime = ExternalProviderRuntime(manager, tmp_path)

    async def fake_list_tools(_source, _mode):
        return [_tool("one"), _tool("two")]

    monkeypatch.setattr(manager, "_list_tools", fake_list_tools)

    assert runtime.configure_initial() == {}

    manifest = _write_manifest(tmp_path, "alpha", "one")
    added = asyncio.run(runtime.rescan())
    assert added["added"] == ["alpha"]
    assert registry.describe("alpha.one")["available"] is True

    _write_manifest(tmp_path, "alpha", "two")
    changed = asyncio.run(runtime.rescan())
    assert changed["changed"] == ["alpha"]
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("alpha.one")
    assert registry.describe("alpha.two")["available"] is True

    disabled = asyncio.run(runtime.disable("alpha"))
    assert disabled["provider"]["enabled"] is False
    assert registry.describe("alpha.two")["available"] is False

    enabled = asyncio.run(runtime.enable("alpha"))
    assert enabled["provider"]["enabled"] is True
    assert registry.describe("alpha.two")["available"] is True

    manifest.unlink()
    removed = asyncio.run(runtime.rescan())
    assert removed["removed"] == ["alpha"]
    assert manager.has_provider("alpha") is False
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("alpha.two")


def test_hotplug_invalid_manifest_does_not_mutate_active_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "*")
    _write_manifest(tmp_path, "alpha", "one")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    runtime = ExternalProviderRuntime(manager, tmp_path)

    async def fake_list_tools(_source, _mode):
        return [_tool("one")]

    monkeypatch.setattr(manager, "_list_tools", fake_list_tools)
    runtime.configure_initial()
    asyncio.run(manager.discover_all())
    assert registry.describe("alpha.one")["available"] is True

    bad = tmp_path / "provider_manifests" / "broken.json"
    bad.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid provider manifest JSON"):
        asyncio.run(runtime.rescan())

    assert manager.has_provider("alpha") is True
    assert registry.describe("alpha.one")["available"] is True


def test_runtime_hotplug_capabilities_use_confirmation_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "*")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    runtime = ExternalProviderRuntime(manager, tmp_path)
    broker = CapabilityBroker(registry, manager)
    register_provider_runtime_capabilities(registry, broker, runtime)

    assert registry.describe("runtime.provider_status")["requires_confirmation"] is False
    for capability_id in (
        "runtime.provider_setup",
        "runtime.provider_rescan",
        "runtime.provider_reload",
        "runtime.provider_enable",
        "runtime.provider_disable",
    ):
        assert registry.describe(capability_id)["requires_confirmation"] is True

    with pytest.raises(PermissionError, match="requires confirmation"):
        asyncio.run(
            broker.invoke(
                "runtime.provider_rescan",
                {},
            )
        )

    result = asyncio.run(
        broker.invoke(
            "runtime.provider_rescan",
            {},
            confirmation="INVOKE",
        )
    )
    assert result["status"] == "completed"
    assert result["data"]["status"] == "completed"
