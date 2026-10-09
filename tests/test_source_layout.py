from pathlib import Path
import json
import os
import re
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROOT_ENTRYPOINTS = {
    "install.ps1",
    "restart_pla.ps1",
    "setup_providers.ps1",
    "setup_skill_library.ps1",
    "start_all.ps1",
    "stop_all.ps1",
}

RUNTIME_HELPERS = {
    "start_browser_runtime.ps1",
    "start_elevation_broker.ps1",
    "start_execution_runner.ps1",
    "start_http.ps1",
    "start_lifecycle_broker.ps1",
    "start_tunnel.ps1",
    "stop_browser_runtime.ps1",
    "stop_elevation_broker.ps1",
    "stop_execution_runner.ps1",
    "stop_lifecycle_broker.ps1",
}

PACKAGE_MODULES = {
    "execution": {
        "command_semantics.py",
        "conpty_backend.py",
        "execution_backend.py",
        "execution_program_policy.py",
        "execution_runner_capabilities.py",
        "execution_runner_runtime.py",
        "execution_runner_service.py",
        "execution_runtime.py",
        "process_controller.py",
        "promotion_execution_policy.py",
        "runner_process_identity.py",
        "runner_transport.py",
        "semantic_execution_policy.py",
        "shadow_execution_policy.py",
    },
    "capabilities": {
        "browser_runtime_capabilities.py",
        "capability_broker.py",
        "capability_models.py",
        "capability_registry.py",
        "core_capabilities.py",
        "external_process_capabilities.py",
        "local_mutation_capabilities.py",
        "observer_runtime_capabilities.py",
        "provider_runtime_capabilities.py",
        "runtime_lifecycle_capabilities.py",
        "session_runtime_capabilities.py",
        "windows_action_capabilities.py",
    },
    "routing": {
        "capability_steering.py",
        "declared_routing.py",
        "routing_audit.py",
    },
    "runtime": {
        "event_runtime.py",
        "runtime_context.py",
        "runtime_lifecycle.py",
        "runtime_lifecycle_broker.py",
        "runtime_parity.py",
        "session_runtime.py",
        "task_store.py",
    },
    "hooks": {
        "external_observer_runtime.py",
        "gate_hook_runtime.py",
        "observer_hook_runtime.py",
        "observer_plugin_manifest.py",
    },
    "transactions": {
        "transaction_action_envelope.py",
        "transaction_runtime.py",
    },
    "project": {
        "acceptance_contract.py",
        "changeset_manager.py",
        "independent_verifier.py",
        "project_state.py",
        "verification_spec.py",
    },
    "artifacts": {
        "artifact_bridge.py",
        "artifact_policy.py",
        "artifact_runtime.py",
    },
    "browser": {
        "browser_download.py",
        "browser_runtime.py",
        "browser_session_keeper.py",
    },
    "agent": {
        "agent_loop.py",
        "agent_service.py",
    },
    "reasoning": {
        "decision_parser.py",
        "fake_model_backend.py",
        "generic_llm_reasoner.py",
        "llm_interface.py",
        "llm_reasoner.py",
        "model_backend.py",
        "prompt_builder.py",
        "reasoner.py",
        "rule_reasoner.py",
    },
    "provider": {
        "external_provider_runtime.py",
        "fake_capability_provider.py",
        "provider_doctor.py",
        "provider_manifest.py",
        "provider_setup_runtime.py",
        "setup_source_provider.py",
        "source_provider_setup.py",
    },
    "host": {
        "computer_use_indicator.py",
        "interactive_elevation_broker.py",
        "workspace_manager.py",
    },
    "tooling": {
        "internal_tool_executor.py",
        "local_tools.py",
        "tool_registry.py",
        "tool_schema.py",
    },
    "mcp_runtime": {
        "mcp_client_manager.py",
    },
    "diagnostics": {
        "e2e_debug.py",
    },
}


