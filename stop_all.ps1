[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $projectRoot "scripts\runtime_common.psm1") -Force
$tunnelConfig = Resolve-PlaTunnelConfig $projectRoot
$brokerStopScript = Join-Path $projectRoot "scripts\stop_elevation_broker.ps1"
$lifecycleStopScript = Join-Path $projectRoot "scripts\stop_lifecycle_broker.ps1"
$browserStopScript = Join-Path $projectRoot "scripts\stop_browser_runtime.ps1"
$runnerStopScript = Join-Path $projectRoot "scripts\stop_execution_runner.ps1"

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
    # HTTP owns provider subprocesses; stop the verified owner's entire tree.
    & "$env:SystemRoot\System32\taskkill.exe" /PID $pidValue /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
        throw "$Name process tree could not be stopped (PID $pidValue)."
    }
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

# Stop the restart authority before stopping HTTP so a queued lifecycle request
# cannot bring the service back during shutdown.
if (Test-Path -LiteralPath $lifecycleStopScript -PathType Leaf) {
    & $lifecycleStopScript
} else {
    Write-Warning "Runtime Lifecycle Broker stop script is missing: $lifecycleStopScript"
}

if (Test-Path -LiteralPath $runnerStopScript -PathType Leaf) {
    & $runnerStopScript
} else {
    Write-Warning "Execution Runner stop script is missing: $runnerStopScript"
}

Stop-OwnedListener 8766 @((Join-Path $projectRoot "src\server.py")) "PLA HTTP"
Set-PlaStoppedStatus (Join-Path $projectRoot "state\lifecycle\http_runtime_status.json")

if (Test-Path -LiteralPath $browserStopScript -PathType Leaf) {
    & $browserStopScript
} else {
    Write-Warning "Browser Runtime stop script is missing: $browserStopScript"
}

if (Test-Path -LiteralPath $brokerStopScript -PathType Leaf) {
    & $brokerStopScript
} else {
    Write-Warning "Interactive Elevation Broker stop script is missing: $brokerStopScript"
}

Write-Host "PLA STOPPED"
