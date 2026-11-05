"""CHAGENT dynamic route hints (additive, no MCP schema changes)."""
from pathlib import Path

import pytest

from capabilities.capability_models import CapabilityDescriptor
from capabilities.capability_registry import CapabilityRegistry
from routing.agent_guide import live_route_advice


DEFAULT = """# Dynamic route preferences

```pla-routing
{
  "schema_version": 1,
  "routes": [
    {"id": "special", "keywords": ["元素复用", "复用元素"], "providers": ["office-enhancement", "wps-office"]},
    {"id": "routine", "keywords": ["PPT", "幻灯片"], "providers": ["wps-office", "office-enhancement"]}
  ]
}
```
"""


def register(reg, provider, capability):
    reg.register_provider(provider, [
        CapabilityDescriptor(
            id=f"{provider}.{capability}", provider_id=provider,
            remote_name=capability, title=capability, description="Document operations",
            input_schema={"type": "object"}, tags=("ppt",),
        )
    ])


def test_live_hot_update_and_preferred_available(tmp_path):
    reg = CapabilityRegistry()
    register(reg, "wps-office", "wps_ppt_add_slide")
    register(reg, "office-enhancement", "pptx_overlay")
    path = tmp_path / "CHAGENT.md"
    path.write_text(DEFAULT, encoding="utf-8")
    first = live_route_advice("帮我制作PPT", reg, default_path=path)
    assert first["preferred_provider"] == "wps-office"
    assert first["rule_id"] == "routine"
    assert first["providers"][0]["candidate_capabilities"][0]["id"] == "wps-office.wps_ppt_add_slide"
    assert live_route_advice("帮我复用元素制作PPT", reg, default_path=path)["rule_id"] == "special"
    path.write_text(DEFAULT.replace('"wps-office", "office-enhancement"', '"office-enhancement", "wps-office"'), encoding="utf-8")
    updated = live_route_advice("帮我制作PPT", reg, default_path=path)
    assert updated["preferred_provider"] == "office-enhancement"
    assert first["revision"] != updated["revision"]


def test_disabled_preferred_uses_live_fallback_without_reconnect(tmp_path):
    reg = CapabilityRegistry()
    register(reg, "wps-office", "wps_ppt_add_slide")
    register(reg, "office-enhancement", "pptx_overlay")
    path = tmp_path / "CHAGENT.md"
    path.write_text(DEFAULT, encoding="utf-8")
    reg.set_provider_enabled("wps-office", False)
    hint = live_route_advice("做一页PPT", reg, default_path=path)
    assert hint["preferred_provider"] == "office-enhancement"
    assert hint["providers"][0]["available"] is False
    assert hint["providers"][1]["available"] is True
    reg.set_provider_enabled("office-enhancement", False)
    assert live_route_advice("做一页PPT", reg, default_path=path)["preferred_provider"] is None


def test_user_overlay_preferred_and_invalid_not_silent(tmp_path):
    reg = CapabilityRegistry()
    register(reg, "wps-office", "wps_ppt_add_slide")
    base = tmp_path / "base.md"
    base.write_text(DEFAULT, encoding="utf-8")
    data = tmp_path / "data"
    override = data / "config" / "CHAGENT.md"
    override.parent.mkdir(parents=True)
    override.write_text(DEFAULT.replace('"wps-office", "office-enhancement"', '"office-enhancement", "wps-office"'), encoding="utf-8")
    result = live_route_advice("PPT", reg, data_root=data, default_path=base)
    assert result["providers"][0]["provider_id"] == "office-enhancement"
    override.write_text("# malformed", encoding="utf-8")
    bad = live_route_advice("PPT", reg, data_root=data, default_path=base)
    assert bad == {"status": "config_error", "code": "routing_block_missing"}


def test_unsafe_symlink_rejected(tmp_path):
    reg = CapabilityRegistry()
    path = tmp_path / "CHAGENT.md"
    actual = tmp_path / "target.md"
    actual.write_text(DEFAULT, encoding="utf-8")
    try:
        path.symlink_to(actual)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    assert live_route_advice("PPT", reg, default_path=path)["code"] == "symlink_not_allowed"


def test_unrelated_queries_get_no_advisory(tmp_path):
    reg = CapabilityRegistry()
    p = tmp_path / "CHAGENT.md"
    p.write_text(DEFAULT, encoding="utf-8")
    assert live_route_advice("查询代码仓库状态", reg, default_path=p) is None


def test_only_recognized_provider_ids_no_markdown_instruction_reflection(tmp_path):
    reg = CapabilityRegistry()
    p = tmp_path / "CHAGENT.md"
    p.write_text(DEFAULT.replace("office-enhancement", "new-uninstalled"), encoding="utf-8")
    result = live_route_advice("PPT", reg, default_path=p)
    assert result["preferred_provider"] is None
    assert result["providers"][1]["provider_id"] == "new-uninstalled"
    assert result["providers"][1]["available"] is False


def test_server_search_adds_hot_route_without_new_tool_arguments(monkeypatch, tmp_path):
    import server

    registry = CapabilityRegistry()
    register(registry, "office", "wps_ppt_add_slide")
    register(registry, "wps-office", "wps_ppt_add_slide")
    register(registry, "office-enhancement", "pptx_overlay")
    register(registry, "drawio-official-mcp", "open_drawio_xml")
    monkeypatch.setattr(server, "CAPABILITY_REGISTRY", registry)
    monkeypatch.setenv("PLA_DATA_ROOT", str(tmp_path))
    # No override yet: read source default from the repository.
    default = server.capability_search("制作PPT")
    assert default["routing_advice"]["preferred_provider"] == "office"
    assert default["routing_advice"]["rule_id"] == "office-routine"
    assert default["routing_advice"]["source"] == "CHAGENT.md"
    assert "capability_search" in server.mcp.instructions
    assert "routing_advice" in server.mcp.instructions
    assert len(server.mcp.instructions) <= 512
    assert server.capability_search("基于PPT模板制作汇报")["routing_advice"]["rule_id"] == "office-precision"
    draw = server.capability_search("用draw.io绘制架构图")["routing_advice"]
    assert draw["rule_id"] == "drawio-explicit"
    assert draw["preferred_provider"] == "drawio-official-mcp"
    assert server.capability_search("绘制技术路线图")["routing_advice"]["rule_id"] == "drawio-diagram"
    assert server.capability_search("在PPT绘制流程图")["routing_advice"]["preferred_provider"] == "office"
    assert server.capability_search("论文润色")["routing_advice"]["rule_id"] == "skill-workflow"

    override = tmp_path / "config" / "CHAGENT.md"
    override.parent.mkdir()
    override.write_text(DEFAULT.replace('"wps-office", "office-enhancement"',
                                        '"office-enhancement", "wps-office"'),
                        encoding="utf-8")
    revised = server.capability_search("制作PPT")
    assert revised["routing_advice"]["preferred_provider"] == "office-enhancement"
    assert revised["routing_advice"]["revision"] != default["routing_advice"]["revision"]
    assert set(("query", "provider_id", "capabilities",
                "match_count", "returned_count", "truncated")) <= set(revised)

