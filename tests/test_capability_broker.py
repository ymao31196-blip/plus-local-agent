import asyncio
from pathlib import Path

import pytest
from fastmcp.client.transports import PythonStdioTransport

import artifact_bridge
import local_tools
from capability_broker import CapabilityBroker
from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "external_mcp_server.py"


def build_runtime():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
    transport = PythonStdioTransport(
        FIXTURE,
        cwd=str(PROJECT_ROOT),
    )
    manager.add_provider("fixture", transport)
    asyncio.run(manager.discover_provider("fixture"))
    return registry, manager, CapabilityBroker(registry, manager)


def test_stdio_provider_discovery_and_invoke_round_trip():
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)

        search = registry.search("echo text")
        assert search["match_count"] == 1
        assert search["capabilities"][0]["id"] == "fixture.echo_text"

        return await broker.invoke(
            "fixture.echo_text",
            {"text": "hi", "repeat": 2},
            confirmation="INVOKE",
        )

    result = asyncio.run(scenario())

    assert result["status"] == "completed"
    assert result["provider_id"] == "fixture"
    assert result["capability_id"] == "fixture.echo_text"
    assert result["remote_name"] == "echo_text"
    assert result["data"] == {"echo": "hihi", "repeat": 2}
    assert result["is_error"] is False


def test_confirmation_gate_blocks_call_before_provider(monkeypatch):
    registry, manager, broker = build_runtime()
    called = {"value": False}

    async def forbidden(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("provider should not be called")

    monkeypatch.setattr(manager, "call_tool", forbidden)

    with pytest.raises(PermissionError, match="confirmation='INVOKE'"):
        asyncio.run(broker.invoke(
            "fixture.echo_text",
            {"text": "blocked"},
        ))
    assert called["value"] is False


def test_jsonschema_validation_blocks_bad_arguments_before_provider(monkeypatch):
    registry, manager, broker = build_runtime()
    called = {"value": False}

    async def forbidden(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("provider should not be called")

    monkeypatch.setattr(manager, "call_tool", forbidden)

    with pytest.raises(ValueError, match="Invalid arguments"):
        asyncio.run(broker.invoke(
            "fixture.add_numbers",
            {"a": 1},
            confirmation="INVOKE",
        ))
    assert called["value"] is False


def test_disabled_provider_cannot_be_invoked():
    registry, manager, broker = build_runtime()
    manager.set_provider_enabled("fixture", False)

    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(broker.invoke(
            "fixture.echo_text",
            {"text": "x"},
            confirmation="INVOKE",
        ))


def test_unknown_capability_is_rejected():
    registry, manager, broker = build_runtime()
    with pytest.raises(ValueError, match="Unknown capability"):
        asyncio.run(broker.invoke(
            "fixture.missing",
            {},
            confirmation="INVOKE",
        ))


def test_manager_rejects_call_before_successful_discovery():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
    manager.add_provider("fixture", transport)

    with pytest.raises(ValueError, match="not ready"):
        asyncio.run(manager.call_tool("fixture", "echo_text", {"text": "x"}))


def test_manager_rejects_disabled_provider_call():
    registry, manager, _broker = build_runtime()
    manager.set_provider_enabled("fixture", False)

    with pytest.raises(ValueError, match="disabled"):
        asyncio.run(manager.call_tool("fixture", "echo_text", {"text": "x"}))


def test_artifact_contract_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("hello artifact\n", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")
    source_id = source["artifact_id"]

    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        detail = registry.describe("fixture.uppercase_file")
        assert detail["artifact_inputs"] == ["input_artifact"]
        assert detail["artifact_outputs"] is True
        assert detail["artifact_contract"] == {
            "transport": "local_path",
            "inputs": ["input_artifact"],
            "outputs": ["output_path"],
        }
        broker = CapabilityBroker(registry, manager)
        return await broker.invoke(
            "fixture.uppercase_file",
            {"input_artifact": source_id},
            confirmation="INVOKE",
        )

    result = asyncio.run(scenario())

    assert result["status"] == "completed"
    assert result["content"] == []
    assert result["content_omitted_for_artifact_contract"] is True
    assert len(result["artifacts"]) == 1
    output = result["artifacts"][0]
    output_id = output["artifact_id"]
    assert result["data"]["input_artifact"] == source_id
    assert result["data"]["output_path"] == output_id
    assert artifact_bridge.read_artifact_bytes(output_id).decode("utf-8").splitlines() == ["HELLO ARTIFACT"]

    metadata = artifact_bridge.artifact_metadata(output_id)
    assert metadata["source_provider"] == "fixture"
    assert metadata["source_capability"] == "fixture.uppercase_file"
    assert metadata["parent_artifacts"] == [source_id]
    io_root = tmp_path / ".capability_io"
    assert not any(io_root.iterdir()) if io_root.exists() else True


def test_artifact_output_must_stay_inside_invocation_workspace():
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)
        return await broker.invoke(
            "fixture.escape_output",
            {},
            confirmation="INVOKE",
        )

    with pytest.raises(ValueError, match="escapes invocation workspace"):
        asyncio.run(scenario())


def test_managed_output_path_is_injected_and_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("managed source", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")
    source_id = source["artifact_id"]

    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider(
            "fixture",
            transport,
            tool_allowlist=["managed_copy"],
            tool_overrides={
                "managed_copy": {
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "input_artifact": {"type": "string"},
                        },
                        "required": ["input_artifact"],
                        "additionalProperties": False,
                    },
                    "artifact_contract": {
                        "transport": "local_path",
                        "inputs": ["input_artifact"],
                        "outputs": [],
                        "output_paths": {
                            "output_path": {
                                "filename": "managed.txt",
                                "mime_type": "text/plain",
                            }
                        },
                    },
                    "risk_level": "write_local",
                    "requires_confirmation": False,
                }
            },
        )
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)
        return await broker.invoke(
            "fixture.managed_copy",
            {"input_artifact": source_id},
        )

    result = asyncio.run(scenario())

    assert result["status"] == "completed"
    assert result["content"] == []
    assert result["content_omitted_for_artifact_contract"] is True
    assert len(result["artifacts"]) == 1
    output_id = result["artifact_outputs"]["output_path"]
    assert output_id == result["artifacts"][0]["artifact_id"]
    assert ".capability_io" not in str(result["data"])
    assert output_id in result["data"]["result"]
    assert artifact_bridge.read_artifact_bytes(output_id).decode("utf-8").splitlines() == [
        "managed source",
        "managed",
    ]
    metadata = artifact_bridge.artifact_metadata(output_id)
    assert metadata["parent_artifacts"] == [source_id]
    assert metadata["source_provider"] == "fixture"
    assert metadata["source_capability"] == "fixture.managed_copy"


