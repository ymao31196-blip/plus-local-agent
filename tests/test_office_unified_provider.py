"""Contracts for the optional unified Office composition.

Real PowerPoint/WPS COM calls are covered by separate on-machine E2E;
these tests never modify a user's open Office document.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastmcp import Client

from providers import office_server as office
from fastmcp.server.providers.proxy import ProxyProvider
from provider.provider_manifest import load_provider_manifests

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_unified_office_manifest_preserves_specialist_security_overrides():
    manifests = load_provider_manifests(PROJECT_ROOT)
    merged = manifests["office"]
    wps = manifests["wps-office"]
    enhancement = manifests["office-enhancement"]
    assert merged.runtime_kind == "isolated_python_stdio"
    assert merged.routing_authority == "preferred"
    assert merged.args == ("providers/office_server.py",)
    for name, config in {**wps.tool_overrides, **enhancement.tool_overrides}.items():
        assert merged.tool_overrides[name] == config
    assert merged.tool_overrides["office_status"]["risk_level"] == "read"
    # Unlike the former experimental combined Provider, do not require a
    # hardcoded list including nonexistent tools such as wps_macro.
    assert merged.tool_allowlist is None


def test_reviewed_wps_source_includes_windows_ppt_compatibility_patch():
    spec = json.loads((PROJECT_ROOT / "provider_specs/wps-office.source.json").read_text(encoding="utf-8"))
    patches = spec["patches"]
    assert patches == [
        "provider_patches/wps-office/windows-com-compat.patch",
        "provider_patches/wps-office/windows-ppt-actions.patch",
    ]
    patch = (PROJECT_ROOT / patches[1]).read_text(encoding="utf-8")
    assert '"setSlideSize" {' in patch
    assert '"setFontColor" {' in patch
    assert "shapeName = $shape.Name; slideIndex = $slideIndex" in patch
    assert "name = $shape.Name" in patch


def test_missing_optional_engines_do_not_prevent_health_discovery(tmp_path):
    async def verify():
        async with Client(office.create_server(tmp_path)) as client:
            tools = await client.list_tools()
            assert {item.name for item in tools} == {"office_status", "office_preview_image"}
            status = (await client.call_tool("office_status", {})).data
            assert status["status"] == "degraded"
            assert status["backends"]["wps"]["last_discovery"] == "unavailable"
            assert status["backends"]["enhancement"]["last_discovery"] == "unavailable"
    asyncio.run(verify())


def test_desktop_unavailable_artifact_tool_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setenv("PLA_DESKTOP_RUNTIME", "1")
    monkeypatch.setattr(office, "ROOT", tmp_path)

    async def fake_list_tools(self):
        return [
            SimpleNamespace(name="artifact_build_overlay"),
            SimpleNamespace(name="pptx_inspect"),
        ]

    monkeypatch.setattr(ProxyProvider, "_list_tools", fake_list_tools)
    provider = office.IsolatedOfficeBackend("enhancement", {
        "available": True, "command": "unused", "args": [], "cwd": str(tmp_path)
    })
    result = asyncio.run(provider._list_tools())
    assert [item.name for item in result] == ["pptx_inspect"]
    assert office.BACKEND_STATES["enhancement"]["unavailable_tools"] == ["artifact_build_overlay"]


def test_proxy_keeps_unrelated_backend_when_one_discovery_fails(monkeypatch, tmp_path):
    async def fake_list_tools(self):
        if self.backend_name == "wps":
            raise ConnectionError("WPS unavailable")
        return [SimpleNamespace(name="pptx_inspect")]

    monkeypatch.setattr(ProxyProvider, "_list_tools", fake_list_tools)
    args = {"available": True, "command": "unused", "args": [], "cwd": str(tmp_path)}
    failed = office.IsolatedOfficeBackend("wps", args)
    healthy = office.IsolatedOfficeBackend("enhancement", args)
    assert asyncio.run(failed._list_tools()) == []
    assert office.BACKEND_STATES["wps"]["last_discovery"] == "unavailable"
    assert [item.name for item in asyncio.run(healthy._list_tools())] == ["pptx_inspect"]
    assert office.BACKEND_STATES["enhancement"]["last_discovery"] == "ok"

def test_unpatched_wps_windows_actions_are_quarantined(monkeypatch, tmp_path):
    if office.os.name != "nt":
        return
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    bridge = scripts / "wps-com.ps1"
    bridge.write_text('# old bridge without the reviewed PPT actions', encoding="utf-8")

    async def fake_list_tools(self):
        return [
            SimpleNamespace(name="wps_common_ping"),
            SimpleNamespace(name="wps_ppt_set_slide_size"),
            SimpleNamespace(name="wps_ppt_set_font_color"),
            SimpleNamespace(name="wps_ppt_add_textbox"),
        ]

    monkeypatch.setattr(ProxyProvider, "_list_tools", fake_list_tools)
    provider = office.IsolatedOfficeBackend("wps", {
        "available": True, "command": "unused", "args": [], "cwd": str(tmp_path)
    })
    tools = asyncio.run(provider._list_tools())
    assert [item.name for item in tools] == ["wps_common_ping"]
    assert len(office.BACKEND_STATES["wps"]["unavailable_tools"]) == 3

    bridge.write_text(
        '"setSlideSize" {\n"setFontColor" {\n'
        'name = $shape.Name; shapeName = $shape.Name; slideIndex = $slideIndex',
        encoding="utf-8",
    )
    recovered = asyncio.run(provider._list_tools())
    assert len(recovered) == 4
    assert office.BACKEND_STATES["wps"]["unavailable_tools"] == []

