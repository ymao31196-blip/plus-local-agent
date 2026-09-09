[CmdletBinding()]
param(
    [switch]$DebugE2E
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Conda environment interpreter not found: $python"
}

& $python -c "import fastmcp, mcp_types; assert fastmcp.__version__ == '4.0.3'"
if ($LASTEXITCODE -ne 0) {
    throw "plus-local-agent environment validation failed"
}

if ($DebugE2E) {
    $env:CHATGPT_E2E_DEBUG = "1"
}

Set-Location -LiteralPath $projectRoot
& $python (Join-Path $projectRoot "server.py") --http --host 127.0.0.1 --port 8766 --path /mcp
exit $LASTEXITCODE