def test_managed_output_argument_cannot_be_supplied_by_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    source = artifact_bridge.export_artifact("source.txt")

    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider(
            "fixture",
            transport,
            tool_allowlist=["managed_copy"],
            tool_overrides={
                "managed_copy": {
                    "artifact_contract": {
                        "transport": "local_path",
                        "inputs": ["input_artifact"],
                        "outputs": [],
                        "output_paths": {
                            "output_path": {"filename": "managed.txt"}
                        },
                    },
                }
            },
        )
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)
        return await broker.invoke(
            "fixture.managed_copy",
            {
                "input_artifact": source["artifact_id"],
                "output_path": "C:/escape.txt",
            },
            confirmation="INVOKE",
        )

    with pytest.raises(ValueError, match="managed by PLA"):
        asyncio.run(scenario())


def test_transaction_required_capability_blocks_direct_broker_call():
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider(
            "fixture",
            transport,
            tool_allowlist=["echo_text"],
            tool_overrides={
                "echo_text": {
                    "requires_confirmation": True,
                    "requires_transaction": True,
                }
            },
        )
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)

        with pytest.raises(PermissionError, match="requires a transaction context"):
            await broker.invoke(
                "fixture.echo_text",
                {"text": "blocked"},
                confirmation="INVOKE",
            )

        return await broker.invoke(
            "fixture.echo_text",
            {"text": "allowed"},
            confirmation="INVOKE",
            transaction_context=True,
        )

    result = asyncio.run(scenario())

    assert result["status"] == "completed"
    assert result["data"]["echo"] == "allowed"
