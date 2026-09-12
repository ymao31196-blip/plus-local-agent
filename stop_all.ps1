[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$tunnelConfig = Join-Path $projectRoot "config\tunnel.yaml"
$brokerStopScript = Join-Path $projectRoot "stop_elevation_broker.ps1"

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

function Stop-OwnedListener([int]$Port, [string[]]$RequiredFragments, [string]$Name) {
    $pidValue = Get-ListenerPid $Port
    if ($null -eq $pidValue) {
        Write-Host "$Name is not running on port $Port."
        return
    }
    $commandLine = Get-ProcessCommandLine $pidValue
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        throw "$Name port $Port belongs to PID $pidValue, but its command line could not be verified. Refusing to stop it."
    }
    foreach ($fragment in $RequiredFragments) {
        if (-not $commandLine.Contains($fragment)) {
            throw ("$Name port $Port belongs to PID $pidValue, but it is not the expected PLA process. Refusing to stop it. CommandLine: " + $commandLine)
        }
    }
    Stop-Process -Id $pidValue -Force
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ($null -eq (Get-ListenerPid $Port)) {
            Write-Host "$Name stopped (PID $pidValue)."
            return
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name PID $pidValue was stopped, but port $Port is still listening."
}

# Stop ingress first so no new remote work is forwarded while local services shut down.
Stop-OwnedListener 18081 @("tunnel-client", $tunnelConfig) "PLA tunnel"
Stop-OwnedListener 8766 @($projectRoot, "server.py") "PLA HTTP"

if (Test-Path -LiteralPath $brokerStopScript -PathType Leaf) {
    & $brokerStopScript
} else {
    Write-Warning "Interactive Elevation Broker stop script is missing: $brokerStopScript"
}

Write-Host "PLA STOPPED"
