[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$httpScript = Join-Path $projectRoot "start_http.ps1"
$tunnelScript = Join-Path $projectRoot "start_tunnel.ps1"
$brokerScript = Join-Path $projectRoot "start_elevation_broker.ps1"
$brokerStatusPath = Join-Path $projectRoot "state\elevation\broker_status.json"
$lifecycleScript = Join-Path $projectRoot "start_lifecycle_broker.ps1"
$lifecycleStatusPath = Join-Path $projectRoot "state\lifecycle\broker_status.json"
$browserScript = Join-Path $projectRoot "start_browser_runtime.ps1"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

function Get-ListenerPid([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection) {
        return $null
    }
    return [int]$connection.OwningProcess
}

function Get-ProcessCommandLine([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    return [string]$process.CommandLine
}

function Assert-OwnedListener([int]$Port, [string[]]$RequiredFragments, [string]$Name) {
    $pidValue = Get-ListenerPid $Port
    if ($null -eq $pidValue) {
        return $null
    }
    $commandLine = Get-ProcessCommandLine $pidValue
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        throw "$Name port $Port is already in use by PID $pidValue, but its command line could not be verified. Refusing to reuse it."
    }
    foreach ($fragment in $RequiredFragments) {
        if (-not $commandLine.Contains($fragment)) {
            throw ("$Name port $Port is already in use by PID $pidValue, but it is not the expected PLA process. Refusing to reuse it. CommandLine: " + $commandLine)
        }
    }
    return $pidValue
}

function Wait-Listener([int]$Port, [int]$TimeoutSeconds = 20) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $pidValue = Get-ListenerPid $Port
        if ($null -ne $pidValue) {
            return $pidValue
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for TCP listener on 127.0.0.1:$Port."
}

function Wait-BrokerReady([int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path -LiteralPath $brokerStatusPath -PathType Leaf) {
            try {
                $status = Get-Content -LiteralPath $brokerStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $brokerPid = [int]$status.pid
                if ($status.state -eq "running" -and $brokerPid -gt 0) {
                    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $brokerPid" -ErrorAction SilentlyContinue
                    if ($null -ne $process) {
                        $commandLine = [string]$process.CommandLine
                        if ($commandLine.Contains($projectRoot) -and $commandLine.Contains("interactive_elevation_broker.py")) {
                            return $brokerPid
                        }
                    }
                }
            } catch {
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for Interactive Elevation Broker."
}

function Wait-LifecycleBrokerReady([int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path -LiteralPath $lifecycleStatusPath -PathType Leaf) {
            try {
                $status = Get-Content -LiteralPath $lifecycleStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $lifecyclePid = [int]$status.pid
                if ($status.state -eq "running" -and $lifecyclePid -gt 0) {
                    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $lifecyclePid" -ErrorAction SilentlyContinue
                    if ($null -ne $process) {
                        $commandLine = [string]$process.CommandLine
                        if ($commandLine.Contains($projectRoot) -and $commandLine.Contains("runtime_lifecycle_broker.py")) {
                            return $lifecyclePid
                        }
                    }
                }
            } catch {
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for Runtime Lifecycle Broker."
}

foreach ($required in @($httpScript, $tunnelScript, $brokerScript, $lifecycleScript, $browserScript)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing startup script: $required"
    }
}

Write-Host "Ensuring Interactive Elevation Broker..."
$brokerStartArgs = @{
    FilePath = $powershell
    ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $brokerScript)
    WorkingDirectory = $projectRoot
    WindowStyle = "Hidden"
}
Start-Process @brokerStartArgs | Out-Null
$brokerPid = Wait-BrokerReady 10
Write-Host "Interactive Elevation Broker ready (PID $brokerPid)."

Write-Host "Ensuring Runtime Lifecycle Broker..."
$lifecycleStartArgs = @{
    FilePath = $powershell
    ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $lifecycleScript)
    WorkingDirectory = $projectRoot
    WindowStyle = "Hidden"
}
Start-Process @lifecycleStartArgs | Out-Null
$lifecyclePid = Wait-LifecycleBrokerReady 10
Write-Host "Runtime Lifecycle Broker ready (PID $lifecyclePid)."

Write-Host "Ensuring Browser Runtime..."
$browserStartArgs = @{
    FilePath = $powershell
    ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $browserScript)
    WorkingDirectory = $projectRoot
    WindowStyle = "Hidden"
}
Start-Process @browserStartArgs | Out-Null
$browserPid = Wait-Listener 8931 30
$browserPid = Assert-OwnedListener 8931 @($projectRoot, "cli.js") "PLA Browser Runtime"
Write-Host "PLA Browser Runtime ready on 127.0.0.1:8931 (PID $browserPid)."

$httpPid = Assert-OwnedListener 8766 @($projectRoot, "server.py") "PLA HTTP"
if ($null -eq $httpPid) {
    Write-Host "Starting PLA HTTP service..."
    $httpStartArgs = @{
        FilePath = $powershell
        ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $httpScript)
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
    }
    Start-Process @httpStartArgs | Out-Null
    $httpPid = Wait-Listener 8766 20
    $httpPid = Assert-OwnedListener 8766 @($projectRoot, "server.py") "PLA HTTP"
    Write-Host "PLA HTTP ready on 127.0.0.1:8766 (PID $httpPid)."
} else {
    Write-Host "PLA HTTP already running on 127.0.0.1:8766 (PID $httpPid)."
}

$defaultLocalTunnelConfig = Join-Path $projectRoot "config\tunnel.local.yaml"
$tunnelConfig = if ([string]::IsNullOrWhiteSpace($env:PLA_TUNNEL_CONFIG)) {
    if (Test-Path -LiteralPath $defaultLocalTunnelConfig -PathType Leaf) {
        $defaultLocalTunnelConfig
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
if (-not (Test-Path -LiteralPath $tunnelConfig -PathType Leaf)) {
    throw "Tunnel config not found: $tunnelConfig"
}
$tunnelConfig = (Resolve-Path -LiteralPath $tunnelConfig).Path
$tunnelPid = Assert-OwnedListener 18081 @("tunnel-client", $tunnelConfig) "PLA tunnel"
if ($null -eq $tunnelPid) {
    Write-Host "Starting PLA tunnel..."
    $tunnelStartArgs = @{
        FilePath = $powershell
        ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tunnelScript)
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
    }
    Start-Process @tunnelStartArgs | Out-Null
    $tunnelPid = Wait-Listener 18081 30
    $tunnelPid = Assert-OwnedListener 18081 @("tunnel-client", $tunnelConfig) "PLA tunnel"
    Write-Host "PLA tunnel ready on health port 127.0.0.1:18081 (PID $tunnelPid)."
} else {
    Write-Host "PLA tunnel already running on health port 127.0.0.1:18081 (PID $tunnelPid)."
}

Write-Host "PLA READY"
