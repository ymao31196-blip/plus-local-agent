[CmdletBinding()]
param(
    [string]$Python,
    [string]$TunnelId,
    [string]$TunnelClient,
    [string]$TunnelCredential,
    [switch]$SkipProviders,
    [switch]$SkipTests,
    [switch]$SkipTunnel,
    [switch]$Start,
    [switch]$PersistEnvironment,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$venvDir = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$localTunnelConfig = Join-Path $projectRoot "config\tunnel.local.yaml"
$expectedPythonMajor = 3
$expectedPythonMinor = 11

function Write-Step([string]$Message) {
    Write-Host ("[PLA] " + $Message)
}

function Resolve-ExecutablePath([string]$CommandName) {
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    if (-not [string]::IsNullOrWhiteSpace($command.Source)) {
        return [string]$command.Source
    }
    return [string]$command.Path
}

function Resolve-ExistingFile([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }
    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    $command = Resolve-ExecutablePath $Value
    if ($null -ne $command -and (Test-Path -LiteralPath $command -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $command).Path
    }
    return $null
}

function Get-PythonVersionInfo([string]$Interpreter) {
    $resolved = Resolve-ExistingFile $Interpreter
    if ($null -eq $resolved) {
        return $null
    }
    $json = $null
    $exitCode = 1
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $json = (& $resolved -c "import json,sys; print(json.dumps({'executable':sys.executable,'major':sys.version_info.major,'minor':sys.version_info.minor,'micro':sys.version_info.micro}))" 2>$null | Out-String).Trim()
        $exitCode = $LASTEXITCODE
    } catch {
        $json = $null
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($json)) {
        return $null
    }
    try {
        return ($json | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Resolve-Python311([string]$Requested) {
    $candidates = @()

    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates += $Requested
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PLA_PYTHON)) {
        $candidates += $env:PLA_PYTHON
    }
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $candidates += $venvPython
    }

    $defaultConda = Join-Path $env:USERPROFILE "miniconda3\envs\plus-local-agent\python.exe"
    if (Test-Path -LiteralPath $defaultConda -PathType Leaf) {
        $candidates += $defaultConda
    }

    $pyLauncher = Resolve-ExecutablePath "py.exe"
    if ($null -ne $pyLauncher) {
        $pyResolved = $null
        $pyExitCode = 1
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $pyResolved = (& $pyLauncher -3.11 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
            $pyExitCode = $LASTEXITCODE
        } catch {
            $pyResolved = $null
            $pyExitCode = 1
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($pyExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($pyResolved)) {
            $candidates += $pyResolved
        }
    }

    foreach ($name in @("python.exe", "python", "python3.exe", "python3")) {
        $resolved = Resolve-ExecutablePath $name
        if ($null -ne $resolved) {
            $candidates += $resolved
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $info = Get-PythonVersionInfo $candidate
        if ($null -ne $info -and
            [int]$info.major -eq $expectedPythonMajor -and
            [int]$info.minor -eq $expectedPythonMinor) {
            return [pscustomobject]@{
                path = [string]$info.executable
                version = "$($info.major).$($info.minor).$($info.micro)"
            }
        }
    }
    return $null
}

function Resolve-Edge {
    $command = Resolve-ExecutablePath "msedge.exe"
    if ($null -ne $command) {
        return $command
    }
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe")
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Set-PLAEnvironment([string]$Name, [string]$Value) {
    Set-Item -Path ("Env:" + $Name) -Value $Value
    if ($PersistEnvironment) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    }
}

function Assert-Command([string]$Name, [string]$InstallHint) {
    $resolved = Resolve-ExecutablePath $Name
    if ($null -eq $resolved) {
        throw "$Name is required. $InstallHint"
    }
    return $resolved
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$Label) {
    Write-Step $Label

    $previousErrorActionPreference = $ErrorActionPreference
    $nativeException = $null
    $exitCode = 1
    try {
        # Native stderr is diagnostic output, not a PowerShell failure signal.
        # Merge it into stdout and judge success only by process exit code.
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        $nativeException = $_
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($null -ne $nativeException) {
        throw ("{0}: {1}" -f $Label, $nativeException.Exception.Message)
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
    }
}

function Resolve-TunnelClient([string]$Requested) {
    foreach ($candidate in @(
        $Requested,
        $env:PLA_TUNNEL_CLIENT,
        (Join-Path (Split-Path -Parent $projectRoot) "tunnel-client\tunnel-client.exe")
    )) {
        $resolved = Resolve-ExistingFile $candidate
        if ($null -ne $resolved) {
            return $resolved
        }
    }
    return $null
}

function Resolve-TunnelCredential([string]$Requested) {
    foreach ($candidate in @(
        $Requested,
        $env:PLA_TUNNEL_CREDENTIAL,
        (Join-Path (Split-Path -Parent $projectRoot) "tunnel-client\secrets\control-plane-api-key.txt")
    )) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf) -and
            (Get-Item -LiteralPath $candidate).Length -gt 0) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

if ($env:OS -ne "Windows_NT") {
    throw "PLA customer installer currently supports Windows only."
}

Write-Step "Auditing prerequisites..."
$git = Assert-Command "git.exe" "Install Git for Windows first."
$node = Assert-Command "node.exe" "Install current Node.js LTS first."
$npm = Assert-Command "npm.cmd" "Install current Node.js LTS (including npm) first."
$powershell = Assert-Command "powershell.exe" "Windows PowerShell 5.1 or later is required."
$edge = Resolve-Edge
if ($null -eq $edge) {
    throw "Microsoft Edge is required by the Browser Provider. Install Edge and rerun install.ps1."
}

$pythonInfo = Resolve-Python311 $Python
if ($null -eq $pythonInfo) {
    throw "Python 3.11 was not found. Install Python 3.11 or Miniconda, then rerun install.ps1."
}

$sourcePython = [string]$pythonInfo.path
$gitVersion = (& $git --version).Trim()
$nodeVersion = (& $node --version).Trim()
$npmVersion = (& $npm --version).Trim()

Write-Step "Git: $gitVersion"
Write-Step "Node: $nodeVersion"
Write-Step "npm: $npmVersion"
Write-Step "Python source: $sourcePython ($($pythonInfo.version))"
Write-Step "Microsoft Edge: $edge"

$effectiveTunnelClient = Resolve-TunnelClient $TunnelClient
$effectiveTunnelCredential = Resolve-TunnelCredential $TunnelCredential

if ($ValidateOnly) {
    $validationStatus = if (
        $null -ne $effectiveTunnelClient -and
        $null -ne $effectiveTunnelCredential -and
        (Test-Path -LiteralPath $localTunnelConfig -PathType Leaf)
    ) { "ready" } else { "partial" }

    $validation = [ordered]@{
        status = $validationStatus
        project_root = $projectRoot
        git = $gitVersion
        node = $nodeVersion
        npm = $npmVersion
        python_source = $pythonInfo.path
        python_version = $pythonInfo.version
        edge = $edge
        venv_exists = (Test-Path -LiteralPath $venvPython -PathType Leaf)
        tunnel_local_config_exists = (Test-Path -LiteralPath $localTunnelConfig -PathType Leaf)
        tunnel_client = $effectiveTunnelClient
        tunnel_credential_configured = ($null -ne $effectiveTunnelCredential)
    }
    $validation | ConvertTo-Json -Depth 4
    exit 0
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Step "Creating repository-local Python 3.11 environment at .venv..."
    & $sourcePython -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create PLA .venv."
    }
}

$venvInfo = Get-PythonVersionInfo $venvPython
if ($null -eq $venvInfo -or
    [int]$venvInfo.major -ne $expectedPythonMajor -or
    [int]$venvInfo.minor -ne $expectedPythonMinor) {
    throw "PLA .venv is not Python 3.11. Remove .venv and rerun the installer."
}

Set-PLAEnvironment "PLA_PYTHON" $venvPython

Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip") "Updating pip"
Invoke-Checked $venvPython @("-m", "pip", "install", "-r", (Join-Path $projectRoot "requirements-core.txt")) "Installing PLA core dependencies"
Invoke-Checked $venvPython @("-m", "pip", "install", "-r", (Join-Path $projectRoot "requirements-documents.txt")) "Installing document dependencies"
if (-not $SkipTests) {
    Invoke-Checked $venvPython @("-m", "pip", "install", "-r", (Join-Path $projectRoot "requirements-dev.txt")) "Installing development/test dependencies"
}
Invoke-Checked $venvPython @("-m", "pip", "check") "Checking PLA Python dependencies"

if (-not $SkipProviders) {
    $setupProviders = Join-Path $projectRoot "setup_providers.ps1"
    Invoke-Checked $powershell @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $setupProviders
    ) "Installing reviewed Provider environments"
}

