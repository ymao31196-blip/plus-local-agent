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
    assert roots == []  # public defaults do not grant access to any local files
    assert "repo" not in config  # each user configures their own GitHub sources
    for path in roots:
        assert not Path(path).is_absolute()
        assert (ROOT / path).resolve().is_relative_to(ROOT.resolve())
    assert "token" not in config
    assert not (ROOT / "workspace" / "skill-library" / "src").as_posix() in str(config)


def test_operator_private_source_config_is_ignored_by_git() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/skill-library.local.json" in ignored
    config = json.loads((ROOT / "config" / "skill-library.json").read_text(encoding="utf-8"))
    assert "repo" not in config
    assert config["local_roots"] == []


def test_provider_accepts_checkout_or_installed_skill_library() -> None:
    entrypoint = (ROOT / "providers" / "skill_library_provider.py").read_text(encoding="utf-8")
    assert 'if source_package.is_dir():' in entrypoint
    assert 'if LOCAL_CONFIG_PATH.is_file():' in entrypoint
    assert 'if config.get("repo"):' in entrypoint
    assert 'from skill_library.server import main' in entrypoint
