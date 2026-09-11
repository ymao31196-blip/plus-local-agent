[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $projectRoot
$config = Join-Path $projectRoot "config\tunnel.yaml"
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

if ($configText.Contains("REPLACE_WITH_PLUS_LOCAL_AGENT_TUNNEL_ID")) {
    throw "Create a distinct plus-local-agent tunnel and replace the tunnel_id placeholder in config\tunnel.yaml."
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
