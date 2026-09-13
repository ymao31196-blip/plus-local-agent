import asyncio

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager, _capability_suffix


def build_external_mcp():
    mcp = FastMCP("external-test")

    @mcp.tool(tags={"document", "pdf"})
    def compress_pdf(input_artifact: str, quality: str = "recommended") -> dict:
        """Compress one PDF artifact."""
        return {"artifact_id": input_artifact, "quality": quality}

    @mcp.tool
    def health_check() -> str:
        """Read provider health."""
        return "ok"

    return mcp


def test_discover_external_mcp_into_registry():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", build_external_mcp())

    state = asyncio.run(manager.discover_provider("external"))

    assert state["state"] == "ready"
    assert state["tool_count"] == 2
    result = registry.search("compress pdf")
    assert result["match_count"] == 1
    capability = result["capabilities"][0]
    assert capability["id"] == "external.compress_pdf"
    assert capability["risk_level"] == "privileged"
    assert capability["requires_confirmation"] is True
    assert capability["requires_transaction"] is False

    detail = registry.describe("external.compress_pdf")
    assert detail["remote_name"] == "compress_pdf"
    assert detail["input_schema"]["required"] == ["input_artifact"]
    assert {"mcp", "document", "pdf"} <= set(detail["tags"])


def test_discovery_refresh_replaces_remote_tool_set():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)

    first = FastMCP("first")

    @first.tool
    def old_tool() -> str:
        return "old"

    manager.add_provider("remote", first)
    asyncio.run(manager.discover_provider("remote"))
    assert registry.describe("remote.old_tool")["available"] is True

    second = FastMCP("second")

    @second.tool
    def new_tool() -> str:
        return "new"

    manager.add_provider("remote", second)
    asyncio.run(manager.discover_provider("remote"))

    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("remote.old_tool")
    assert registry.describe("remote.new_tool")["available"] is True


def test_disabled_provider_discovers_but_is_not_available():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", build_external_mcp(), enabled=False)

    asyncio.run(manager.discover_provider("external"))

    assert registry.search("")["match_count"] == 0
    hidden = registry.search("", include_unavailable=True)
    assert hidden["match_count"] == 2
    assert all(item["available"] is False for item in hidden["capabilities"])


def test_provider_can_be_enabled_after_discovery():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", build_external_mcp(), enabled=False)
    asyncio.run(manager.discover_provider("external"))

    manager.set_provider_enabled("external", True)

    assert registry.search("")["match_count"] == 2
    assert manager.provider_status("external")["enabled"] is True


def test_discover_all_isolates_provider_failures(monkeypatch):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("good", build_external_mcp())
    manager.add_provider("bad", object())

    original = manager._list_tools

    async def fake_list_tools(source, mode):
        if source is manager._sources["bad"]:
            raise RuntimeError("offline")
        return await original(source, mode)

    monkeypatch.setattr(manager, "_list_tools", fake_list_tools)
    result = asyncio.run(manager.discover_all())

    assert result["good"]["state"] == "ready"
    assert result["bad"]["state"] == "error"
    assert result["bad"]["error_type"] == "RuntimeError"
    assert registry.search("", provider_id="good")["match_count"] == 2


def test_discover_all_runs_provider_discovery_concurrently(monkeypatch):
    registry = CapabilityRegistry()
    manager = MCPClientManager(
        registry,
        discovery_timeout=1.0,
        discovery_concurrency=2,
    )
    manager.add_provider("alpha", build_external_mcp())
    manager.add_provider("beta", build_external_mcp())

    async def scenario():
        original = manager._list_tools
        entered: set[int] = set()
        both_started = asyncio.Event()

        async def coordinated_list_tools(source, mode):
            entered.add(id(source))
            if len(entered) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return await original(source, mode)

        monkeypatch.setattr(manager, "_list_tools", coordinated_list_tools)
        return await manager.discover_all()

    result = asyncio.run(scenario())

    assert result["alpha"]["state"] == "ready"
    assert result["beta"]["state"] == "ready"


