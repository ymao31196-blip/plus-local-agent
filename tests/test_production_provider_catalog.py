import json
from pathlib import Path

from provider_manifest import load_provider_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_provider_catalog_matches_specs():
    manifests = load_provider_manifests(PROJECT_ROOT)
    manifest_ids = set(manifests)
    spec_ids = set()
    for path in (PROJECT_ROOT / "provider_specs").glob("*.txt"):
        name = path.name
        if name.endswith(".npm.txt"):
            spec_ids.add(name[: -len(".npm.txt")])
        else:
            spec_ids.add(path.stem)

    assert manifest_ids == {
        "browser",
        "computer",
        "docx",
        "markitdown",
        "pdf",
        "software-migration",
        "winget",
        "windows-management",
    }
    assert spec_ids == {
        "browser",
        "computer",
        "docx",
        "markitdown",
        "pdf",
        "software-migration",
        "windows-management",
    }
    assert manifests["winget"].runtime_kind == "executable_stdio"
    assert manifests["winget"].python_path is None
    assert all(manifest.autostart for manifest in manifests.values())


def test_computer_provider_is_semantic_first_and_version_pinned():
    manifests = load_provider_manifests(PROJECT_ROOT)
    provider = manifests["computer"]

    assert provider.runtime_kind == "isolated_python_stdio"
    assert provider.mode == "auto"
    assert set(provider.tool_allowlist or ()) == {
        "backend_status",
        "list_windows",
        "inspect",
        "search",
        "get_property",
        "get_value",
        "get_focused",
        "invoke",
        "set_value",
        "focus",
        "scroll_into_view",
        "scroll",
        "wait_for",
        "screenshot",
        "click",
        "type_text",
        "press_key",
    }

    forbidden = {
        "drag",
        "touch",
        "pen",
        "record",
        "send_keys",
        "run",
        "evaluate",
    }
    assert forbidden.isdisjoint(set(provider.tool_allowlist or ()))

    screenshot = provider.tool_overrides["screenshot"]
    assert screenshot["risk_level"] == "read"
    assert screenshot["requires_confirmation"] is False
    assert "capture_screen" not in screenshot["input_schema"]["properties"]
    assert screenshot["artifact_contract"]["policy"]["allowed_output_mime_types"] == [
        "image/png"
    ]

    click = provider.tool_overrides["click"]
    assert click["risk_level"] == "write_external"
    assert "input-injection" in click["tags"]

    python_lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "computer.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    node_lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "computer.npm.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert python_lines == ["fastmcp==4.0.3", "mcp==2.2.0"]
    assert node_lines == ["@microsoft/winappcli@0.5.0"]


def test_winget_provider_exposes_reviewed_search_and_install_capabilities():
    manifests = load_provider_manifests(PROJECT_ROOT)
    winget = manifests["winget"]

    assert winget.runtime_kind == "executable_stdio"
    assert winget.tool_allowlist == (
        "find-winget-packages",
        "install-winget-package",
    )

    find_override = winget.tool_overrides["find-winget-packages"]
    assert find_override["risk_level"] == "read"
    assert find_override["requires_confirmation"] is False

    install_override = winget.tool_overrides["install-winget-package"]
    assert install_override["risk_level"] == "privileged"
    assert install_override["requires_confirmation"] is True


def test_windows_management_provider_exposes_reviewed_observation_and_uninstall():
    manifests = load_provider_manifests(PROJECT_ROOT)
    provider = manifests["windows-management"]

    assert provider.runtime_kind == "isolated_python_stdio"
    assert provider.mode == "legacy"
    assert len(provider.tool_allowlist or ()) == 24
    assert {
        "list_installed_software",
        "find_uninstall_entries",
        "find_process_locking_file",
        "get_disk_usage",
        "analyze_directory_space",
        "get_process_details",
        "list_services",
        "list_env_vars",
        "get_gpu_details",
        "get_listening_ports",
        "get_security_status",
        "uninstall_software",
    }.issubset(set(provider.tool_allowlist or ()))
    assert {
        "execute_powershell",
        "manage_registry",
        "manage_service",
        "delete_file",
        "install_windows_updates",
    }.isdisjoint(set(provider.tool_allowlist or ()))

    uninstall = provider.tool_overrides["uninstall_software"]
    assert uninstall["risk_level"] == "destructive"
    assert uninstall["requires_confirmation"] is True
    assert uninstall["requires_transaction"] is True

    read_only_names = set(provider.tool_overrides) - {"uninstall_software"}
    assert all(
        provider.tool_overrides[name].get("risk_level") == "read"
        and provider.tool_overrides[name].get("requires_confirmation") is False
        for name in read_only_names
    )


