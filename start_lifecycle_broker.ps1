[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($env:PLA_PYTHON)) {
    $python = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"
} else {
    $python = $env:PLA_PYTHON
}
$broker = Join-Path $projectRoot "runtime_lifecycle_broker.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Conda environment interpreter not found: $python"
}
if (-not (Test-Path -LiteralPath $broker -PathType Leaf)) {
    throw "Runtime Lifecycle Broker not found: $broker"
}

Set-Location -LiteralPath $projectRoot
& $python $broker
exit $LASTEXITCODE
