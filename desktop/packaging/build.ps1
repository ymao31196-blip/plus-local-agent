[CmdletBinding()]
param(
    [string]$Python = "python.exe",
    [string]$ToolchainBin = "",
    [switch]$SkipRuntime,
    [switch]$SkipAcceptance
)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$buildRoot = Join-Path $projectRoot ".desktop-build"
$resources = Join-Path $projectRoot "desktop\src-tauri\resources"
$originalDirectory = Get-Location
$originalPath = $env:PATH
function Assert-Exit([string]$Phase) {
    if ($LASTEXITCODE -ne 0) { throw "$Phase failed with exit code $LASTEXITCODE; inspect build logs." }
}
function Get-VerifiedArchive([string]$Url, [string]$Name, [string]$Hash) {
    $archive = Join-Path $buildRoot $Name
    if (-not (Test-Path -LiteralPath $archive)) { Invoke-WebRequest -Uri $Url -OutFile $archive }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $Hash) {
        throw "Archive checksum mismatch: $Name. File preserved for investigation."
    }
    return $archive
}
try {
    Set-Location -LiteralPath $projectRoot
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    if ($ToolchainBin) { $env:PATH = "$ToolchainBin;$env:PATH" }
    Get-Command cargo.exe,npm.cmd -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Force -Path $resources | Out-Null
    $packPython = Join-Path $buildRoot "venv\Scripts\python.exe"
    if (-not $SkipRuntime) {
        if (-not (Test-Path -LiteralPath $packPython)) {
            & $Python -m venv (Join-Path $buildRoot "venv")
            Assert-Exit "Build virtual environment"
        }
        & $packPython -m pip install -r (Join-Path $PSScriptRoot "requirements-lock.txt") *> (Join-Path $buildRoot "dependencies.log")
        Assert-Exit "Pinned dependencies"
        & $packPython -m PyInstaller --noconfirm --workpath (Join-Path $buildRoot "freeze") --distpath (Join-Path $buildRoot "frozen") (Join-Path $PSScriptRoot "runtime.spec") *> (Join-Path $buildRoot "runtime-build.log")
        Assert-Exit "Frozen Runtime"
        $runtimeDir = Join-Path $resources "runtime"
        if (Test-Path -LiteralPath $runtimeDir) {
            $resolvedTarget = [IO.Path]::GetFullPath($runtimeDir)
            if (-not $resolvedTarget.StartsWith($resources + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe generated resource target" }
            Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
        }
        Copy-Item -LiteralPath (Join-Path $buildRoot "frozen\pla-runtime") -Destination $runtimeDir -Recurse
    }
    $pythonArchive = Get-VerifiedArchive "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" "python-embed.zip" "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
    Expand-Archive -LiteralPath $pythonArchive -DestinationPath (Join-Path $resources "python") -Force
    $tunnelArchive = Get-VerifiedArchive "https://github.com/openai/tunnel-client/releases/download/v0.0.16/tunnel-client-runtime-cloudflared-v0.0.16-windows-amd64.zip" "tunnel16.zip" "02346814ccd0a9a7a4e6d3d494e225c2befb095ede0c46cbe8d71a8d952acfcc"
    $tunnelDirectory = [IO.Path]::GetFullPath((Join-Path $resources "tunnel"))
    if (-not $tunnelDirectory.StartsWith($resources + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe generated Tunnel target" }
    if (Test-Path -LiteralPath $tunnelDirectory) { Remove-Item -LiteralPath $tunnelDirectory -Recurse -Force }
    Expand-Archive -LiteralPath $tunnelArchive -DestinationPath $tunnelDirectory
    Move-Item -LiteralPath (Join-Path $tunnelDirectory "tunnel-client-runtime-cloudflared.exe") -Destination (Join-Path $tunnelDirectory "tunnel-client.exe")
    $node = Join-Path $resources "node.exe"
    if (-not (Test-Path -LiteralPath $node)) { Invoke-WebRequest "https://nodejs.org/dist/v22.16.0/win-x64/node.exe" -OutFile $node }
    if ((Get-FileHash -LiteralPath $node).Hash -ne "c5ff4c736112dd483c750fd4149d30c8a116db1a49b8b3ec88be4b65e6c86c19") { throw "Node component checksum mismatch" }
    & npm.cmd ci --prefix (Join-Path $projectRoot "desktop\browser-component") *> (Join-Path $buildRoot "browser-component.log")
    Assert-Exit "Pinned browser component"
    $browserDir = Join-Path $resources ".provider_envs\browser"
    New-Item -ItemType Directory -Force -Path $browserDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot "desktop\browser-component\node_modules") -Destination $browserDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "provider_manifests\browser-playwright.json") -Destination (Join-Path $resources "browser-playwright.json") -Force
    & $packPython (Join-Path $PSScriptRoot "licenses.py") --output (Join-Path $resources "licenses")
    Assert-Exit "License inventory"
    Invoke-WebRequest "https://raw.githubusercontent.com/nodejs/node/v22.16.0/LICENSE" -OutFile (Join-Path $resources "licenses\NODE-LICENSE.txt")
    & cargo.exe fetch --locked --manifest-path (Join-Path $projectRoot "desktop\src-tauri\Cargo.toml") *> (Join-Path $buildRoot "cargo-dependencies.log")
    Assert-Exit "Locked Rust dependencies"
    $cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE ".cargo" }
    $sdkArchive = Get-VerifiedArchive "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.3800.47/microsoft.web.webview2.1.0.3800.47.nupkg" "webview2.zip" "56c9f26bdd07916a2d1949fb58a5c7e434dfa1173577dca879206050c4e718db"
    $sdkDirectory = Join-Path $buildRoot "webview2-sdk"
    Expand-Archive -LiteralPath $sdkArchive -DestinationPath $sdkDirectory -Force
    $nativeDir = Join-Path $projectRoot "desktop\src-tauri\native"
    New-Item -ItemType Directory -Force -Path $nativeDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $sdkDirectory "build\native\x64\WebView2Loader.dll") -Destination (Join-Path $nativeDir "WebView2Loader.dll") -Force
    Copy-Item -LiteralPath (Join-Path $sdkDirectory "LICENSE.txt") -Destination (Join-Path $resources "licenses\MICROSOFT-WEBVIEW2-LICENSE.txt") -Force
    Copy-Item -LiteralPath (Join-Path $sdkDirectory "NOTICE.txt") -Destination (Join-Path $resources "licenses\MICROSOFT-WEBVIEW2-NOTICE.txt") -Force
    Invoke-WebRequest "https://raw.githubusercontent.com/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE" -OutFile (Join-Path $resources "licenses\WEBVIEW2-RUST-MIT-LICENSE.txt")
    & $packPython (Join-Path $PSScriptRoot "licenses.py") --output (Join-Path $resources "licenses") --cargo-lock (Join-Path $projectRoot "desktop\src-tauri\Cargo.lock") --cargo-home $cargoHome
    Assert-Exit "Rust license inventory"
    if (-not $SkipAcceptance) {
        & $packPython (Join-Path $projectRoot "desktop\verification\packaged_mcp.py") --resources $resources --report (Join-Path $buildRoot "packaged-mcp-report.json") --browser *> (Join-Path $buildRoot "packaged-mcp.log")
        Assert-Exit "Packaged MCP acceptance"
    }
    Set-Location -LiteralPath (Join-Path $projectRoot "desktop")
    & npm.cmd ci *> (Join-Path $buildRoot "frontend-dependencies.log")
    Assert-Exit "Frontend dependencies"
    & npm.cmd run build -- -- --locked *> (Join-Path $buildRoot "installer-build.log")
    Assert-Exit "Tauri NSIS installer"
    $releaseRoot = Join-Path $projectRoot "dist\desktop-v1"
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    $releaseVersion = (Get-Content -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
    $installers = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\target\release\bundle\nsis") -Filter "PLA Desktop_${releaseVersion}_x64-setup.exe")
    if ($installers.Count -ne 1) { throw "Expected one NSIS installer" }
    Copy-Item -LiteralPath $installers[0].FullName -Destination $releaseRoot -Force
    $installer = Join-Path $releaseRoot $installers[0].Name
    $digest = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $releaseRoot "SHA256SUMS.txt"), "$digest  $($installers[0].Name)`n")
    $revision = & git rev-parse HEAD
    # Git status can retain a stat-only change after Tauri rewrites CRLF/LF.
    # Compare actual tracked content (staged and unstaged) and non-ignored new files.
    & git diff --quiet HEAD --
    if ($LASTEXITCODE -notin 0,1) { throw "Unable to verify build source content" }
    $trackedContentDirty = $LASTEXITCODE -eq 1
    $untrackedSource = @(& git ls-files --others --exclude-standard)
    Assert-Exit "Untracked source inventory"
    [ordered]@{ version = $releaseVersion; source_commit = $revision; source_dirty = ($trackedContentDirty -or $untrackedSource.Count -gt 0); installer = $installers[0].Name; sha256 = $digest; signed = $false; updates = "disabled" } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot "build-manifest.json") -Encoding utf8
    Write-Output "Installer: $installer"
    Write-Output "SHA-256: $digest"
} finally {
    $env:PATH = $originalPath
    Set-Location -LiteralPath $originalDirectory
}
