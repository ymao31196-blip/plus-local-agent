[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$config = Join-Path $projectRoot "config\tunnel.yaml"
$tunnelClient = "D:\AI_Tools\tunnel-client\tunnel-client.exe"
$credentialFile = "D:\AI_Tools\tunnel-client\secrets\control-plane-api-key.txt"
$credentialRef = "file:D:/AI_Tools/tunnel-client/secrets/control-plane-api-key.txt"
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

& $tunnelClient doctor --config $config --control-plane.api-key $credentialRef --explain
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client doctor failed"
}

& $tunnelClient run --config $config --control-plane.api-key $credentialRef
exit $LASTEXITCODE
