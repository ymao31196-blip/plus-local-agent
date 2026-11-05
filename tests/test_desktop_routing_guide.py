"""Desktop CHAGENT viewer/editor: optimistic concurrency and live rules."""
from pathlib import Path

import pytest

from desktop_runtime.manager import Manager
from desktop_runtime.pet_viewer import MAX_ROWS
from desktop_runtime.routing_guide import read_guide
from routing.agent_guide import live_route_advice
from capabilities.capability_registry import CapabilityRegistry
from capabilities.capability_models import CapabilityDescriptor

ROOT = Path(__file__).resolve().parents[1]


def test_routing_editor_view_and_hot_save(tmp_path):
    manager = Manager(tmp_path / "data", ROOT)
    first = manager.dispatch("routing_guide_read", {})
    assert first["source"] == "default"
    assert first["validation"]["status"] == "valid"
    assert first["validation"]["route_count"] >= 4
    assert "office-routine" in first["content"]

    changed = first["content"].replace(
        '"providers": ["office", "wps-office", "office-enhancement"]',
        '"providers": ["office-enhancement", "office", "wps-office"]',
    )
    assert changed != first["content"]
    saved = manager.dispatch("routing_guide_save", {
        "content": changed,
        "expected_sha256": first["expected_sha256"],
    })
    assert saved["source"] == "personal"
    assert saved["validation"]["status"] == "valid"
    assert saved["content"] == changed
    assert saved["expected_sha256"] != first["expected_sha256"]
    assert (manager.config.root / "config" / "CHAGENT.md").read_text(encoding="utf-8") == changed

    registry = CapabilityRegistry()
    for provider in ("wps-office", "office-enhancement"):
        registry.register_provider(provider, [
            CapabilityDescriptor(
                id=provider + ".ppt_edit", provider_id=provider,
                remote_name="ppt_edit", title="PPT editing",
                description="Office editing", input_schema={"type": "object"}
            )
        ])
    live = live_route_advice("制作PPT", registry, data_root=manager.config.root)
    assert live["preferred_provider"] == "office-enhancement"


def test_routing_editor_rejects_stale_and_invalid_save(tmp_path):
    manager = Manager(tmp_path / "data", ROOT)
    state = manager.dispatch("routing_guide_read", {})
    original = state["content"]
    valid = original + "\n<!-- custom note -->\n"
    first = manager.dispatch("routing_guide_save", {
        "content": valid, "expected_sha256": state["expected_sha256"],
    })
    with pytest.raises(ValueError, match="changed since last read"):
        manager.dispatch("routing_guide_save", {
            "content": original, "expected_sha256": state["expected_sha256"],
        })
    with pytest.raises(ValueError, match="routing_block_missing"):
        manager.dispatch("routing_guide_save", {
            "content": "# invalid", "expected_sha256": first["expected_sha256"],
        })
    assert manager.dispatch("routing_guide_read", {})["content"] == valid


def test_routing_editor_rejects_symlink_and_bad_args(tmp_path):
    manager = Manager(tmp_path / "data", ROOT)
    with pytest.raises(ValueError, match="no arguments"):
        manager.dispatch("routing_guide_read", {"unexpected": True})
    with pytest.raises(ValueError, match="requires content"):
        manager.dispatch("routing_guide_save", {"content": "x"})
    victim = tmp_path / "victim.md"
    victim.write_text("do not touch", encoding="utf-8")
    target = manager.config.root / "config" / "CHAGENT.md"
    try:
        target.symlink_to(victim)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        manager.dispatch("routing_guide_read", {})
    assert victim.read_text(encoding="utf-8") == "do not touch"


def test_compact_pet_retains_four_calls():
    assert MAX_ROWS == 4



def test_desktop_ui_routing_editor_wired_to_manager_and_tauri():
    html = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
    js = (ROOT / "desktop/ui/app.js").read_text(encoding="utf-8")
    rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    for label in (
        "routing-guide-panel", "routing-guide-content", "routing-guide-reload",
        "routing-guide-default", "routing-guide-save", "routing-guide-dirty"
    ):
        assert f'id="{label}"' in html
        assert ("'" + label + "'") in js
    assert "routingGuideDirty()" in js
    assert "expected_sha256: routingGuideSnapshot.expected_sha256" in js
    assert "rpc('routing_guide_read')" in js
    assert "rpc('routing_guide_save'," in js
    for command in ("routing_guide_read", "routing_guide_save"):
        assert ('"' + command + '"') in rust
