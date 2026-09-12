import asyncio
import json
from pathlib import Path

from fastmcp import FastMCP

import provider_doctor as doctor_module
from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager
from provider_doctor import provider_doctor


def _write_provider_layout(root: Path, *, version: str = "1.2.3") -> None:
    manifest_dir = root / "provider_manifests"
    spec_dir = root / "provider_specs"
    python_path = root / ".provider_envs" / "demo" / "Scripts" / "python.exe"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fake-python")

    manifest = {
        "schema_version": 1,
        "id": "demo",
        "autostart": True,
        "mode": "legacy",
        "runtime": {
            "kind": "isolated_python_stdio",
            "python": ".provider_envs/demo/Scripts/python.exe",
            "args": ["-m", "demo_server"],
            "cwd": ".",
        },
        "tool_allowlist": ["health_check"],
        "tool_overrides": {},
    }
    (manifest_dir / "demo.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (spec_dir / "demo.txt").write_text(
        f"demo-package=={version}\n",
        encoding="utf-8",
    )


def _build_demo_mcp():
    mcp = FastMCP("doctor-demo")

    @mcp.tool
    def health_check() -> str:
        return "ok"

    return mcp


def test_provider_doctor_reports_healthy_static_and_lifecycle(tmp_path, monkeypatch):
    _write_provider_layout(tmp_path)
    monkeypatch.setattr(
        doctor_module,
        "_installed_versions",
        lambda python_path, package_names: ({"demo-package": "1.2.3"}, None),
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "demo",
        _build_demo_mcp(),
        mode="legacy",
        tool_allowlist=["health_check"],
    )
    asyncio.run(manager.discover_provider("demo"))

    result = asyncio.run(provider_doctor(manager, tmp_path))

    assert result["status"] == "healthy"
    assert result["provider_count"] == 1
    item = result["providers"][0]
    assert item["status"] == "healthy"
    assert item["lifecycle"]["state"] == "ready"
    assert item["static"]["version_drift_detected"] is False
    assert item["static"]["python_exists"] is True
    assert item["live_probe"]["attempted"] is False


def test_provider_doctor_detects_version_drift(tmp_path, monkeypatch):
    _write_provider_layout(tmp_path, version="1.2.3")
    monkeypatch.setattr(
        doctor_module,
        "_installed_versions",
        lambda python_path, package_names: ({"demo-package": "9.9.9"}, None),
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "demo",
        _build_demo_mcp(),
        mode="legacy",
        tool_allowlist=["health_check"],
    )
    asyncio.run(manager.discover_provider("demo"))

    result = asyncio.run(provider_doctor(manager, tmp_path, provider_id="demo"))

    item = result["providers"][0]
    assert result["status"] == "degraded"
    assert item["static"]["version_drift_detected"] is True
    assert item["static"]["version_drift"]["demo-package"] == {
        "expected": "1.2.3",
        "installed": "9.9.9",
        "match": False,
    }


def test_provider_doctor_live_probe_recovers_error_state(tmp_path, monkeypatch):
    _write_provider_layout(tmp_path)
    monkeypatch.setattr(
        doctor_module,
        "_installed_versions",
        lambda python_path, package_names: ({"demo-package": "1.2.3"}, None),
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(
        registry,
        retry_base_seconds=60,
        retry_max_seconds=60,
    )
    manager.add_provider(
        "demo",
        _build_demo_mcp(),
        mode="legacy",
        tool_allowlist=["health_check"],
    )

    original = manager._list_tools
    calls = {"count": 0}

    async def flaky(source, mode):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("offline")
        return await original(source, mode)

    monkeypatch.setattr(manager, "_list_tools", flaky)

    try:
        asyncio.run(manager.discover_provider("demo"))
    except ConnectionError:
        pass
    assert manager.provider_status("demo")["state"] == "error"

    blocked = asyncio.run(
        provider_doctor(
            manager,
            tmp_path,
            provider_id="demo",
            live_probe=True,
            force=False,
        )
    )
    assert blocked["providers"][0]["live_probe"]["ok"] is False
    assert "retry backoff" in blocked["providers"][0]["live_probe"]["error_message"]

    recovered = asyncio.run(
        provider_doctor(
            manager,
            tmp_path,
            provider_id="demo",
            live_probe=True,
            force=True,
        )
    )
    item = recovered["providers"][0]
    assert item["live_probe"]["ok"] is True
    assert item["lifecycle"]["state"] == "ready"
    assert item["lifecycle"]["consecutive_failures"] == 0
    assert recovered["status"] == "healthy"


def test_provider_doctor_distinguishes_disabled_provider(tmp_path, monkeypatch):
    _write_provider_layout(tmp_path)
    monkeypatch.setattr(
        doctor_module,
        "_installed_versions",
        lambda python_path, package_names: ({"demo-package": "1.2.3"}, None),
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "demo",
        _build_demo_mcp(),
        mode="legacy",
        tool_allowlist=["health_check"],
    )
    asyncio.run(manager.discover_provider("demo"))
    manager.set_provider_enabled("demo", False)

    report = asyncio.run(provider_doctor(manager, tmp_path))

    assert report["status"] == "healthy"
    assert report["healthy_count"] == 0
    assert report["disabled_count"] == 1
    assert report["degraded_count"] == 0
    assert report["providers"][0]["status"] == "disabled"

def test_provider_doctor_accepts_executable_stdio_without_python_spec(tmp_path):
    manifest_dir = tmp_path / "provider_manifests"
    executable = tmp_path / "bin" / "system-mcp.exe"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fake-executable")
    manifest = {
        "schema_version": 1,
        "id": "system",
        "autostart": True,
        "mode": "legacy",
        "runtime": {
            "kind": "executable_stdio",
            "command": "bin/system-mcp.exe",
            "args": [],
            "cwd": ".",
        },
        "tool_allowlist": ["health_check"],
        "tool_overrides": {},
    }
    (manifest_dir / "system.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "system",
        _build_demo_mcp(),
        mode="legacy",
        tool_allowlist=["health_check"],
    )
    asyncio.run(manager.discover_provider("system"))

    report = asyncio.run(provider_doctor(manager, tmp_path))

    item = report["providers"][0]
    assert report["status"] == "healthy"
    assert item["status"] == "healthy"
    assert item["static"]["runtime_kind"] == "executable_stdio"
    assert item["static"]["runtime_ready"] is True
    assert item["static"]["command_exists"] is True
    assert item["static"]["spec"] is None
    assert item["static"]["python"] is None