def test_discovery_can_retry_after_failure(monkeypatch):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    source = build_external_mcp()
    manager.add_provider("external", source)

    calls = {"count": 0}
    original = manager._list_tools

    async def flaky(current, mode):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return await original(current, mode)

    monkeypatch.setattr(manager, "_list_tools", flaky)

    with pytest.raises(RuntimeError, match="temporary"):
        asyncio.run(manager.discover_provider("external"))
    assert manager.provider_status("external")["state"] == "error"

    blocked = manager.provider_status("external")
    assert blocked["consecutive_failures"] == 1
    assert blocked["retry_after"] is not None
    with pytest.raises(RuntimeError, match="retry backoff"):
        asyncio.run(manager.discover_provider("external"))

    state = asyncio.run(manager.discover_provider("external", force=True))
    assert state["state"] == "ready"
    assert state["consecutive_failures"] == 0
    assert state["retry_after"] is None
    assert registry.search("")["match_count"] == 2


def test_transport_failure_marks_provider_degraded_and_hides_capabilities(monkeypatch):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", build_external_mcp())
    asyncio.run(manager.discover_provider("external"))

    async def broken_call(source, remote_name, arguments, mode):
        raise ConnectionError("transport lost")

    monkeypatch.setattr(manager, "_call_tool", broken_call)

    with pytest.raises(ConnectionError, match="transport lost"):
        asyncio.run(manager.call_tool("external", "health_check", {}))

    state = manager.provider_status("external")
    assert state["state"] == "degraded"
    assert state["consecutive_failures"] == 1
    assert state["retry_after"] is not None
    assert state["error_type"] == "ConnectionError"
    assert registry.search("")["match_count"] == 0
    hidden = registry.search("", include_unavailable=True)
    assert hidden["match_count"] == 2
    assert all(item["available"] is False for item in hidden["capabilities"])


def test_successful_call_updates_lifecycle_latency():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", build_external_mcp())
    asyncio.run(manager.discover_provider("external"))

    result = asyncio.run(manager.call_tool("external", "health_check", {}))

    assert result.is_error is False
    state = manager.provider_status("external")
    assert state["state"] == "ready"
    assert state["last_success_at"] is not None
    assert state["last_latency_ms"] is not None
    assert state["last_latency_ms"] >= 0
    assert state["consecutive_failures"] == 0


def test_remote_tool_error_does_not_mark_provider_unhealthy():
    mcp = FastMCP("business-error")

    @mcp.tool
    def fail_business_rule() -> str:
        raise RuntimeError("business rule failed")

    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("external", mcp)
    asyncio.run(manager.discover_provider("external"))

    with pytest.raises(ToolError, match="business rule failed"):
        asyncio.run(manager.call_tool("external", "fail_business_rule", {}))

    state = manager.provider_status("external")
    assert state["state"] == "ready"
    assert state["consecutive_failures"] == 0
    assert registry.search("", provider_id="external")["match_count"] == 1


@pytest.mark.parametrize(
    ("remote_name", "suffix"),
    [
        ("compress_pdf", "compress_pdf"),
        ("PDF/Compress", "pdf_compress"),
        ("Foo Bar", "foo_bar"),
    ],
)
def test_capability_suffix_is_stable(remote_name, suffix):
    assert _capability_suffix(remote_name) == suffix


def test_normalized_remote_name_collision_is_rejected():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    mcp = FastMCP("collision")

    @mcp.tool(name="Foo Bar")
    def first() -> str:
        return "one"

    @mcp.tool(name="foo/bar")
    def second() -> str:
        return "two"

    manager.add_provider("external", mcp)
    with pytest.raises(ValueError, match="collide after capability normalization"):
        asyncio.run(manager.discover_provider("external"))


def test_invalid_provider_and_timeout_are_rejected():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="discovery_timeout"):
        MCPClientManager(registry, discovery_timeout=0)

    manager = MCPClientManager(registry)
    with pytest.raises(ValueError, match="Invalid provider id"):
        manager.add_provider("Bad.Provider", build_external_mcp())
    with pytest.raises(ValueError, match="mode"):
        manager.add_provider("badmode", build_external_mcp(), mode="future")


def test_legacy_mode_discovers_modern_test_server_without_auto_probe():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider("legacy", build_external_mcp(), mode="legacy")

    state = asyncio.run(manager.discover_provider("legacy"))

    assert state["state"] == "ready"
    assert registry.search("", provider_id="legacy")["match_count"] == 2


