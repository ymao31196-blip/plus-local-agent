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
function Invoke-LoggedNative([string]$Phase, [scriptblock]$Command) {
    # Windows PowerShell 5.1 converts native stderr into non-terminating
    # NativeCommandError records. With the script's global Stop preference,
    # normal pip/npm/cargo diagnostics could abort before exit-code checking.
    # Scope Continue to the native invocation only; restore Stop afterward.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        # Run in this scope so LASTEXITCODE is the actual native child status.
        . $Command
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $exitCode -or $exitCode -ne 0) {
        throw "$Phase failed with native exit code $exitCode; inspect the corresponding UTF-8 build log."
    }
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
    # Rustup on Windows sometimes leaves the per-user cargo directory out of PATH.
    # Only use an existing trusted per-user toolchain; never change permanent PATH.
    if (-not (Get-Command cargo.exe -ErrorAction SilentlyContinue)) {
        $cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE ".cargo" }
        $cargoBin = Join-Path $cargoHome "bin"
        if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe") -PathType Leaf) {
            $env:PATH = "$cargoBin;$env:PATH"
        }
    }
    # The previously verified local GNU linker is needed by rustc's Windows GNU target.
    $mingwBin = Join-Path $buildRoot "toolchain\mingw64\bin"
    if (-not (Get-Command gcc.exe -ErrorAction SilentlyContinue) -and
        (Test-Path -LiteralPath (Join-Path $mingwBin "gcc.exe") -PathType Leaf)) {
        $env:PATH = "$mingwBin;$env:PATH"
    }
    if (-not (Get-Command cargo.exe -ErrorAction SilentlyContinue)) {
        throw "Cargo not found. Install the Rust toolchain or specify -ToolchainBin; expected per-user location: %USERPROFILE%\.cargo\bin."
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm.cmd not found. Install Node.js or specify the directory with -ToolchainBin."
    }
    if (-not $SkipAcceptance) {
        if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
            throw "node.exe not found; the required Desktop UI smoke test cannot run."
        }
        # Make the UI contract a blocking preflight, not an optional manual
        # command whose failure can be followed by an apparently good NSIS.
        Invoke-LoggedNative "Component UI smoke" {
            & node.exe (Join-Path $projectRoot "desktop\verification\components-ux-smoke.cjs") 2>&1 |
                Out-File -FilePath (Join-Path $buildRoot "components-ux-smoke.log") -Encoding utf8 -ErrorAction Stop
        }
    }
    New-Item -ItemType Directory -Force -Path $resources | Out-Null
    $packPython = Join-Path $buildRoot "venv\Scripts\python.exe"
    if (-not $SkipRuntime) {
        if (-not (Test-Path -LiteralPath $packPython)) {
            & $Python -m venv (Join-Path $buildRoot "venv")
            Assert-Exit "Build virtual environment"
        }
        Invoke-LoggedNative "Pinned dependencies" {
            & $packPython -m pip install -r (Join-Path $PSScriptRoot "requirements-lock.txt") 2>&1 |
                Out-File -FilePath (Join-Path $buildRoot "dependencies.log") -Encoding utf8 -ErrorAction Stop
        }
        Invoke-LoggedNative "Frozen Runtime" {
            & $packPython -m PyInstaller --noconfirm --workpath (Join-Path $buildRoot "freeze") --distpath (Join-Path $buildRoot "frozen") (Join-Path $PSScriptRoot "runtime.spec") 2>&1 |
                Out-File -FilePath (Join-Path $buildRoot "runtime-build.log") -Encoding utf8 -ErrorAction Stop
        }
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
    $nodeArchive = Get-VerifiedArchive "https://nodejs.org/dist/v22.16.0/node-v22.16.0-win-x64.zip" "node-22.16.0.zip" "21c2d9735c80b8f86dab19305aa6a9f6f59bbc808f68de3eef09d5832e3bfbbd"
    $nodeExpanded = Join-Path $buildRoot "node-runtime"
    Expand-Archive -LiteralPath $nodeArchive -DestinationPath $nodeExpanded -Force
    $nodeTarget = Join-Path $resources "node-runtime"
    New-Item -ItemType Directory -Force -Path $nodeTarget | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $nodeExpanded "node-v22.16.0-win-x64") | Where-Object Name -ne "node.exe" | Copy-Item -Destination $nodeTarget -Recurse -Force
    # uv is an optional official download, not a redistributed binary. This
    # keeps the base installer smaller and preserves the vendor distribution.
    $obsoleteUv = Join-Path $resources "uv.exe"
    if (Test-Path -LiteralPath $obsoleteUv) { Remove-Item -LiteralPath $obsoleteUv -Force }
    Invoke-LoggedNative "Pinned browser component" {
        & npm.cmd ci --prefix (Join-Path $projectRoot "desktop\browser-component") 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "browser-component.log") -Encoding utf8 -ErrorAction Stop
    }
    $browserDir = Join-Path $resources ".provider_envs\browser"
    New-Item -ItemType Directory -Force -Path $browserDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot "desktop\browser-component\node_modules") -Destination $browserDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "provider_manifests\browser-playwright.json") -Destination (Join-Path $resources "browser-playwright.json") -Force
    # Public reviewed assets only; developer environments and local settings are never bundled.
    foreach ($folder in @("provider_manifests", "provider_specs", "providers")) {
        $assetTarget = Join-Path $resources "provider-assets\$folder"
        New-Item -ItemType Directory -Force -Path $assetTarget | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -File | Where-Object {
            $_.Extension -in @(".json", ".txt", ".py", ".mjs") -and $_.Name -notlike "*.local.*"
        } | Copy-Item -Destination $assetTarget -Force
    }
    $publicConfig = Join-Path $resources "provider-assets\config"
    New-Item -ItemType Directory -Force -Path $publicConfig | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot "config\skill-library.json") -Destination $publicConfig -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "provider_patches") -Destination (Join-Path $resources "provider-assets") -Recurse -Force
    $policyAssets = Join-Path $resources "provider-assets\policy\host"
    New-Item -ItemType Directory -Force -Path $policyAssets | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot "src\host\workspace_manager.py"),(Join-Path $projectRoot "src\host\__init__.py") -Destination $policyAssets -Force
    & $packPython (Join-Path $PSScriptRoot "source_snapshot.py") --output (Join-Path $resources "developer-source.zip")
    Assert-Exit "Public self-development source snapshot"
    & $packPython (Join-Path $PSScriptRoot "licenses.py") --output (Join-Path $resources "licenses")
    Assert-Exit "License inventory"
    Invoke-WebRequest "https://raw.githubusercontent.com/nodejs/node/v22.16.0/LICENSE" -OutFile (Join-Path $resources "licenses\NODE-LICENSE.txt")
    Invoke-WebRequest "https://raw.githubusercontent.com/astral-sh/uv/0.12.24/LICENSE-MIT" -OutFile (Join-Path $resources "licenses\UV-MIT-LICENSE.txt")
    Invoke-WebRequest "https://raw.githubusercontent.com/astral-sh/uv/0.12.24/LICENSE-APACHE" -OutFile (Join-Path $resources "licenses\UV-APACHE-LICENSE.txt")
    Invoke-LoggedNative "Locked Rust dependencies" {
        & cargo.exe fetch --locked --manifest-path (Join-Path $projectRoot "desktop\src-tauri\Cargo.toml") 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "cargo-dependencies.log") -Encoding utf8 -ErrorAction Stop
    }
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
        Invoke-LoggedNative "Packaged MCP acceptance" {
            & $packPython (Join-Path $projectRoot "desktop\verification\packaged_mcp.py") --resources $resources --report (Join-Path $buildRoot "packaged-mcp-report.json") --browser 2>&1 |
                Out-File -FilePath (Join-Path $buildRoot "packaged-mcp.log") -Encoding utf8 -ErrorAction Stop
        }
    }
    Set-Location -LiteralPath (Join-Path $projectRoot "desktop")
    Invoke-LoggedNative "Frontend dependencies" {
        & npm.cmd ci 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "frontend-dependencies.log") -Encoding utf8 -ErrorAction Stop
    }
    Invoke-LoggedNative "Tauri NSIS installer" {
        & npm.cmd run build -- -- --locked 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "installer-build.log") -Encoding utf8 -ErrorAction Stop
    }
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
    $untrackedSource = @(& git -C $projectRoot ls-files --others --exclude-standard)
    Assert-Exit "Untracked source inventory"
    [ordered]@{ version = $releaseVersion; source_commit = $revision; source_dirty = ($trackedContentDirty -or $untrackedSource.Count -gt 0); installer = $installers[0].Name; sha256 = $digest; signed = $false; updates = "disabled" } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot "build-manifest.json") -Encoding utf8
    # Refresh the delivery's notices from this exact build, not an earlier candidate.
    $noticeTarget = Join-Path $releaseRoot "third-party-licenses\runtime"
    New-Item -ItemType Directory -Force -Path $noticeTarget | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $resources "licenses") | Copy-Item -Destination $noticeTarget -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $resources "python\LICENSE.txt") -Destination (Join-Path $releaseRoot "third-party-licenses\PYTHON-EXECUTION-LICENSE.txt") -Force
    $tunnelNotices = Join-Path $releaseRoot "third-party-licenses\tunnel"
    New-Item -ItemType Directory -Force -Path $tunnelNotices | Out-Null
    Get-ChildItem -LiteralPath $tunnelDirectory -File | Where-Object { $_.Extension -ne ".exe" } | Copy-Item -Destination $tunnelNotices -Force
    Get-ChildItem -LiteralPath (Join-Path $projectRoot "docs") -File | Where-Object { $_.Name -like "desktop-v1*.md" } | Copy-Item -Destination $releaseRoot -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\desktop-v1-third-party-notices.md") -Destination (Join-Path $releaseRoot "THIRD-PARTY-NOTICES.md") -Force
    Write-Output "Installer: $installer"
    Write-Output "SHA-256: $digest"
} finally {
    $env:PATH = $originalPath
    Set-Location -LiteralPath $originalDirectory
}
