"""Acceptance harness safety and native stderr handling on Windows PowerShell 5.1."""
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] /
          "desktop/verification/installation.ps1").read_text(encoding="utf-8")


def test_native_stderr_logged_without_throwing_on_first_traceback_line():
    assert 'function Invoke-LoggedNative(' in SCRIPT
    assert '$ErrorActionPreference = "Continue"' in SCRIPT
    assert '$ErrorActionPreference = $previousPreference' in SCRIPT
    assert '. $Command' in SCRIPT
    assert '$exitCode = $LASTEXITCODE' in SCRIPT
    assert 'Out-File -LiteralPath $mcpLog -Encoding utf8 -ErrorAction Stop' in SCRIPT
    assert 'Out-File -LiteralPath $nativeLog -Encoding utf8 -ErrorAction Stop' in SCRIPT
    assert 'Invoke-LoggedNative "Installed MCP acceptance"' in SCRIPT
    assert 'Invoke-LoggedNative "Installed native UI acceptance"' in SCRIPT
    assert ' --browser *>' not in SCRIPT
    assert ' --output $evidence *>' not in SCRIPT


def test_packaged_mcp_uses_known_build_interpreter_not_conda_base():
    assert 'param([Parameter(Mandatory)][string]$Installer, [string]$Python = "", [switch]$AllowSameMachine)' in SCRIPT
    assert 'venv\\Scripts\\python.exe' in SCRIPT
    assert '$verificationPython = if ($Python)' in SCRIPT
    assert 'Test-Path -LiteralPath $verificationPython -PathType Leaf' in SCRIPT
    assert '& $verificationPython (Join-Path $PSScriptRoot "packaged_mcp.py")' in SCRIPT
    assert '& $Python (Join-Path $PSScriptRoot "packaged_mcp.py")' not in SCRIPT


def test_unique_evidence_run_never_mistakes_old_report_for_current_result():
    assert '$runId = [guid]::NewGuid().ToString("N")' in SCRIPT
    assert '$evidence = Join-Path $buildRoot (Join-Path "installation-evidence" $runId)' in SCRIPT
    assert 'installed-mcp-report.json' in SCRIPT
    assert 'installation-report.json' in SCRIPT
    assert 'Installation test evidence: $evidence' in SCRIPT


def test_same_machine_mode_does_not_write_into_existing_user_data():
    assert '$defaultData = Join-Path $env:LOCALAPPDATA "io.pla.desktop"' in SCRIPT
    assert '$hadDefaultData = Test-Path -LiteralPath $defaultData' in SCRIPT
    assert 'if ($hadDefaultData -and -not $AllowSameMachine)' in SCRIPT
    assert 'Existing PLA Desktop private data detected' in SCRIPT
    guard = SCRIPT.index('if ($hadDefaultData -and -not $AllowSameMachine)')
    install = SCRIPT.index('$setup = Start-Process -FilePath')
    marker = SCRIPT.index('[IO.File]::WriteAllText($marker')
    assert guard < install < marker
    assert 'if (-not $hadDefaultData)' in SCRIPT
    assert '$marker = if ($hadDefaultData) { $null }' in SCRIPT
    assert 'Existing PLA Desktop private data directory retained' in SCRIPT
    assert 'An existing PLA Desktop installation was detected' in SCRIPT


def test_failure_path_still_uninstalls_test_program_and_records_report():
    assert 'finally {' in SCRIPT
    assert 'Start-Process -FilePath $uninstaller' in SCRIPT
    assert 'Uninstall preserves default private data marker' in SCRIPT
    assert 'Current-user NSIS installation' not in SCRIPT or         'Actual current-user NSIS installation and payload presence' in SCRIPT
