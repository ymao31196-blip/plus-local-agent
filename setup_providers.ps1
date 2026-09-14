[CmdletBinding()]
param(
    [switch]$Recreate,
    [string[]]$Provider
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($env:PLA_PYTHON)) {
    $basePython = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"
} else {
    $basePython = $env:PLA_PYTHON
}
$specDir = Join-Path $projectRoot "provider_specs"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [object[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $nativeException = $null
    $exitCode = 1
    try {
        # Windows PowerShell can surface ordinary native stderr as a
        # NativeCommandError. Keep script errors strict, but judge native
        # commands by their real exit code instead of stderr text.
        $ErrorActionPreference = "Continue"
        & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
    } catch {
        $nativeException = $_
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($null -ne $nativeException) {
        throw ("{0}: {1}" -f $FailureMessage, $nativeException.Exception.Message)
    }
    if ($exitCode -ne 0) {
        throw ("{0} (exit code {1})" -f $FailureMessage, $exitCode)
    }
}

if (-not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
    throw "PLA base Python not found: $basePython"
}
if (-not (Test-Path -LiteralPath $specDir -PathType Container)) {
    throw "Provider spec directory not found: $specDir"
}

$specFiles = @(
    Get-ChildItem -LiteralPath $specDir -Filter "*.txt" -File |
        Where-Object { $_.Name -notlike "*.npm.txt" } |
        Sort-Object Name
)
$nodeSpecFiles = @(
    Get-ChildItem -LiteralPath $specDir -Filter "*.npm.txt" -File |
        Sort-Object Name
)
if ($specFiles.Count -eq 0 -and $nodeSpecFiles.Count -eq 0) {
    throw "No provider specs found in: $specDir"
}

$requestedProviders = @()
if ($null -ne $Provider -and $Provider.Count -gt 0) {
    foreach ($name in $Provider) {
        $normalized = ([string]$name).Trim().ToLowerInvariant()
        if ($normalized -notmatch '^[a-z0-9][a-z0-9_-]*$') {
            throw "Invalid requested provider id: $name"
        }
        if ($normalized -notin $requestedProviders) {
            $requestedProviders += $normalized
        }
    }

    $availableProviders = @()
    foreach ($spec in $specFiles) {
        $availableProviders += [System.IO.Path]::GetFileNameWithoutExtension($spec.Name)
    }
    foreach ($spec in $nodeSpecFiles) {
        $suffix = ".npm.txt"
        $availableProviders += $spec.Name.Substring(0, $spec.Name.Length - $suffix.Length)
    }
    $availableProviders = @($availableProviders | Sort-Object -Unique)

    $unknownProviders = @(
        $requestedProviders | Where-Object { $_ -notin $availableProviders }
    )
    if ($unknownProviders.Count -gt 0) {
        throw "No reviewed dependency spec for provider(s): $($unknownProviders -join ', ')"
    }

    $specFiles = @(
        $specFiles | Where-Object {
            [System.IO.Path]::GetFileNameWithoutExtension($_.Name) -in $requestedProviders
        }
    )
    $nodeSpecFiles = @(
        $nodeSpecFiles | Where-Object {
            $suffix = ".npm.txt"
            $_.Name.Substring(0, $_.Name.Length - $suffix.Length) -in $requestedProviders
        }
    )
}

$selectedProviders = @()
foreach ($spec in $specFiles) {
    $selectedProviders += [System.IO.Path]::GetFileNameWithoutExtension($spec.Name)
}
foreach ($spec in $nodeSpecFiles) {
    $suffix = ".npm.txt"
    $selectedProviders += $spec.Name.Substring(0, $spec.Name.Length - $suffix.Length)
}
$selectedProviders = @($selectedProviders | Sort-Object -Unique)

if ($Recreate) {
    foreach ($providerId in $selectedProviders) {
        $envDir = Join-Path $projectRoot ".provider_envs\$providerId"
        if (Test-Path -LiteralPath $envDir) {
            Write-Host "Recreating provider environment: $providerId"
            Remove-Item -LiteralPath $envDir -Recurse -Force
        }
    }
}

foreach ($spec in $specFiles) {
    $provider = [System.IO.Path]::GetFileNameWithoutExtension($spec.Name)
    if ($provider -notmatch '^[a-z0-9][a-z0-9_-]*$') {
        throw "Invalid provider spec name: $($spec.Name)"
    }

    $envDir = Join-Path $projectRoot ".provider_envs\$provider"
    $providerPython = Join-Path $envDir "Scripts\python.exe"
    $requirements = $spec.FullName

    if (-not (Test-Path -LiteralPath $providerPython -PathType Leaf)) {
        Write-Host "Creating provider environment: $provider"
        Invoke-NativeChecked `
            -FilePath $basePython `
            -ArgumentList @("-m", "venv", $envDir) `
            -FailureMessage "Failed to create provider environment: $provider"
    }

    Write-Host "Installing provider dependencies: $provider"
    Invoke-NativeChecked `
        -FilePath $providerPython `
        -ArgumentList @("-m", "pip", "install", "-r", $requirements) `
        -FailureMessage "Failed to install provider dependencies: $provider"

    Invoke-NativeChecked `
        -FilePath $providerPython `
        -ArgumentList @("-m", "pip", "check") `
        -FailureMessage "Provider dependency check failed: $provider"
}

if ($nodeSpecFiles.Count -gt 0) {
    $nodeCommand = Get-Command node.exe -ErrorAction Stop
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop

    foreach ($spec in $nodeSpecFiles) {
        $suffix = ".npm.txt"
        $provider = $spec.Name.Substring(0, $spec.Name.Length - $suffix.Length)
        if ($provider -notmatch '^[a-z0-9][a-z0-9_-]*$') {
            throw "Invalid Node provider spec name: $($spec.Name)"
        }

        $envDir = Join-Path $projectRoot ".provider_envs\$provider"
        if (-not (Test-Path -LiteralPath $envDir -PathType Container)) {
            New-Item -ItemType Directory -Path $envDir -Force | Out-Null
        }

        $packages = @(
            Get-Content -LiteralPath $spec.FullName |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ -and -not $_.StartsWith("#") }
        )
        if ($packages.Count -eq 0) {
            throw "No Node packages declared for provider: $provider"
        }

        Write-Host "Installing Node provider dependencies: $provider"
        $previousSkipBrowserDownload = $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD
        $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
        try {
            $npmInstallArguments = @("install", "--prefix", $envDir, "--no-save", "--package-lock=false") + $packages
            Invoke-NativeChecked `
                -FilePath $npmCommand.Source `
                -ArgumentList $npmInstallArguments `
                -FailureMessage "Failed to install Node provider dependencies: $provider"
        } finally {
            $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = $previousSkipBrowserDownload
        }

        Invoke-NativeChecked `
            -FilePath $npmCommand.Source `
            -ArgumentList @("ls", "--prefix", $envDir, "--depth=0") `
            -FailureMessage "Node provider dependency check failed: $provider"
    }
}

Write-Host "PLA PROVIDERS READY"
