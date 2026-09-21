import asyncio
from pathlib import Path

import pytest
from fastmcp.client.transports import PythonStdioTransport

import artifact_bridge
import capability_broker as broker_module
import local_tools
from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
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


def test_capability_broker_records_success_events_without_raw_arguments(tmp_path):
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        store = EventStore(tmp_path / "events.sqlite3")
        broker = CapabilityBroker(registry, manager, store)
        result = await broker.invoke(
            "fixture.echo_text",
            {"text": "super-secret-value", "repeat": 2},
            confirmation="INVOKE",
        )
        events = store.query(capability_id="fixture.echo_text")["events"]
        store.close()
        return result, events

    result, events = asyncio.run(scenario())

    assert result["data"]["echo"] == "super-secret-valuesuper-secret-value"
    assert [event["event_type"] for event in events] == [
        "capability.before_invoke",
        "capability.succeeded",
    ]
    before, succeeded = events
    assert before["correlation_id"] == succeeded["correlation_id"]
    assert succeeded["causation_id"] == before["event_id"]
    assert before["payload"]["argument_keys"] == ["repeat", "text"]
    assert len(before["payload"]["arguments_sha256"]) == 64
    assert len(succeeded["payload"]["result_sha256"]) == 64
    assert "super-secret-value" not in str(events)


def test_capability_broker_records_transaction_id_on_transaction_invocation(tmp_path):
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        store = EventStore(tmp_path / "events.sqlite3")
        broker = CapabilityBroker(registry, manager, store)
        result = await broker.invoke(
            "fixture.echo_text",
            {"text": "transactional"},
            confirmation="INVOKE",
            transaction_context=True,
            transaction_id="tx-123",
        )
        events = store.query(
            capability_id="fixture.echo_text",
            transaction_id="tx-123",
        )["events"]
        store.close()
        return result, events

    result, events = asyncio.run(scenario())

    assert result["status"] == "completed"
    assert [event["event_type"] for event in events] == [
        "capability.before_invoke",
        "capability.succeeded",
    ]
    assert {event["transaction_id"] for event in events} == {"tx-123"}


def test_capability_broker_rejects_transaction_id_without_transaction_context():
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        broker = CapabilityBroker(registry, manager)
        with pytest.raises(
            ValueError,
            match="transaction_id requires transaction_context=True",
        ):
            await broker.invoke(
                "fixture.echo_text",
                {"text": "blocked"},
                confirmation="INVOKE",
                transaction_id="fake-transaction",
            )

    asyncio.run(scenario())


def test_capability_broker_records_failed_event_on_provider_exception(
    tmp_path,
    monkeypatch,
):
    async def scenario():
        registry = CapabilityRegistry()
        manager = MCPClientManager(registry, discovery_timeout=10, invoke_timeout=10)
        transport = PythonStdioTransport(FIXTURE, cwd=str(PROJECT_ROOT))
        manager.add_provider("fixture", transport)
        await manager.discover_provider("fixture")
        store = EventStore(tmp_path / "events.sqlite3")
        broker = CapabilityBroker(registry, manager, store)

        async def broken(*_args, **_kwargs):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(manager, "call_tool", broken)
        with pytest.raises(RuntimeError, match="provider exploded"):
            await broker.invoke(
                "fixture.echo_text",
                {"text": "x"},
                confirmation="INVOKE",
            )
        events = store.query(capability_id="fixture.echo_text")["events"]
        store.close()
        return events

    events = asyncio.run(scenario())

    assert [event["event_type"] for event in events] == [
        "capability.before_invoke",
        "capability.failed",
    ]
    assert events[-1]["payload"]["exception_type"] == "RuntimeError"
    assert len(events[-1]["payload"]["message_sha256"]) == 64
    assert events[-1]["payload"]["message_length"] == len("provider exploded")
    assert "provider exploded" not in str(events)


def test_capability_validation_failure_emits_no_runtime_event(tmp_path):
    registry, manager, _broker = build_runtime()
    store = EventStore(tmp_path / "events.sqlite3")
    broker = CapabilityBroker(registry, manager, store)
    try:
        with pytest.raises(ValueError, match="Invalid arguments"):
            asyncio.run(
                broker.invoke(
                    "fixture.add_numbers",
                    {"a": 1},
                    confirmation="INVOKE",
                )
            )
        assert store.query()["events"] == []
    finally:
        store.close()


def test_event_persistence_failure_does_not_change_capability_result():
    class BrokenEventStore:
        def emit(self, *_args, **_kwargs):
            raise OSError("event database unavailable")

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    descriptor = CapabilityDescriptor(
        id="fixture.internal",
        provider_id="fixture",
        remote_name="internal",
        title="Internal Fixture",
        description="Internal fixture for event fail-open testing.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk_level="read",
        tags=("fixture", "event-test"),
    )
    registry.register_provider("fixture", [descriptor], enabled=True)
    broker = CapabilityBroker(registry, manager, BrokenEventStore())
    broker.register_internal_handler(
        "fixture.internal",
        lambda args: {"status": "completed", "echo": args["text"]},
    )

    result = asyncio.run(
        broker.invoke(
            "fixture.internal",
            {"text": "still-runs"},
        )
    )

    assert result["status"] == "completed"
    assert result["data"]["echo"] == "still-runs"

def _browser_click_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="browser.click",
        provider_id="browser",
        remote_name="browser_click",
        title="Click Browser Element",
        description="Browser click fixture with download artifact recovery.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        artifact_outputs=True,
        artifact_contract={
            "transport": "local_path",
            "inputs": [],
            "input_arrays": [],
            "outputs": [],
            "output_paths": {},
            "result_paths": [
                {
                    "pattern": (
                        r'Downloaded file .*? to '
                        r'"(?P<path>state\\browser\\.+?)"'
                    ),
                    "group": "path",
                    "root": "state/browser",
                    "remove_source": True,
                }
            ],
        },
        risk_level="write_external",
        requires_confirmation=False,
        tags=("browser", "download", "artifact"),
    )


