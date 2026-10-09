"""Deployment contract for user-configured Skill Sources behind PLA."""
from __future__ import annotations

import json
from pathlib import Path

from provider.provider_manifest import load_provider_manifests
from capabilities.capability_models import CapabilityDescriptor
from capabilities.capability_registry import CapabilityRegistry


ROOT = Path(__file__).resolve().parents[1]
LEGACY = {"list_skills", "search_skills", "read_skill", "refresh_library"}
MANAGED = {
    "source_manage", "source_list", "source_sync", "skill_search",
    "skill_read", "skill_resource", "skill_validate",
    "skill_prepare", "skill_apply_local",
    "skill_states", "skill_toggle", "skill_plan_change", "skill_apply_change",
    "skill_publish_plan", "skill_restore_local",
    "skill_publication_prepare", "skill_publication_apply",
}


def test_skill_library_managed_tools_are_explicitly_reviewed() -> None:
    provider = load_provider_manifests(ROOT)["skill-library"]
    assert set(provider.tool_allowlist or []) == MANAGED
    assert not (LEGACY & set(provider.tool_allowlist or []))
    assert set(provider.tool_overrides) == MANAGED
    assert "Preferred Skill discovery entrypoint" in provider.tool_overrides["skill_search"]["description"]
    assert "source-qualified ref" in provider.tool_overrides["skill_read"]["description"]
    assert "DO NOT call skill-library.load again for the same ref" in provider.tool_overrides["skill_search"]["description"]
    assert "DO NOT reload that same ref" in provider.tool_overrides["skill_read"]["description"]
    for tool in MANAGED - {"source_manage", "source_sync", "skill_prepare", "skill_apply_local", "skill_toggle", "skill_apply_change", "skill_restore_local", "skill_publication_apply", "skill_publication_prepare"}:
        override = provider.tool_overrides[tool]
        assert override["risk_level"] == "read"
        assert override["requires_confirmation"] is False
    for tool in {"source_manage", "source_sync", "skill_prepare", "skill_apply_local", "skill_toggle", "skill_apply_change", "skill_restore_local", "skill_publication_apply", "skill_publication_prepare"}:
        override = provider.tool_overrides[tool]
        assert override["risk_level"] == "write_local"
    assert provider.tool_overrides["skill_apply_local"]["requires_confirmation"] is True
    assert provider.tool_overrides["skill_apply_change"]["requires_confirmation"] is True
    assert provider.tool_overrides["skill_restore_local"]["requires_confirmation"] is True
    assert provider.tool_overrides["skill_publication_apply"]["requires_confirmation"] is True
    assert provider.tool_overrides["skill_publication_prepare"]["risk_level"] == "write_local"
    assert provider.tool_overrides["skill_publication_prepare"]["requires_confirmation"] is False
    assert provider.tool_overrides["skill_plan_change"]["risk_level"] == "read"
    assert provider.tool_overrides["skill_prepare"]["requires_confirmation"] is False


def test_task_language_discovers_skill_entrypoint_without_skill_keyword() -> None:
    manifest = load_provider_manifests(ROOT)["skill-library"]
    rule = manifest.tool_overrides["skill_search"]
    registry = CapabilityRegistry()
    registry.register_provider(
        "skill-library",
        [
            CapabilityDescriptor(
                id="skill-library.find",
                provider_id="skill-library",
                remote_name="skill_search",
                title="Find Skills",
                description=rule["description"],
                tags=tuple(rule["tags"]),
                risk_level="read",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        ],
    )
    registry.register_provider(
        "wps",
        [
            CapabilityDescriptor(
                id="wps.beautify",
                provider_id="wps",
                remote_name="beautify",
                title="PPT Optimize",
                description="优化PPT版式和文字表达。",
                input_schema={"type": "object"},
            )
        ],
    )
    for utterance in (
        "帮我润色这个论文",
        "改写论文摘要",
        "优化这篇学术论文的表达",
        "整理Word里的图片作为论文配图",
        "按GitHub格式修复公式",
    ):
        matches = registry.search(utterance, limit=10)["capabilities"]
        assert matches and matches[0]["id"] == "skill-library.find", utterance
    for utterance in ("介绍什么是PCA", "天气预报", "创建一个空文件"):
        assert not any(
            match["id"] == "skill-library.find"
            for match in registry.search(utterance, limit=10)["capabilities"]
        )


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
    assert config.get("writable_roots", []) == []


def test_provider_accepts_checkout_or_installed_skill_library() -> None:
    entrypoint = (ROOT / "providers" / "skill_library_provider.py").read_text(encoding="utf-8")
    assert 'if source_package.is_dir():' in entrypoint
    assert 'if LOCAL_CONFIG_PATH.is_file():' in entrypoint
    assert 'if config.get("repo"):' in entrypoint
    assert 'from skill_library.server import main' in entrypoint
    assert 'SKILL_LIBRARY_WRITE_ROOTS' in entrypoint
    assert 'skill-library.writable_roots must be inside local_roots' in entrypoint