def test_repository_root_has_no_python_implementation_modules():
    assert list(PROJECT_ROOT.glob("*.py")) == []


def test_repository_root_keeps_only_reviewed_powershell_entrypoints():
    assert {path.name for path in PROJECT_ROOT.glob("*.ps1")} == ROOT_ENTRYPOINTS


def test_runtime_helper_scripts_live_under_scripts_directory():
    scripts_dir = PROJECT_ROOT / "scripts"
    assert {path.name for path in scripts_dir.glob("*.ps1")} == RUNTIME_HELPERS


def test_server_is_the_only_python_module_left_at_src_top_level():
    assert {path.name for path in (PROJECT_ROOT / "src").glob("*.py")} == {"server.py"}


def test_domain_packages_are_real_and_do_not_leak_back_to_src_root():
    source_root = PROJECT_ROOT / "src"
    for package, modules in PACKAGE_MODULES.items():
        package_dir = source_root / package
        assert (package_dir / "__init__.py").is_file()
        assert {path.name for path in package_dir.glob("*.py")} == modules | {"__init__.py"}
        for module in modules:
            assert not (source_root / module).exists()


def test_lifecycle_broker_helper_points_to_runtime_package():
    script = (PROJECT_ROOT / "scripts" / "start_lifecycle_broker.ps1").read_text(
        encoding="utf-8"
    )
    assert "src\\runtime\\runtime_lifecycle_broker.py" in script
    assert "src\\runtime_lifecycle_broker.py" not in script


def test_browser_helpers_import_browser_package():
    for name in ("start_browser_runtime.ps1", "stop_browser_runtime.ps1"):
        script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "from browser.browser_runtime import" in script
        assert "from browser_runtime import" not in script


def test_agent_cli_points_to_src_server_entrypoint():
    agent_loop = (PROJECT_ROOT / "src" / "agent" / "agent_loop.py").read_text(
        encoding="utf-8"
    )
    assert 'SOURCE_ROOT / "server.py"' in agent_loop
    assert 'PROJECT_ROOT / "server.py"' not in agent_loop


def test_provider_setup_helper_uses_scoped_module_entrypoint():
    script = (PROJECT_ROOT / "setup_providers.ps1").read_text(encoding="utf-8")
    assert '"-m", "provider.setup_source_provider"' in script
    assert '$previousPythonPath = $env:PYTHONPATH' in script
    assert '$env:PYTHONPATH = Join-Path $projectRoot "src"' in script
    assert '$env:PYTHONPATH = $previousPythonPath' in script
    assert 'src\\setup_source_provider.py' not in script


def test_elevation_helper_points_to_host_package():
    script = (PROJECT_ROOT / "scripts" / "start_elevation_broker.ps1").read_text(
        encoding="utf-8"
    )
    assert "src\\host\\interactive_elevation_broker.py" in script
    assert 'src\\interactive_elevation_broker.py' not in script


def test_computer_use_indicator_relaunches_through_host_package():
    indicator = (
        PROJECT_ROOT / "src" / "host" / "computer_use_indicator.py"
    ).read_text(encoding="utf-8")
    assert "str(Path(__file__).resolve())" in indicator
    assert '"host.computer_use_indicator"' not in indicator


def test_local_source_does_not_shadow_third_party_mcp_package():
    assert not (PROJECT_ROOT / "src" / "mcp").exists()
    assert (PROJECT_ROOT / "src" / "mcp_runtime" / "mcp_client_manager.py").is_file()


def test_source_layout_document_exists():
    assert (PROJECT_ROOT / "docs" / "source_layout.md").is_file()


PYTHON_HELPERS = {
    "start_browser_runtime.ps1",
    "stop_browser_runtime.ps1",
    "start_execution_runner.ps1",
    "stop_execution_runner.ps1",
    "start_elevation_broker.ps1",
    "start_lifecycle_broker.ps1",
    "start_http.ps1",
}


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(code, cwd, env=None):
    if sys.platform != "win32":
        pytest.skip("Windows PowerShell integration")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", code],
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=True,
    ).stdout.strip()


