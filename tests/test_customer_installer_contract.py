from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_customer_installer_contract_files_exist():
    assert (PROJECT_ROOT / "install.ps1").is_file()
    assert (PROJECT_ROOT / "config" / "tunnel.example.yaml").is_file()
    assert (PROJECT_ROOT / "docs" / "customer_installation.md").is_file()


def test_customer_installer_uses_repo_local_python_and_reviewed_setup():
    script = _read("install.ps1")
    assert 'Join-Path $projectRoot ".venv"' in script
    assert "Python 3.11" in script
    assert "requirements-core.txt" in script
    assert "requirements-documents.txt" in script
    assert "requirements-dev.txt" in script
    assert "setup_providers.ps1" in script
    assert "Microsoft Edge" in script
    assert "WindowsPackageManagerMCPServer.exe" in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "$previousErrorActionPreference" in script


def test_installer_native_subprocesses_are_exit_code_checked():
    script = _read("install.ps1")

    assert "function Invoke-Checked" in script
    assert "& $FilePath @Arguments 2>&1" in script
    assert 'Invoke-Checked $powershell @(' in script
    assert "& $powershell -NoProfile -ExecutionPolicy Bypass -File $setupProviders" not in script


def test_customer_tunnel_is_machine_local_and_secret_free():
    script = _read("install.ps1")
    example = _read("config/tunnel.example.yaml")
    gitignore = _read(".gitignore")

    assert "config/tunnel.local.yaml" in gitignore
    assert "tunnel.local.yaml" in script
    assert "REPLACE_WITH_CUSTOMER_TUNNEL_ID" in example
    assert "control-plane-api-key" not in example
    assert "api_key:" not in example
    assert "D:/AI_Tools" not in example
    assert "D:\\AI_Tools" not in example


def test_tunnel_startup_prefers_local_or_explicit_config():
    start_tunnel = _read("start_tunnel.ps1")
    start_all = _read("start_all.ps1")

    for script in (start_tunnel, start_all):
        assert "PLA_TUNNEL_CONFIG" in script
        assert "tunnel.local.yaml" in script
        assert "Customer Tunnel config is missing" in script
        assert "config\\tunnel.yaml" not in script


def test_installer_never_embeds_customer_secret_material():
    script = _read("install.ps1")

    assert "PLA_TUNNEL_CREDENTIAL" in script
    assert "control-plane.api-key" in script
    assert "api_key:" not in script
    assert "credentialRef" in script
    assert "file:$credentialPath" in script


def test_customer_installation_doc_routes_codex_through_installer():
    doc = _read("docs/customer_installation.md")

    assert "Codex task" in doc
    assert "install.ps1" in doc
    assert "Never reuse another machine Tunnel ID or credential" in doc
    assert "PARTIAL" in doc
    assert "PASS" in doc


def test_default_workspace_is_repository_local_and_portable():
    local_tools = _read("local_tools.py")

    assert 'str(PLA_ROOT / "workspace")' in local_tools
    assert r'D:\\AI_Tools\\plus-local-agent\\workspace' not in local_tools
    assert r'D:\AI_Tools\plus-local-agent\workspace' not in local_tools