def test_browser_download_recovers_completed_pdf_without_second_click(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_dir = tmp_path / "state" / "browser"
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace)
    monkeypatch.setattr(broker_module, "PROJECT_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        broker_module,
        "_BROWSER_DOWNLOAD_RECOVERY_TIMEOUT_SECONDS",
        0.2,
    )
    monkeypatch.setattr(
        broker_module,
        "_BROWSER_DOWNLOAD_RECOVERY_POLL_SECONDS",
        0.01,
    )

    runtime_starts = {"count": 0}

    def fake_start_browser_runtime():
        runtime_starts["count"] += 1
        return {"ready": True}

    monkeypatch.setattr(
        "browser_runtime.start_browser_runtime",
        fake_start_browser_runtime,
    )

    class FailingBrowserManager:
        def __init__(self):
            self.call_count = 0
            self.discovery_count = 0

        async def call_tool(self, provider_id, remote_name, arguments):
            assert provider_id == "browser"
            assert remote_name == "browser_click"
            assert arguments == {"target": "download"}
            self.call_count += 1
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "paper.pdf").write_bytes(
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
                b"trailer\n<<>>\n%%EOF\n"
            )
            raise RuntimeError("SSE stream ended without a response")

        def provider_status(self, provider_id):
            assert provider_id == "browser"
            return {"state": "degraded"}

        async def discover_provider(self, provider_id, *, force=False):
            assert provider_id == "browser"
            assert force is True
            self.discovery_count += 1
            return {"state": "ready"}

    registry = CapabilityRegistry()
    registry.register_provider(
        "browser",
        [_browser_click_descriptor()],
        enabled=True,
    )
    manager = FailingBrowserManager()
    broker = CapabilityBroker(registry, manager)

    result = asyncio.run(
        broker.invoke(
            "browser.click",
            {"target": "download"},
        )
    )

    assert result["status"] == "completed"
    assert result["is_error"] is False
    assert result["data"] == {
        "download_recovered": True,
        "provider_recovered": True,
        "artifact_count": 1,
    }
    assert result["meta"]["download_recovered"] is True
    assert result["meta"]["provider_recovered"] is True
    assert manager.call_count == 1
    assert manager.discovery_count == 1
    assert runtime_starts["count"] == 1
    assert not (output_dir / "paper.pdf").exists()

    artifact_id = result["artifact_outputs"]["result_1"]
    payload = artifact_bridge.read_artifact_bytes(artifact_id)
    assert payload.startswith(b"%PDF-")
    assert b"%%EOF" in payload


def test_browser_download_does_not_recover_incomplete_pdf(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_dir = tmp_path / "state" / "browser"
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace)
    monkeypatch.setattr(broker_module, "PROJECT_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        broker_module,
        "_BROWSER_DOWNLOAD_RECOVERY_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        broker_module,
        "_BROWSER_DOWNLOAD_RECOVERY_POLL_SECONDS",
        0.01,
    )

    runtime_starts = {"count": 0}

    def fake_start_browser_runtime():
        runtime_starts["count"] += 1
        return {"ready": True}

    monkeypatch.setattr(
        "browser_runtime.start_browser_runtime",
        fake_start_browser_runtime,
    )

    class IncompleteBrowserManager:
        def __init__(self):
            self.call_count = 0
            self.discovery_count = 0

        async def call_tool(self, *_args, **_kwargs):
            self.call_count += 1
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "partial.pdf").write_bytes(
                b"%PDF-1.4\npartial"
            )
            raise RuntimeError("SSE stream ended without a response")

        def provider_status(self, _provider_id):
            return {"state": "degraded"}

        async def discover_provider(self, _provider_id, *, force=False):
            assert force is True
            self.discovery_count += 1
            return {"state": "ready"}

    registry = CapabilityRegistry()
    registry.register_provider(
        "browser",
        [_browser_click_descriptor()],
        enabled=True,
    )
    manager = IncompleteBrowserManager()
    broker = CapabilityBroker(registry, manager)

    with pytest.raises(
        RuntimeError,
        match="SSE stream ended without a response",
    ):
        asyncio.run(
            broker.invoke(
                "browser.click",
                {"target": "download"},
            )
        )

    assert manager.call_count == 1
    assert manager.discovery_count == 1
    assert runtime_starts["count"] == 1
    assert (output_dir / "partial.pdf").exists()


def test_browser_click_business_error_does_not_restart_provider(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace)
    monkeypatch.setattr(broker_module, "PROJECT_ROOT", tmp_path.resolve())

    def forbidden_start():
        raise AssertionError("browser runtime should not restart")

    monkeypatch.setattr(
        "browser_runtime.start_browser_runtime",
        forbidden_start,
    )

    class ReadyBrowserManager:
        def __init__(self):
            self.call_count = 0

        async def call_tool(self, *_args, **_kwargs):
            self.call_count += 1
            raise RuntimeError("element not found")

        def provider_status(self, _provider_id):
            return {"state": "ready"}

        async def discover_provider(self, *_args, **_kwargs):
            raise AssertionError("provider should not be rediscovered")

    registry = CapabilityRegistry()
    registry.register_provider(
        "browser",
        [_browser_click_descriptor()],
        enabled=True,
    )
    manager = ReadyBrowserManager()
    broker = CapabilityBroker(registry, manager)

    with pytest.raises(RuntimeError, match="element not found"):
        asyncio.run(
            broker.invoke(
                "browser.click",
                {"target": "missing"},
            )
        )

    assert manager.call_count == 1

