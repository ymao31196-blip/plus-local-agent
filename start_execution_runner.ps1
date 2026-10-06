[CmdletBinding()]
param()

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
$code = "import json; from execution_runner_runtime import start_execution_runner; print(json.dumps(start_execution_runner()))"
$output = & $python -c $code
if ($LASTEXITCODE -ne 0) {
    throw "PLA Execution Runner failed to start."
}
$status = $output | ConvertFrom-Json
if (-not $status.running -or -not $status.production_ready) {
    throw ("PLA Execution Runner did not become production-ready: " + ($output -join "`n"))
}
Write-Host ("PLA Execution Runner ready (PID {0}, instance {1})." -f $status.runner.process_id, $status.runner.runner_instance_id)
