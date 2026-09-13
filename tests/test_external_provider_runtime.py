import json
from pathlib import Path

import pytest

from capability_registry import CapabilityRegistry
from external_provider_runtime import configure_external_providers
from mcp_client_manager import MCPClientManager


def write_manifest(
    root: Path,
    provider_id: str,
    *,
    autostart: bool = True,
    tool_name: str = "convert",
    runtime: dict | None = None,
):
    manifest_dir = root / "provider_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": provider_id,
        "autostart": autostart,
        "mode": "legacy",
        "runtime": runtime or {
            "kind": "isolated_python_stdio",
            "python": f".provider_envs/{provider_id}/Scripts/python.exe",
            "args": ["-m", f"{provider_id}_server"],
            "cwd": ".",
        },
        "tool_allowlist": [tool_name],
        "tool_overrides": {
            tool_name: {
                "risk_level": "read",
                "requires_confirmation": False,
                "artifact_contract": {
                    "transport": "file_uri",
                    "inputs": ["uri"],
                    "outputs": [],
                },
            }
        },
    }
    path = manifest_dir / f"{provider_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_external_providers_are_opt_in(tmp_path, monkeypatch):
    write_manifest(tmp_path, "demo")
    monkeypatch.delenv("PLA_EXTERNAL_PROVIDERS", raising=False)
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)

    configured = configure_external_providers(manager, tmp_path)

    assert configured == {}
    assert manager.provider_status() == {}


def test_manifest_defaults_select_only_autostart_providers(tmp_path, monkeypatch):
    write_manifest(tmp_path, "alpha", autostart=True)
    write_manifest(tmp_path, "beta", autostart=False)
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "*")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)

    configured = configure_external_providers(manager, tmp_path)

    assert set(configured) == {"alpha"}
    assert manager.provider_status("alpha")["state"] == "configured"
    assert manager._modes["alpha"] == "legacy"
    assert manager._tool_allowlists["alpha"] == {"convert"}


def test_explicit_selection_can_enable_non_autostart_provider(tmp_path, monkeypatch):
    write_manifest(tmp_path, "alpha", autostart=True)
    write_manifest(tmp_path, "beta", autostart=False)
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "beta")
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)

    configured = configure_external_providers(manager, tmp_path)

    assert set(configured) == {"beta"}
    assert manager.provider_status("beta")["state"] == "configured"


def test_unknown_selected_provider_is_rejected(tmp_path, monkeypatch):
    write_manifest(tmp_path, "alpha")
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "missing")
    manager = MCPClientManager(CapabilityRegistry())

    with pytest.raises(ValueError, match="Unknown external provider ids"):
        configure_external_providers(manager, tmp_path)


def test_manifest_tool_policy_is_forwarded_to_manager(tmp_path, monkeypatch):
    write_manifest(tmp_path, "alpha", tool_name="convert")
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "alpha")
    manager = MCPClientManager(CapabilityRegistry())

    configured = configure_external_providers(manager, tmp_path)

    assert configured["alpha"]["provider_id"] == "alpha"
    assert configured["alpha"]["mode"] == "legacy"
    assert configured["alpha"]["tool_allowlist"] == ["convert"]
    assert manager._tool_overrides["alpha"]["convert"]["artifact_contract"] == {
        "transport": "file_uri",
        "inputs": ["uri"],
        "outputs": [],
    }

def test_executable_stdio_provider_is_registered_without_python_env(tmp_path, monkeypatch):
    executable = tmp_path / "bin" / "system-mcp.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake-executable")
    write_manifest(
        tmp_path,
        "system",
        runtime={
            "kind": "executable_stdio",
            "command": "bin/system-mcp.exe",
            "args": ["--stdio"],
            "cwd": ".",
        },
    )
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "system")
    manager = MCPClientManager(CapabilityRegistry())

    configured = configure_external_providers(manager, tmp_path)

    assert configured["system"]["runtime_kind"] == "executable_stdio"
    assert configured["system"]["command"] == str(executable.resolve())
    assert configured["system"]["command_exists"] is True
    assert "python" not in configured["system"]


def test_loopback_streamable_http_provider_uses_http_transport(tmp_path, monkeypatch):
    write_manifest(
        tmp_path,
        "browser",
        runtime={
            "kind": "streamable_http",
            "url": "http://127.0.0.1:8931/mcp",
        },
    )
    monkeypatch.setenv("PLA_EXTERNAL_PROVIDERS", "browser")
    manager = MCPClientManager(CapabilityRegistry())

    configured = configure_external_providers(manager, tmp_path)

    assert configured["browser"]["runtime_kind"] == "streamable_http"
    assert configured["browser"]["url"] == "http://127.0.0.1:8931/mcp"
    assert type(manager._sources["browser"]).__name__ == "StreamableHttpTransport"