$wingetMcp = Resolve-ExecutablePath "WindowsPackageManagerMCPServer.exe"
$wingetStatus = if ($null -ne $wingetMcp) { "ready" } else { "missing" }
if ($wingetStatus -eq "missing") {
    Write-Warning "WindowsPackageManagerMCPServer.exe was not found. The WinGet Provider will remain unavailable until Windows Package Manager MCP support is installed."
}

$tunnelReady = $false
$tunnelConfigPath = $null

if (-not $SkipTunnel) {
    if (-not [string]::IsNullOrWhiteSpace($TunnelId)) {
        if ($TunnelId -notmatch '^tunnel_[A-Za-z0-9]+$') {
            throw "TunnelId must be a distinct customer tunnel id such as tunnel_xxx."
        }
        $configContent = @"
config_version: 1
control_plane:
  base_url: "https://api.openai.com"
  tunnel_id: "$TunnelId"
health:
  listen_addr: "127.0.0.1:18081"
admin_ui:
  open_browser: false
log:
  level: info
  format: json
mcp:
  server_urls:
    - channel: main
      url: "http://127.0.0.1:8766/mcp"
"@
        [System.IO.File]::WriteAllText(
            $localTunnelConfig,
            $configContent,
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    if ((Test-Path -LiteralPath $localTunnelConfig -PathType Leaf) -and
        $null -ne $effectiveTunnelClient -and
        $null -ne $effectiveTunnelCredential) {

        Set-PLAEnvironment "PLA_TUNNEL_CONFIG" $localTunnelConfig
        Set-PLAEnvironment "PLA_TUNNEL_CLIENT" $effectiveTunnelClient
        Set-PLAEnvironment "PLA_TUNNEL_CREDENTIAL" $effectiveTunnelCredential

        $credentialPath = $effectiveTunnelCredential -replace '\\', '/'
        $credentialRef = "file:$credentialPath"
        Write-Step "Validating customer Secure MCP Tunnel..."
        & $effectiveTunnelClient doctor --config $localTunnelConfig --control-plane.api-key $credentialRef --explain
        if ($LASTEXITCODE -ne 0) {
            throw "Secure MCP Tunnel doctor failed."
        }
        $tunnelReady = $true
        $tunnelConfigPath = $localTunnelConfig
    } else {
        Write-Warning "Customer Tunnel is not configured yet. Local PLA installation will complete as PARTIAL. Provide a customer-specific Tunnel ID, tunnel-client, and credential, then rerun install.ps1."
    }
}

if (-not $SkipTests) {
    Invoke-Checked $venvPython @("-m", "pytest", "-q") "Running PLA regression suite"
}

$started = $false
if ($Start) {
    if ($SkipTunnel -or -not $tunnelReady) {
        Write-Warning "PLA was not started because a customer-specific Secure MCP Tunnel is not ready."
    } else {
        Write-Step "Starting PLA runtime stack..."
        & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot "start_all.ps1")
        if ($LASTEXITCODE -ne 0) {
            throw "start_all.ps1 failed."
        }
        $started = $true
    }
}

$complete = (
    -not $SkipProviders -and
    -not $SkipTests -and
    $wingetStatus -eq "ready" -and
    -not $SkipTunnel -and
    $tunnelReady
)
$finalStatus = if ($complete) { "PASS" } else { "PARTIAL" }

$summary = [ordered]@{
    status = $finalStatus
    project_root = $projectRoot
    python = $venvPython
    python_version = "$($venvInfo.major).$($venvInfo.minor).$($venvInfo.micro)"
    node = $nodeVersion
    npm = $npmVersion
    edge = $edge
    providers_installed = (-not $SkipProviders)
    winget_provider_runtime = $wingetStatus
    regression_tested = (-not $SkipTests)
    tunnel_ready = $tunnelReady
    tunnel_config = $tunnelConfigPath
    started = $started
    environment_persisted = [bool]$PersistEnvironment
}

Write-Step "INSTALLATION RESULT"
$summary | ConvertTo-Json -Depth 4
