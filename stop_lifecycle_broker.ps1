[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$statusPath = Join-Path $projectRoot "state\lifecycle\broker_status.json"

if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
    Write-Host "Runtime Lifecycle Broker status file is absent."
    exit 0
}

try {
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw "Runtime Lifecycle Broker status file is invalid: $statusPath"
}

$brokerPid = [int]$status.pid
if ($brokerPid -le 0) {
    Write-Host "Runtime Lifecycle Broker is not running."
    exit 0
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $brokerPid" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Write-Host "Runtime Lifecycle Broker process is already stopped (PID $brokerPid)."
    exit 0
}

$commandLine = [string]$process.CommandLine
foreach ($fragment in @($projectRoot, "runtime_lifecycle_broker.py")) {
    if (-not $commandLine.Contains($fragment)) {
        throw ("PID $brokerPid is not the expected Runtime Lifecycle Broker. Refusing to stop it. CommandLine: " + $commandLine)
    }
}

Stop-Process -Id $brokerPid -Force
$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    Start-Sleep -Milliseconds 200
    if ($null -eq (Get-Process -Id $brokerPid -ErrorAction SilentlyContinue)) {
        Write-Host "Runtime Lifecycle Broker stopped (PID $brokerPid)."
        exit 0
    }
} while ([DateTime]::UtcNow -lt $deadline)

throw "Timed out stopping Runtime Lifecycle Broker PID $brokerPid."
