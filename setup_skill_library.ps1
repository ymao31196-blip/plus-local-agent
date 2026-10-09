[CmdletBinding()]
param(
    [string]$SourcePath,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPython = Join-Path $projectRoot ".provider_envs\skill-library\Scripts\python.exe"
$setupScript = Join-Path $projectRoot "setup_providers.ps1"
$defaultCheckout = Join-Path $projectRoot "workspace\skill-library"

function Assert-Source {
    param([string]$InputPath)
    if ([string]::IsNullOrWhiteSpace($InputPath)) {
        $InputPath = $defaultCheckout
    }
    $resolved = Resolve-Path -LiteralPath $InputPath -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        throw "Skill Library source was not found: $InputPath. Clone or download the reviewed Skill Library v0.3.0 source, or pass -SourcePath to a local wheel."
    }
    $path = [string]$resolved.ProviderPath
    if (Test-Path -LiteralPath $path -PathType Container) {
        $metadata = Join-Path $path "pyproject.toml"
        if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) {
            throw "Expected pyproject.toml in the Skill Library source checkout: $path"
        }
        $sourceToml = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8
        if ($sourceToml -notmatch '(?m)^name\s*=\s*"chatgpt-skill-library"\s*$' -or
            $sourceToml -notmatch '(?m)^version\s*=\s*"0\.3\.0"\s*$') {
            throw "The source directory must contain the reviewed chatgpt-skill-library v0.3.0 project."
        }
        return $path
    }
    if (-not ($path -match 'chatgpt_skill_library-0\.3\.0-.*\.whl$')) {
        throw "Only the reviewed chatgpt_skill_library-0.3.0 wheel is accepted: $path"
    }
    return $path
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$Step)
    Write-Host "[PLA Skill Library] $Step"
    $prior = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Program @Arguments 2>&1 | Out-Host
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prior
    }
    if ($exitCode -ne 0) {
        throw "$Step failed (exit $exitCode)."
    }
}

$package = Assert-Source -InputPath $SourcePath
if ($ValidateOnly) {
    [pscustomobject]@{
        status = "validated"
        package = $package
        provider_environment_exists = (Test-Path -LiteralPath $envPython -PathType Leaf)
        write_roots = "disabled-by-default"
    } | ConvertTo-Json
    return
}

if (-not (Test-Path -LiteralPath $setupScript -PathType Leaf)) {
    throw "Missing reviewed setup_providers.ps1 in PLA root."
}
Invoke-Checked -Program "powershell.exe" -Arguments @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", $setupScript, "-Provider", "skill-library"
) -Step "Install reviewed Skill Library provider runtime dependencies"

if (-not (Test-Path -LiteralPath $envPython -PathType Leaf)) {
    throw "Skill Library provider Python environment is unavailable: $envPython"
}
Invoke-Checked -Program $envPython -Arguments @(
    "-m", "pip", "install", "--no-deps", $package
) -Step "Install reviewed Skill Library v0.3.0 server package"

$verify = @'
from importlib.metadata import version
from skill_library.authoring import SkillAuthor
from skill_library.server import mcp
assert version("chatgpt-skill-library") == "0.3.0"
assert mcp.name == "Skill Library"
print("SKILL_LIBRARY_PACKAGE_READY 0.3.0")
'@
Invoke-Checked -Program $envPython -Arguments @(
    "-I", "-c", $verify
) -Step "Verify installed Skill Library package"
Write-Host "[PLA Skill Library] READY. Run the approved provider reload to expose 13 tools."