def test_tool_override_can_define_file_uri_artifact_contract_and_lower_risk():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "external",
        build_external_mcp(),
        tool_overrides={
            "compress_pdf": {
                "artifact_contract": {
                    "transport": "file_uri",
                    "inputs": ["input_artifact"],
                    "outputs": [],
                },
                "risk_level": "read",
                "requires_confirmation": False,
                "requires_transaction": True,
                "tags": ["document", "pdf"],
            }
        },
    )

    asyncio.run(manager.discover_provider("external"))
    detail = registry.describe("external.compress_pdf")

    assert detail["artifact_inputs"] == ["input_artifact"]
    assert detail["artifact_outputs"] is False
    assert detail["artifact_contract"]["transport"] == "file_uri"
    assert detail["artifact_contract"]["inputs"] == ["input_artifact"]
    assert detail["artifact_contract"]["outputs"] == []
    assert detail["artifact_contract"]["output_paths"] == {}
    assert detail["artifact_contract"]["policy"]["max_input_bytes"] > 0
    assert detail["artifact_contract"]["policy"]["max_output_artifacts"] > 0
    assert detail["risk_level"] == "read"
    assert detail["requires_confirmation"] is False
    assert detail["requires_transaction"] is True
    assert {"mcp", "document", "pdf"} <= set(detail["tags"])


def test_tool_allowlist_limits_registered_capabilities():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "limited",
        build_external_mcp(),
        tool_allowlist=["compress_pdf"],
    )

    state = asyncio.run(manager.discover_provider("limited"))

    assert state["tool_count"] == 1
    result = registry.search("", provider_id="limited")
    assert result["match_count"] == 1
    assert result["capabilities"][0]["id"] == "limited.compress_pdf"


def test_missing_allowlisted_tool_fails_discovery():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "limited",
        build_external_mcp(),
        tool_allowlist=["missing_tool"],
    )

    with pytest.raises(ValueError, match="missing from provider"):
        asyncio.run(manager.discover_provider("limited"))


def test_override_can_define_managed_output_path_and_public_schema():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "external",
        build_external_mcp(),
        tool_overrides={
            "compress_pdf": {
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
                            "filename": "result.bin",
                            "mime_type": "application/octet-stream",
                        }
                    },
                },
            }
        },
    )

    asyncio.run(manager.discover_provider("external"))
    detail = registry.describe("external.compress_pdf")

    assert detail["artifact_outputs"] is True
    assert detail["input_schema"]["required"] == ["input_artifact"]
    assert detail["artifact_contract"]["output_paths"] == {
        "output_path": {
            "filename": "result.bin",
            "mime_type": "application/octet-stream",
        }
    }


def test_provider_specific_timeouts_override_manager_defaults():
    manager = MCPClientManager(
        CapabilityRegistry(),
        discovery_timeout=10,
        invoke_timeout=60,
    )

    manager.add_provider(
        "slow",
        build_external_mcp(),
        discovery_timeout=45,
        invoke_timeout=90,
    )
    manager.add_provider("default", build_external_mcp())

    assert manager._discovery_timeouts["slow"] == 45.0
    assert manager._invoke_timeouts["slow"] == 90.0
    assert manager._discovery_timeouts["default"] == 10.0
    assert manager._invoke_timeouts["default"] == 60.0


def test_tool_override_can_define_public_capability_name():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "browser",
        build_external_mcp(),
        tool_allowlist=["health_check"],
        tool_overrides={
            "health_check": {
                "public_name": "status",
                "risk_level": "read",
                "requires_confirmation": False,
            }
        },
    )

    asyncio.run(manager.discover_provider("browser"))

    detail = registry.describe("browser.status")
    assert detail["remote_name"] == "health_check"
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.describe("browser.health_check")


def test_persistent_session_reuses_one_client_until_explicit_close():
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    manager.add_provider(
        "browser",
        build_external_mcp(),
        persistent_session=True,
    )

    async def scenario():
        await manager.discover_provider("browser")
        first_client = manager._persistent_clients["browser"]

        first = await manager.call_tool("browser", "health_check", {})
        second = await manager.call_tool("browser", "health_check", {})

        assert first.is_error is False
        assert second.is_error is False
        assert manager._persistent_clients["browser"] is first_client

        await manager.close_provider_session("browser")
        assert "browser" not in manager._persistent_clients

    asyncio.run(scenario())
