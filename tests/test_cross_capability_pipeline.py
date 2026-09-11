import asyncio
from pathlib import Path

import pytest
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.exceptions import ToolError

import artifact_bridge
import local_tools
from capability_broker import CapabilityBroker
from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_A = PROJECT_ROOT / "tests" / "fixtures" / "external_mcp_server.py"
PROVIDER_B = PROJECT_ROOT / "tests" / "fixtures" / "external_mcp_server_b.py"


async def build_two_provider_runtime():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
    manager.add_provider(
        "provider_a",
        PythonStdioTransport(PROVIDER_A, cwd=str(PROJECT_ROOT)),
    )
    manager.add_provider(
        "provider_b",
        PythonStdioTransport(PROVIDER_B, cwd=str(PROJECT_ROOT)),
    )
    discovered = await manager.discover_all()
    assert discovered["provider_a"]["state"] == "ready"
    assert discovered["provider_b"]["state"] == "ready"
    return registry, manager, CapabilityBroker(registry, manager)


def test_cross_provider_artifact_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("hello pipeline\n", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")
    source_id = source["artifact_id"]

    async def scenario():
        registry, _manager, broker = await build_two_provider_runtime()

        first_search = registry.search("uppercase file", provider_id="provider_a")
        assert first_search["match_count"] == 1
        second_search = registry.search("append marker", provider_id="provider_b")
        assert second_search["match_count"] == 1

        first = await broker.invoke(
            "provider_a.uppercase_file",
            {"input_artifact": source_id},
            confirmation="INVOKE",
        )
        intermediate_id = first["artifacts"][0]["artifact_id"]

        second = await broker.invoke(
            "provider_b.append_marker",
            {"input_artifact": intermediate_id, "marker": " :: SECOND"},
            confirmation="INVOKE",
        )
        final_id = second["artifacts"][0]["artifact_id"]
        return first, second, intermediate_id, final_id

    first, second, intermediate_id, final_id = asyncio.run(scenario())

    assert first["provider_id"] == "provider_a"
    assert second["provider_id"] == "provider_b"
    assert first["data"]["input_artifact"] == source_id
    assert second["data"]["input_artifact"] == intermediate_id
    assert second["data"]["output_path"] == final_id

    assert artifact_bridge.read_artifact_bytes(intermediate_id).decode("utf-8").splitlines() == [
        "HELLO PIPELINE"
    ]
    assert artifact_bridge.read_artifact_bytes(final_id).decode("utf-8").splitlines() == [
        "HELLO PIPELINE :: SECOND"
    ]

    intermediate_meta = artifact_bridge.artifact_metadata(intermediate_id)
    final_meta = artifact_bridge.artifact_metadata(final_id)
    assert intermediate_meta["source_provider"] == "provider_a"
    assert intermediate_meta["source_capability"] == "provider_a.uppercase_file"
    assert intermediate_meta["parent_artifacts"] == [source_id]
    assert final_meta["source_provider"] == "provider_b"
    assert final_meta["source_capability"] == "provider_b.append_marker"
    assert final_meta["parent_artifacts"] == [intermediate_id]

    # Direct-parent provenance must form a chain rather than flattening ancestry.
    assert source_id not in final_meta["parent_artifacts"]

    io_root = tmp_path / ".capability_io"
    assert not any(io_root.iterdir()) if io_root.exists() else True


def test_second_provider_failure_preserves_existing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("keep me\n", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")
    source_id = source["artifact_id"]

    async def scenario():
        _registry, _manager, broker = await build_two_provider_runtime()
        first = await broker.invoke(
            "provider_a.uppercase_file",
            {"input_artifact": source_id},
            confirmation="INVOKE",
        )
        intermediate_id = first["artifacts"][0]["artifact_id"]
        try:
            await broker.invoke(
                "provider_b.fail_after_read",
                {"input_artifact": intermediate_id},
                confirmation="INVOKE",
            )
        except ToolError as exc:
            return intermediate_id, str(exc)
        raise AssertionError("provider_b.fail_after_read should fail")

    intermediate_id, message = asyncio.run(scenario())

    assert "provider-b deliberate failure" in message
    assert artifact_bridge.read_artifact_bytes(source_id).decode("utf-8").splitlines() == ["keep me"]
    assert artifact_bridge.read_artifact_bytes(intermediate_id).decode("utf-8").splitlines() == ["KEEP ME"]

    intermediate_meta = artifact_bridge.artifact_metadata(intermediate_id)
    assert intermediate_meta["parent_artifacts"] == [source_id]
    assert intermediate_meta["source_provider"] == "provider_a"

    io_root = tmp_path / ".capability_io"
    assert not any(io_root.iterdir()) if io_root.exists() else True


def test_provider_b_can_be_disabled_without_affecting_provider_a(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("isolation\n", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")

    async def scenario():
        registry, manager, broker = await build_two_provider_runtime()
        manager.set_provider_enabled("provider_b", False)

        assert registry.search("", provider_id="provider_b")["match_count"] == 0
        assert registry.search("", provider_id="provider_a")["match_count"] > 0

        first = await broker.invoke(
            "provider_a.uppercase_file",
            {"input_artifact": source["artifact_id"]},
            confirmation="INVOKE",
        )
        intermediate_id = first["artifacts"][0]["artifact_id"]

        with pytest.raises(ValueError, match="unavailable"):
            await broker.invoke(
                "provider_b.append_marker",
                {"input_artifact": intermediate_id},
                confirmation="INVOKE",
            )
        return intermediate_id

    intermediate_id = asyncio.run(scenario())
    assert artifact_bridge.read_artifact_bytes(intermediate_id).decode("utf-8").splitlines() == [
        "ISOLATION"
    ]
