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
& $python -c "import json; from browser_runtime import stop_browser_runtime; print(json.dumps(stop_browser_runtime(), ensure_ascii=False))"
if ($LASTEXITCODE -ne 0) {
    throw "PLA Browser Runtime failed to stop"
}