@pytest.mark.parametrize("name", sorted(RUNTIME_HELPERS))
def test_runtime_helpers_resolve_project_root_independently_of_cwd(name, tmp_path):
    path = PROJECT_ROOT / "scripts" / name
    text = path.read_text(encoding="utf-8")
    expression = re.search(r"(?m)^\$projectRoot = (.+)$", text).group(1)
    output = _powershell(
        f"$PSScriptRoot = {_ps_literal(path.parent)}; $projectRoot = {expression}; $projectRoot",
        tmp_path,
    )
    assert Path(output) == PROJECT_ROOT


@pytest.mark.parametrize("name", sorted(PYTHON_HELPERS))
def test_python_helpers_initialize_shared_source_environment(name):
    text = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert 'Import-Module (Join-Path $PSScriptRoot "runtime_common.psm1") -Force' in text
    assert "Initialize-PlaPythonEnvironment $projectRoot" in text
    assert text.index("Initialize-PlaPythonEnvironment") < text.index("& $python")


def test_shared_environment_imports_all_runtime_packages_from_foreign_cwd(tmp_path):
    module = PROJECT_ROOT / "scripts" / "runtime_common.psm1"
    env = os.environ.copy()
    extra = str(tmp_path / "extra-pythonpath")
    env["PYTHONPATH"] = extra
    code = (
        "import json,sys; from host import interactive_elevation_broker as e; "
        "from runtime import runtime_lifecycle_broker as l; "
        "from execution import execution_runner_runtime as r; "
        "from browser import browser_runtime as b; import server; "
        "print(json.dumps([e.__file__,l.__file__,r.__file__,b.__file__,server.__file__,sys.path]))"
    )
    output = _powershell(
        f"Import-Module {_ps_literal(module)}; "
        f"Initialize-PlaPythonEnvironment {_ps_literal(PROJECT_ROOT)}; "
        f"Initialize-PlaPythonEnvironment {_ps_literal(PROJECT_ROOT)}; "
        f"& {_ps_literal(sys.executable)} -c {_ps_literal(code)}; "
        "if ($LASTEXITCODE -ne 0) { throw 'runtime imports failed' }",
        tmp_path, env,
    )
    payload = json.loads(output)
    assert all(Path(path).is_relative_to(PROJECT_ROOT / "src") for path in payload[:5])
    assert payload[5].count(str(PROJECT_ROOT / "src")) == 1
    assert extra in payload[5]


@pytest.mark.parametrize("name", ["start_all.ps1", "stop_all.ps1", "restart_pla.ps1"])
def test_orchestrators_reference_existing_scripts_and_current_python_entries(name):
    text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    references = re.findall(r'Join-Path \$projectRoot "([^"\n]+\.(?:ps1|psm1|py))"', text)
    assert references
    for reference in references:
        assert (PROJECT_ROOT / reference.replace("\\", "/")).is_file(), reference
    assert "src\\server.py" in text
    assert '@($projectRoot, "server.py")' not in text


def test_python_entry_paths_exist_and_old_root_entries_are_not_dependencies():
    for path in (PROJECT_ROOT / "scripts").glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        for reference in re.findall(r'Join-Path \$projectRoot "([^"\n]+\.py)"', text):
            assert reference.startswith("src\\")
            assert (PROJECT_ROOT / reference.replace("\\", "/")).is_file()
    from browser import browser_runtime
    from execution import runner_transport
    from runtime import runtime_lifecycle_broker
    assert browser_runtime.KEEPER_SCRIPT == PROJECT_ROOT / "src/browser/browser_session_keeper.py"
    assert browser_runtime.KEEPER_SCRIPT.is_file()
    assert Path(runner_transport.__file__).with_name("execution_runner_service.py").is_file()
    assert runtime_lifecycle_broker.RESTART_SCRIPT == PROJECT_ROOT / "restart_pla.ps1"


