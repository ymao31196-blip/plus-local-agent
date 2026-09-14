[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $projectRoot
$defaultLocalConfig = Join-Path $projectRoot "config\tunnel.local.yaml"
$config = if ([string]::IsNullOrWhiteSpace($env:PLA_TUNNEL_CONFIG)) {
    if (Test-Path -LiteralPath $defaultLocalConfig -PathType Leaf) {
        $defaultLocalConfig
    } else {
        throw "Customer Tunnel config is missing. Run install.ps1 with customer Tunnel settings or set PLA_TUNNEL_CONFIG explicitly."
    }
} else {
    $candidate = $env:PLA_TUNNEL_CONFIG
    if ([System.IO.Path]::IsPathRooted($candidate)) {
        $candidate
    } else {
        Join-Path $projectRoot $candidate
    }
}
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    throw "Tunnel config not found: $config"
}
$config = (Resolve-Path -LiteralPath $config).Path
$tunnelClient = if ([string]::IsNullOrWhiteSpace($env:PLA_TUNNEL_CLIENT)) {
    Join-Path $toolsRoot "tunnel-client\tunnel-client.exe"
} else {
    $env:PLA_TUNNEL_CLIENT
}
$credentialFile = if ([string]::IsNullOrWhiteSpace($env:PLA_TUNNEL_CREDENTIAL)) {
    Join-Path $toolsRoot "tunnel-client\secrets\control-plane-api-key.txt"
} else {
    $env:PLA_TUNNEL_CREDENTIAL
}
$configText = Get-Content -LiteralPath $config -Raw

if ($configText.Contains("REPLACE_WITH_PLUS_LOCAL_AGENT_TUNNEL_ID") -or
    $configText.Contains("REPLACE_WITH_CUSTOMER_TUNNEL_ID")) {
    throw "Create a distinct customer Secure MCP Tunnel and configure its tunnel_id before starting PLA."
}
if (-not (Test-Path -LiteralPath $tunnelClient -PathType Leaf)) {
    throw "tunnel-client not found: $tunnelClient"
}
if (-not (Test-Path -LiteralPath $credentialFile -PathType Leaf) -or
    (Get-Item -LiteralPath $credentialFile).Length -eq 0) {
    throw "Tunnel runtime credential file is missing or empty: $credentialFile"
}
$credentialPath = (Resolve-Path -LiteralPath $credentialFile).Path -replace '\\', '/'
$credentialRef = "file:$credentialPath"

& $tunnelClient doctor --config $config --control-plane.api-key $credentialRef --explain
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client doctor failed"
}

& $tunnelClient run --config $config --control-plane.api-key $credentialRef
exit $LASTEXITCODE
