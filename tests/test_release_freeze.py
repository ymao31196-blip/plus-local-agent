from pathlib import Path

import server
from provider_manifest import load_provider_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_provider_catalog():
    assert server.mcp.version == "1.5.3"
    manifests = load_provider_manifests(PROJECT_ROOT)
    assert set(manifests) == {
        "browser",
        "computer",
        "docx",
        "markitdown",
        "pdf",
        "software-migration",
        "winget",
        "windows-management",
    }
    assert manifests["winget"].runtime_kind == "executable_stdio"
    assert manifests["computer"].runtime_kind == "isolated_python_stdio"
    assert manifests["windows-management"].runtime_kind == "isolated_python_stdio"
    assert manifests["software-migration"].runtime_kind == "isolated_python_stdio"

    runtime_caps = server.CAPABILITY_REGISTRY.search(
        "",
        provider_id="runtime",
        include_unavailable=True,
        limit=20,
    )
    runtime_ids = {item["id"] for item in runtime_caps["capabilities"]}
    assert runtime_caps["match_count"] == 18
    assert runtime_ids == {
        "runtime.provider_status",
        "runtime.provider_setup",
        "runtime.provider_rescan",
        "runtime.provider_reload",
        "runtime.provider_enable",
        "runtime.provider_disable",
        "runtime.observer_status",
        "runtime.observer_rescan",
        "runtime.observer_reload",
        "runtime.observer_enable",
        "runtime.observer_disable",
        "runtime.lifecycle_status",
        "runtime.restart_http",
        "runtime.restart_status",
        "runtime.browser_status",
        "runtime.browser_diagnostics",
        "runtime.browser_start",
        "runtime.browser_stop",
    }

    core_caps = server.CAPABILITY_REGISTRY.search(
        "",
        provider_id="core",
        include_unavailable=True,
        limit=100,
    )
    assert core_caps["match_count"] == 15
    assert {item["id"] for item in core_caps["capabilities"]} == {
        "core.workspace_roots_get",
        "core.workspace_root_upsert",
        "core.workspace_root_remove",
        "core.transaction_create",
        "core.transaction_get",
        "core.transaction_checkpoint",
        "core.transaction_finalize",
        "core.transaction_invoke",
        "core.git_tag",
        "core.git_push",
        "core.event_query",
        "core.hook_status",
        "core.hook_invocation_query",
        "core.gate_status",
        "core.gate_decision_query",
    }

    release_caps = server.CAPABILITY_REGISTRY.search(
        "release",
        provider_id="core",
        include_unavailable=True,
        limit=20,
    )
    assert {item["id"] for item in release_caps["capabilities"]} == {
        "core.git_tag",
        "core.git_push",
    }
    assert all(
        item["requires_confirmation"] is True
        for item in release_caps["capabilities"]
    )


def test_release_entrypoint_documents_exist():
    assert (PROJECT_ROOT / "README.md").is_file()
    assert (PROJECT_ROOT / "docs" / "v1_overview.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.0.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.1.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.2.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.3.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.4.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.4.1.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.4.2.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.5.0.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.5.1.md").is_file()
    assert (PROJECT_ROOT / "docs" / "release_v1.5.3.md").is_file()
    assert (PROJECT_ROOT / "docs" / "v1_2_threat_model.md").is_file()
    assert (PROJECT_ROOT / "CHANGELOG.md").is_file()
    assert (PROJECT_ROOT / "requirements-core.txt").is_file()
    assert (PROJECT_ROOT / "requirements-dev.txt").is_file()


def test_manual_agent_fixture_is_intentionally_broken():
    calculator = (
        PROJECT_ROOT / "workspace" / "agent_test" / "calculator.py"
    ).read_text(encoding="utf-8")
    test_file = (
        PROJECT_ROOT / "workspace" / "agent_test" / "test_calculator.py"
    ).read_text(encoding="utf-8")
    assert "return a - b" in calculator
    assert "assert add(2, 3) == 5" in test_file
