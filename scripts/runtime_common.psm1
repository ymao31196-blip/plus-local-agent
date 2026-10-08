# Shared environment and state handling for the src/ + scripts/ layout.
function Initialize-PlaPythonEnvironment([string]$ProjectRoot) {
    $srcRoot = Join-Path $ProjectRoot "src"
    $paths = @($srcRoot) + @($env:PYTHONPATH -split ';' | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and $_ -ne $srcRoot
    })
    $env:PYTHONPATH = $paths -join ';'
}

function Resolve-PlaTunnelConfig([string]$ProjectRoot) {
    $candidate = if ([string]::IsNullOrWhiteSpace($env:PLA_TUNNEL_CONFIG)) {
        Join-Path $ProjectRoot "config\tunnel.local.yaml"
    } elseif ([System.IO.Path]::IsPathRooted($env:PLA_TUNNEL_CONFIG)) {
        $env:PLA_TUNNEL_CONFIG
    } else {
        Join-Path $ProjectRoot $env:PLA_TUNNEL_CONFIG
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Customer Tunnel config not found: $candidate"
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Set-PlaStoppedStatus([string]$StatusPath) {
    if (-not (Test-Path -LiteralPath $StatusPath -PathType Leaf)) { return }
    $status = Get-Content -LiteralPath $StatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $status.state = "stopped"
    $status.pid = 0
    $status.updated_at = [DateTime]::UtcNow.ToString("o")
    foreach ($name in @("current_request_id", "current_launch_id")) {
        if ($status.PSObject.Properties.Name -contains $name) { $status.$name = $null }
    }
    $tempPath = "$StatusPath.tmp"
    $json = $status | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($tempPath, $json, (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $tempPath -Destination $StatusPath -Force
}

Export-ModuleMember -Function Initialize-PlaPythonEnvironment, Resolve-PlaTunnelConfig, Set-PlaStoppedStatus
