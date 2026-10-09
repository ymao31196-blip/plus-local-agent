"""Deployment contract for user-configured Skill Sources behind PLA."""
from __future__ import annotations

import json
from pathlib import Path

from provider.provider_manifest import load_provider_manifests


ROOT = Path(__file__).resolve().parents[1]
LEGACY = {"list_skills", "search_skills", "read_skill", "refresh_library"}
MANAGED = {
    "source_manage", "source_list", "source_sync", "skill_search",
    "skill_read", "skill_resource", "skill_validate",
}


def test_skill_library_managed_tools_are_explicitly_reviewed() -> None:
    provider = load_provider_manifests(ROOT)["skill-library"]
    assert set(provider.tool_allowlist or []) == LEGACY | MANAGED
    assert set(provider.tool_overrides) == LEGACY | MANAGED
    for tool in MANAGED - {"source_manage", "source_sync"}:
        override = provider.tool_overrides[tool]
        assert override["risk_level"] == "read"
        assert override["requires_confirmation"] is False
    for tool in {"source_manage", "source_sync"}:
        override = provider.tool_overrides[tool]
        assert override["risk_level"] == "write_local"


def test_local_source_roots_are_explicit_and_within_project() -> None:
    config = json.loads((ROOT / "config" / "skill-library.json").read_text(encoding="utf-8"))
    roots = config["local_roots"]
    assert roots == ["workspace/skill-library"]
    for path in roots:
        assert not Path(path).is_absolute()
        assert (ROOT / path).resolve().is_relative_to(ROOT.resolve())
    assert "token" not in config