def test_browser_persistent_paths_stay_at_project_root():
    from browser import browser_runtime as runtime
    from browser import browser_session_keeper as keeper
    assert runtime.PROJECT_ROOT == PROJECT_ROOT
    assert runtime.STATE_DIR == PROJECT_ROOT / "state/browser_runtime"
    assert runtime.PROFILE_DIR == PROJECT_ROOT / ".browser_profiles/v13-default"
    assert runtime.OUTPUT_DIR == PROJECT_ROOT / "state/browser"
    assert keeper.PROJECT_ROOT == PROJECT_ROOT
    assert keeper.STATE_DIR == runtime.STATE_DIR


def test_start_and_stop_resolve_same_tunnel_config_with_relative_override(tmp_path):
    for name in ("start_all.ps1", "stop_all.ps1", "scripts/start_tunnel.ps1"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "Resolve-PlaTunnelConfig $projectRoot" in text
        assert "config\\tunnel.yaml" not in text
    config = tmp_path / "config/tunnel.local.yaml"
    config.parent.mkdir()
    config.write_text("test fixture", encoding="utf-8")
    module = PROJECT_ROOT / "scripts/runtime_common.psm1"
    output = _powershell(
        f"Import-Module {_ps_literal(module)}; "
        "$env:PLA_TUNNEL_CONFIG = ''; "
        f"Resolve-PlaTunnelConfig {_ps_literal(tmp_path)}; "
        "$env:PLA_TUNNEL_CONFIG = 'config/tunnel.local.yaml'; "
        f"Resolve-PlaTunnelConfig {_ps_literal(tmp_path)}",
        PROJECT_ROOT,
    )
    assert [Path(line) for line in output.splitlines()] == [config, config]


def test_listener_identity_rejects_old_flat_entrypoint(tmp_path):
    script = PROJECT_ROOT / "start_all.ps1"
    code = (
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({_ps_literal(script)}, [ref]$null, [ref]$null); "
        "$fn = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-OwnedListener'}, $true); "
        "Invoke-Expression $fn.Extent.Text; "
        "function Get-ListenerPid { return 123 }; "
        "function Get-ProcessCommandLine { return $script:commandLine }; "
        f"$entry = {_ps_literal(PROJECT_ROOT / 'src/server.py')}; "
        "$script:commandLine = 'python.exe ' + $entry; "
        "if ((Assert-OwnedListener 8766 @($entry) 'PLA HTTP') -ne 123) { throw 'new entry rejected' }; "
        f"$script:commandLine = 'python.exe ' + {_ps_literal(PROJECT_ROOT / 'server.py')}; "
        "$rejected = $false; try { Assert-OwnedListener 8766 @($entry) 'PLA HTTP' } catch { $rejected = $true }; "
        "if (-not $rejected) { throw 'old flat entry accepted' }; 'verified'"
    )
    assert _powershell(code, tmp_path) == "verified"


def test_force_stopped_status_clears_live_pid_and_active_request(tmp_path):
    status = tmp_path / "broker_status.json"
    status.write_text(json.dumps({"state": "running", "pid": 123, "updated_at": "old", "current_request_id": "request"}), encoding="utf-8")
    module = PROJECT_ROOT / "scripts/runtime_common.psm1"
    _powershell(f"Import-Module {_ps_literal(module)}; Set-PlaStoppedStatus {_ps_literal(status)}", tmp_path)
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "stopped"
    assert payload["pid"] == 0
    assert payload["current_request_id"] is None
    assert payload["updated_at"] != "old"


def test_all_runtime_powershell_files_parse():
    paths = [PROJECT_ROOT / name for name in ROOT_ENTRYPOINTS]
    paths += list((PROJECT_ROOT / "scripts").glob("*.ps*1"))
    for path in paths:
        _powershell(
            "$errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile({_ps_literal(path)}, [ref]$null, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { throw ($errors | Out-String) }",
            PROJECT_ROOT,
        )
