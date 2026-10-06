[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($env:PLA_PYTHON)) {
    $python = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"
} else {
    $python = $env:PLA_PYTHON
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Conda environment interpreter not found: $python"
}

Set-Location -LiteralPath $projectRoot
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
do {
    $code = "import json; from execution_runner_runtime import stop_execution_runner; print(json.dumps(stop_execution_runner()))"
    $output = & $python -c $code
    if ($LASTEXITCODE -ne 0) {
        throw "PLA Execution Runner stop request failed."
    }
    $status = $output | ConvertFrom-Json
    if ($status.state -eq "stopped" -or $status.status -in @("stopped", "already_stopped", "stale_state_cleaned")) {
        Write-Host "PLA Execution Runner stopped."
        exit 0
    }
    if ($status.state -eq "stale_live" -or $status.recovery_required) {
        throw ("PLA Execution Runner is alive but unreachable; refusing to force-stop it. Status: " + ($output -join "`n"))
    }
    Start-Sleep -Milliseconds 500
} while ([DateTime]::UtcNow -lt $deadline)

throw "Timed out waiting for PLA Execution Runner to stop cleanly."
