[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$httpScript = Join-Path $projectRoot "start_http.ps1"
$tunnelScript = Join-Path $projectRoot "start_tunnel.ps1"
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
            throw "$Name port $Port is already in use by PID $pidValue, but it is not the expected PLA process. Refusing to reuse it.`nCommandLine: $commandLine"
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

if (-not (Test-Path -LiteralPath $httpScript -PathType Leaf)) {
    throw "Missing HTTP startup script: $httpScript"
}
if (-not (Test-Path -LiteralPath $tunnelScript -PathType Leaf)) {
    throw "Missing tunnel startup script: $tunnelScript"
}

$httpPid = Assert-OwnedListener 8766 @($projectRoot, "server.py") "PLA HTTP"
if ($null -eq $httpPid) {
    Write-Host "Starting PLA HTTP service..."
    Start-Process -FilePath $powershell `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $httpScript) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    $httpPid = Wait-Listener 8766 20
    $httpPid = Assert-OwnedListener 8766 @($projectRoot, "server.py") "PLA HTTP"
    Write-Host "PLA HTTP ready on 127.0.0.1:8766 (PID $httpPid)."
} else {
    Write-Host "PLA HTTP already running on 127.0.0.1:8766 (PID $httpPid)."
}

$tunnelConfig = Join-Path $projectRoot "config\tunnel.yaml"
$tunnelPid = Assert-OwnedListener 18081 @("tunnel-client", $tunnelConfig) "PLA tunnel"
if ($null -eq $tunnelPid) {
    Write-Host "Starting PLA tunnel..."
    Start-Process -FilePath $powershell `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tunnelScript) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    $tunnelPid = Wait-Listener 18081 30
    $tunnelPid = Assert-OwnedListener 18081 @("tunnel-client", $tunnelConfig) "PLA tunnel"
    Write-Host "PLA tunnel ready on health port 127.0.0.1:18081 (PID $tunnelPid)."
} else {
    Write-Host "PLA tunnel already running on health port 127.0.0.1:18081 (PID $tunnelPid)."
}

Write-Host "PLA READY"