def test_windows_management_provider_spec_is_exactly_pinned():
    lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "windows-management.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert lines == [
        "windows-management-mcp-server==0.3.1",
        "mcp==1.30.0",
        "fastmcp==3.4.7",
    ]


def test_software_migration_provider_has_controlled_execution_surface():
    manifests = load_provider_manifests(PROJECT_ROOT)
    provider = manifests["software-migration"]

    assert provider.runtime_kind == "isolated_python_stdio"
    assert provider.mode == "auto"
    assert provider.tool_allowlist == (
        "assess_software_migration",
        "preview_winget_reinstall",
        "prepare_migration",
        "execute_registered_uninstaller",
        "elevation_broker_status",
        "elevated_uninstall_status",
        "execute_winget_uninstall",
        "execute_elevated_winget_install",
        "elevated_install_status",
        "execute_winget_install",
        "verify_migration",
    )

    install = provider.tool_overrides["execute_winget_install"]
    assert install["risk_level"] == "privileged"
    assert install["requires_confirmation"] is True
    assert install["requires_transaction"] is True

    uninstall = provider.tool_overrides["execute_winget_uninstall"]
    assert uninstall["risk_level"] == "destructive"
    assert uninstall["requires_confirmation"] is True
    assert uninstall["requires_transaction"] is True

    registered = provider.tool_overrides["execute_registered_uninstaller"]
    assert registered["risk_level"] == "destructive"
    assert registered["requires_confirmation"] is True
    assert registered["requires_transaction"] is True

    elevated_install = provider.tool_overrides["execute_elevated_winget_install"]
    assert elevated_install["risk_level"] == "privileged"
    assert elevated_install["requires_confirmation"] is True
    assert elevated_install["requires_transaction"] is True

    read_only_names = set(provider.tool_overrides) - {
        "execute_winget_install",
        "execute_elevated_winget_install",
        "execute_winget_uninstall",
        "execute_registered_uninstaller",
    }
    assert all(
        provider.tool_overrides[name].get("risk_level") == "read"
        and provider.tool_overrides[name].get("requires_confirmation") is False
        for name in read_only_names
    )


def test_software_migration_provider_spec_is_exactly_pinned():
    lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "software-migration.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert lines == [
        "fastmcp==4.0.3",
        "mcp==2.2.0",
    ]


def test_pdf_provider_exposes_only_reviewed_watermark_capability():
    manifests = load_provider_manifests(PROJECT_ROOT)
    pdf = manifests["pdf"]

    assert pdf.mode == "auto"
    assert pdf.tool_allowlist == ("add_text_watermark_direct",)
    assert "pdf_mcp.server" in " ".join(pdf.args)

    override = pdf.tool_overrides["add_text_watermark_direct"]
    contract = override["artifact_contract"]

    assert override["risk_level"] == "write_local"
    assert override["requires_confirmation"] is True
    assert contract["transport"] == "local_path"
    assert contract["inputs"] == ["input_path"]
    assert contract["outputs"] == []
    assert contract["output_paths"] == {
        "output_path": {
            "filename": "watermarked.pdf",
            "mime_type": "application/pdf",
        }
    }

    policy = contract["policy"]
    assert policy["max_input_bytes"] == 64 * 1024 * 1024
    assert policy["max_output_bytes"] == 64 * 1024 * 1024
    assert policy["max_output_artifacts"] == 1
    assert policy["allowed_input_mime_types"] == ["application/pdf"]
    assert policy["allowed_output_mime_types"] == ["application/pdf"]


def test_pdf_provider_spec_is_exactly_pinned():
    lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "pdf.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert "pdf-mcp-server==0.1.2" in lines
    assert "fastmcp==4.0.3" in lines
    assert "mcp==2.2.0" in lines
    assert "pikepdf==10.13.0.post1" in lines
    assert "PyMuPDF==1.28.2" in lines
    assert all("==" in line for line in lines)