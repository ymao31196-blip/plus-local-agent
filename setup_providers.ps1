[CmdletBinding()]
param(
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($env:PLA_PYTHON)) {
    $basePython = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"
} else {
    $basePython = $env:PLA_PYTHON
}
$specDir = Join-Path $projectRoot "provider_specs"

if (-not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
    throw "PLA base Python not found: $basePython"
}
if (-not (Test-Path -LiteralPath $specDir -PathType Container)) {
    throw "Provider spec directory not found: $specDir"
}

$specFiles = @(Get-ChildItem -LiteralPath $specDir -Filter "*.txt" -File | Sort-Object Name)
if ($specFiles.Count -eq 0) {
    throw "No provider specs found in: $specDir"
}

foreach ($spec in $specFiles) {
    $provider = [System.IO.Path]::GetFileNameWithoutExtension($spec.Name)
    if ($provider -notmatch '^[a-z0-9][a-z0-9_-]*$') {
        throw "Invalid provider spec name: $($spec.Name)"
    }

    $envDir = Join-Path $projectRoot ".provider_envs\$provider"
    $providerPython = Join-Path $envDir "Scripts\python.exe"
    $requirements = $spec.FullName

    if ($Recreate -and (Test-Path -LiteralPath $envDir)) {
        Write-Host "Recreating provider environment: $provider"
        Remove-Item -LiteralPath $envDir -Recurse -Force
    }

    if (-not (Test-Path -LiteralPath $providerPython -PathType Leaf)) {
        Write-Host "Creating provider environment: $provider"
        & $basePython -m venv $envDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create provider environment: $provider"
        }
    }

    Write-Host "Installing provider dependencies: $provider"
    & $providerPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install provider dependencies: $provider"
    }

    & $providerPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "Provider dependency check failed: $provider"
    }
}

Write-Host "PLA PROVIDERS READY"
