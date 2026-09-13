[CmdletBinding()]
param(
    [int]$ExpectedPid = 0,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
if ($Json) {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
}
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$httpScript = Join-Path $projectRoot "start_http.ps1"
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

function Assert-OwnedHttp([int]$ProcessId) {
    $commandLine = Get-ProcessCommandLine $ProcessId
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        throw "PLA HTTP PID $ProcessId could not be verified. Refusing to stop it."
    }
    foreach ($fragment in @($projectRoot, "server.py")) {
        if (-not $commandLine.Contains($fragment)) {
            throw "Port 8766 belongs to PID $ProcessId, but it is not the expected PLA HTTP process. Refusing to stop it. CommandLine: $commandLine"
        }
    }
}

function Wait-PortState([int]$Port, [bool]$Listening, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $pidValue = Get-ListenerPid $Port
        if ($Listening -and $null -ne $pidValue) {
            return $pidValue
        }
        if (-not $Listening -and $null -eq $pidValue) {
            return $null
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    $target = if ($Listening) { "start listening" } else { "stop listening" }
    throw "Timed out waiting for 127.0.0.1:$Port to $target."
}

$oldPid = Get-ListenerPid 8766
if ($ExpectedPid -gt 0) {
    if ($null -eq $oldPid) {
        throw "Expected PLA HTTP PID $ExpectedPid, but port 8766 is not listening."
    }
    if ($oldPid -ne $ExpectedPid) {
        throw "Expected PLA HTTP PID $ExpectedPid, but port 8766 belongs to PID $oldPid. Refusing restart."
    }
}

if ($null -ne $oldPid) {
    Assert-OwnedHttp $oldPid
    if (-not $Json) {
        Write-Host "Stopping PLA HTTP (PID $oldPid)..."
    }
    Stop-Process -Id $oldPid -Force
    Wait-PortState 8766 $false 10 | Out-Null
} elseif (-not $Json) {
    Write-Host "PLA HTTP is not currently running; starting it."
}

Start-Process -FilePath $powershell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $httpScript) `
    -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
$newPid = Wait-PortState 8766 $true 20
Assert-OwnedHttp $newPid

$tunnelPid = Get-ListenerPid 18081
$result = [ordered]@{
    status = "completed"
    old_pid = if ($null -ne $oldPid) { [int]$oldPid } else { $null }
    new_pid = [int]$newPid
    tunnel_pid = if ($null -ne $tunnelPid) { [int]$tunnelPid } else { $null }
    tunnel_listening = ($null -ne $tunnelPid)
}

if ($Json) {
    $result | ConvertTo-Json -Compress
} elseif ($null -ne $tunnelPid) {
    Write-Host "PLA HTTP restarted on 127.0.0.1:8766 (PID $newPid); tunnel remains running (PID $tunnelPid)."
    Write-Host "PLA HTTP RESTARTED"
} else {
    Write-Warning "PLA HTTP restarted on 127.0.0.1:8766 (PID $newPid), but tunnel health port 18081 is not listening. Run .\start_tunnel.ps1 or .\start_all.ps1."
    Write-Host "PLA HTTP RESTARTED"
}
