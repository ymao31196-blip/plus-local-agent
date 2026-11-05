[CmdletBinding()]
param(
    [string]$Python = "python.exe",
    [string]$ToolchainBin = "",
    [string]$SkillLibrarySource = "",
    [string]$SkillLibraryWheel = "",
    [switch]$SkipRuntime,
    [switch]$SkipAcceptance,
    [string]$UpdaterPublicKeyFile = "",
    [switch]$LocalSignedTest,
    [ValidateRange(1,16)][int]$CargoJobs = 2
)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$buildRoot = Join-Path $projectRoot ".desktop-build"
$resources = Join-Path $projectRoot "desktop\src-tauri\resources"
$originalDirectory = Get-Location
$originalPath = $env:PATH
$originalUpdaterKey = $env:PLA_UPDATER_PUBLIC_KEY
$originalCargoBuildJobs = $env:CARGO_BUILD_JOBS
$useSignedUpdater = -not [string]::IsNullOrWhiteSpace($UpdaterPublicKeyFile)
if ($LocalSignedTest -and -not $useSignedUpdater) {
    throw "-LocalSignedTest requires a signing public key file; unsigned builds are already available."
}
# Preflight the exact CLI that patches the Tauri binary. CLI 2.8.x expects
# a .taubndl PE section; Rust Tauri 2.12.x emits a different token. Catch
# stale or unsynchronized npm lockfiles BEFORE expensive runtime work.
$npmManifest = Get-Content -LiteralPath (Join-Path $projectRoot "desktop\package.json") -Raw | ConvertFrom-Json
$cliRequested = [string]$npmManifest.devDependencies.'@tauri-apps/cli'
# npm lockfile v3 contains a legal empty-string key under "packages".
# Windows PowerShell 5.1 ConvertFrom-Json rejects empty property names, so
# use the already-required Python JSON parser for this single lookup.
$lockPath = Join-Path $projectRoot "desktop\package-lock.json"
$cliLocked = & $Python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8-sig'))['packages']['node_modules/@tauri-apps/cli']['version'])" $lockPath
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($cliLocked)) {
    throw "Unable to read the locked Tauri CLI version from package-lock.json."
}
if ($cliRequested -ne "2.12.2" -or $cliLocked -ne $cliRequested) {
    throw "Incompatible Tauri CLI or stale npm lockfile. From desktop, run: npm.cmd install --save-dev --save-exact @tauri-apps/cli@2.12.2"
}
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
    # Signing is opt-in and fails closed. Never read or print the private key.
    Remove-Item Env:PLA_UPDATER_PUBLIC_KEY -ErrorAction SilentlyContinue
    if ($useSignedUpdater) {
        if (-not (Test-Path -LiteralPath $UpdaterPublicKeyFile -PathType Leaf)) {
            throw "Signed release requires a local Tauri public key file."
        }
        if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
            throw "Signed release requires TAURI_SIGNING_PRIVATE_KEY in the build environment."
        }
        $pubkey = [IO.File]::ReadAllText([IO.Path]::GetFullPath($UpdaterPublicKeyFile), [Text.Encoding]::UTF8).Trim()
        # Tauri CLI writes the public key as Base64-encoded minisign text.
        # The decoded text contains 'untrusted comment: minisign public key'
        # followed by the RW... key line. Do not confuse it with the raw
        # minisign format, and never expose the signing private key in logs.
        try {
            if ($pubkey.Length -lt 80 -or $pubkey.Length -gt 4096) {
                throw "Unexpected public key length"
            }
            $decodedPubkey = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pubkey))
            if (-not $decodedPubkey.StartsWith("untrusted comment: minisign public key") -or
                $decodedPubkey -notmatch '(?m)^RW[A-Za-z0-9+/=]{40,}\r?$') {
                throw "Invalid minisign public key content"
            }
        } catch {
            throw "The supplied Tauri public key file is invalid: expected a Base64-encoded Tauri minisign public key."
        }
        $env:PLA_UPDATER_PUBLIC_KEY = $pubkey
        # Signed development checks may use a dirty worktree, but are isolated
        # from publishable release artifacts and visibly marked as test only.
        if (-not $LocalSignedTest) {
            & git diff --quiet HEAD --
            if ($LASTEXITCODE -ne 0 -or @(& git ls-files --others --exclude-standard).Count -gt 0) {
                throw "Signed release requires a clean reviewed Git worktree."
            }
        }
    }
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
    # Windows GNU builds need a complete MinGW binutils set, including
    # as.exe. The previously bundled toolchain may have been cleaned up.
    $mingwCandidates = @(
        (Join-Path $buildRoot "toolchain\mingw64\bin"),
        "C:\msys64\mingw64\bin"
    )
    $gnuToolsFound = ((Get-Command gcc.exe -ErrorAction SilentlyContinue) -and
                      (Get-Command as.exe -ErrorAction SilentlyContinue) -and
                      (Get-Command ld.exe -ErrorAction SilentlyContinue))
    if (-not $gnuToolsFound) {
        foreach ($mingwBin in $mingwCandidates) {
            if ((Test-Path -LiteralPath (Join-Path $mingwBin "gcc.exe") -PathType Leaf) -and
                (Test-Path -LiteralPath (Join-Path $mingwBin "as.exe") -PathType Leaf) -and
                (Test-Path -LiteralPath (Join-Path $mingwBin "ld.exe") -PathType Leaf)) {
                $env:PATH = "$mingwBin;$env:PATH"
                break
            }
        }
    }
    if (-not (Get-Command cargo.exe -ErrorAction SilentlyContinue)) {
        throw "Cargo not found. Install the Rust toolchain or specify -ToolchainBin; expected per-user location: %USERPROFILE%\.cargo\bin."
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm.cmd not found. Install Node.js or specify the directory with -ToolchainBin."
    }
    # Install locked Playwright dependencies before the real-Edge Skill UI
    # smoke test. Clean CI runners have no developer node_modules cache.
    Invoke-LoggedNative "Pinned browser component" {
        & npm.cmd ci --prefix (Join-Path $projectRoot "desktop\browser-component") 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "browser-component.log") -Encoding utf8 -ErrorAction Stop
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
        Invoke-LoggedNative "Skill quick source UI smoke" {
            & node.exe (Join-Path $projectRoot "desktop\verification\skill-quick-source-smoke.cjs") 2>&1 |
                Out-File -FilePath (Join-Path $buildRoot "skill-quick-source-smoke.log") -Encoding utf8 -ErrorAction Stop
        }
    }
    if (-not $SkipAcceptance) {
        foreach ($smoke in @("activity-toast-smoke.cjs", "verification-status-smoke.cjs", "wide-layout-smoke.cjs")) {
            Invoke-LoggedNative $smoke {
                & node.exe (Join-Path $projectRoot "desktop\verification\$smoke") 2>&1 |
                    Out-File -FilePath (Join-Path $buildRoot "$smoke.log") -Encoding utf8 -ErrorAction Stop
            }
        }
    }
    New-Item -ItemType Directory -Force -Path $resources | Out-Null
    # Ship editable advisory defaults; user overrides live under PLA_DATA_ROOT/config.
    Copy-Item -LiteralPath (Join-Path $projectRoot "CHAGENT.md") `
        -Destination (Join-Path $resources "CHAGENT.md") -Force
    # Local releases rebuild from the exact reviewed source. CI can instead
    # supply the identical SHA-256-verified wheel without private repo access.
    if ($SkillLibrarySource -and $SkillLibraryWheel) {
        throw "Specify either -SkillLibrarySource or -SkillLibraryWheel, not both."
    }
    $wheelInputArgs = if ($SkillLibraryWheel) {
        @("--wheel", $SkillLibraryWheel)
    } else {
        $skillSource = if ($SkillLibrarySource) { $SkillLibrarySource } else { Join-Path $projectRoot "workspace\skill-library" }
        @("--source", $skillSource)
    }
    Invoke-LoggedNative "Reviewed Skill Library Wheel" {
        & $Python (Join-Path $PSScriptRoot "prepare_skill_library.py") @wheelInputArgs --resources $resources 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "skill-library-wheel.log") -Encoding utf8 -ErrorAction Stop
    }
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
    $browserDir = Join-Path $resources ".provider_envs\browser"
    New-Item -ItemType Directory -Force -Path $browserDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot "desktop\browser-component\node_modules") -Destination $browserDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "provider_manifests\browser-playwright.json") -Destination (Join-Path $resources "browser-playwright.json") -Force
    # Public reviewed assets only; developer environments and local settings are never bundled.
    # Recreate generated provider directories: copying over older builds retains
    # removed manifests (for example the retired independent DOCX provider).
    foreach ($folder in @("provider_manifests", "provider_specs", "providers")) {
        $assetTarget = Join-Path $resources "provider-assets\$folder"
        $resolvedAssets = [IO.Path]::GetFullPath($assetTarget)
        if (-not $resolvedAssets.StartsWith($resources + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe generated provider assets target" }
        if (Test-Path -LiteralPath $resolvedAssets) { Remove-Item -LiteralPath $resolvedAssets -Recurse -Force }
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
    # Remove the obsolete generated snapshot from previous local builds.
    $obsoleteSourceSnapshot = Join-Path $resources "developer-source.zip"
    if (Test-Path -LiteralPath $obsoleteSourceSnapshot) {
        Remove-Item -LiteralPath $obsoleteSourceSnapshot -Force
    }
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
    Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $resources "licenses\PLA-APACHE-2.0-LICENSE.txt") -Force
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
    $tauriArgs = @("run", "build", "--")
    if ($useSignedUpdater) {
        # The Tauri CLI reads plugins.updater.pubkey from its merged build
        # config when signing. The Rust compile-time key alone is not enough.
        # Keep the tracked template key-free and generate an ignored local
        # config containing only the *public* key.
        $updaterTemplatePath = Join-Path $projectRoot "desktop\src-tauri\tauri.updater.conf.json"
        $updaterConfig = Get-Content -LiteralPath $updaterTemplatePath -Raw | ConvertFrom-Json
        if ($updaterConfig.bundle.createUpdaterArtifacts -ne $true -or
            -not $updaterConfig.plugins.updater) {
            throw "Invalid Tauri updater configuration template."
        }
        $updaterConfig.plugins.updater.pubkey = $pubkey
        $generatedUpdaterConfig = Join-Path $buildRoot "tauri.updater.generated.conf.json"
        $updaterJson = $updaterConfig | ConvertTo-Json -Depth 20
        [IO.File]::WriteAllText($generatedUpdaterConfig, $updaterJson)
        $tauriArgs += @("--config", $generatedUpdaterConfig)
    }
    $tauriArgs += @("--", "--locked")
    # Bound concurrent rustc processes to reduce memory/commit pressure on
    # 16 GiB Windows machines. -CargoJobs 1 is available for tight systems.
    $env:CARGO_BUILD_JOBS = [string]$CargoJobs
    Invoke-LoggedNative "Tauri NSIS installer" {
        & npm.cmd @tauriArgs 2>&1 |
            Out-File -FilePath (Join-Path $buildRoot "installer-build.log") -Encoding utf8 -ErrorAction Stop
    }
    if ($useSignedUpdater) {
        # A successful NSIS build is not sufficient if Tauri failed to embed
        # the bundle-type marker required to select the correct updater.
        $installerBuildLog = Get-Content -LiteralPath (Join-Path $buildRoot "installer-build.log") -Raw
        if ($installerBuildLog -match '__TAURI_BUNDLE_TYPE variable not found in binary') {
            throw "Tauri bundle-type marker is missing; signed updater build cannot be trusted. See installer-build.log."
        }
    }
    $releaseRoot = Join-Path $projectRoot $(if ($LocalSignedTest) { "dist\desktop-local-signed-test" } else { "dist\desktop-v1" })
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    $releaseVersion = (Get-Content -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
    $installers = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\target\release\bundle\nsis") -Filter "PLA Desktop_${releaseVersion}_x64-setup.exe")
    if ($installers.Count -ne 1) { throw "Expected one NSIS installer" }
    # In Tauri v2 createUpdaterArtifacts=true, the NSIS .exe is itself the
    # signed update artifact; v1Compatible alone creates .nsis.zip archives.
    $assetName = "PLA.Desktop_${releaseVersion}_x64-setup.exe"
    $installer = Join-Path $releaseRoot $assetName
    Copy-Item -LiteralPath $installers[0].FullName -Destination $installer -Force
    $digest = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $releaseRoot "SHA256SUMS.txt"), "$digest  $assetName`n")
    if ($useSignedUpdater) {
        $sourceSignature = "$($installers[0].FullName).sig"
        if (-not (Test-Path -LiteralPath $sourceSignature -PathType Leaf)) {
            throw "Tauri did not produce the signed NSIS installer .exe.sig; signed release blocked."
        }
        $assetSignature = Join-Path $releaseRoot "$assetName.sig"
        Copy-Item -LiteralPath $sourceSignature -Destination $assetSignature -Force
        & $Python (Join-Path $projectRoot "scripts/build_signed_updater_manifest.py") --version $releaseVersion --installer $installer --signature $assetSignature --output (Join-Path $releaseRoot "latest.json")
        Assert-Exit "Signed updater manifest"
    } else {
        # Do not accidentally publish signed artifacts from an earlier build.
        foreach ($obsolete in @("latest.json", "$assetName.sig", "PLA.Desktop_${releaseVersion}_x64-setup.nsis.zip", "PLA.Desktop_${releaseVersion}_x64-setup.nsis.zip.sig")) {
            $obsoletePath = Join-Path $releaseRoot $obsolete
            if (Test-Path -LiteralPath $obsoletePath) { Remove-Item -LiteralPath $obsoletePath -Force }
        }
    }
    $revision = & git rev-parse HEAD
    # Git status can retain a stat-only change after Tauri rewrites CRLF/LF.
    # Compare actual tracked content (staged and unstaged) and non-ignored new files.
    & git diff --quiet HEAD --
    if ($LASTEXITCODE -notin 0,1) { throw "Unable to verify build source content" }
    $trackedContentDirty = $LASTEXITCODE -eq 1
    $untrackedSource = @(& git -C $projectRoot ls-files --others --exclude-standard)
    Assert-Exit "Untracked source inventory"
    [ordered]@{ version = $releaseVersion; source_commit = $revision; source_dirty = ($trackedContentDirty -or $untrackedSource.Count -gt 0); release_channel = $(if ($LocalSignedTest) { "local-test-only" } else { "stable-candidate" }); publishable = (-not $LocalSignedTest -and -not ($trackedContentDirty -or $untrackedSource.Count -gt 0)); installer = $assetName; sha256 = $digest; updater_signature = [bool]$useSignedUpdater; windows_authenticode_signed = $false; updates = $(if ($useSignedUpdater) { "signed_tauri_updater" } else { "disabled" }) } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot "build-manifest.json") -Encoding utf8
    if ($LocalSignedTest) {
        [IO.File]::WriteAllText((Join-Path $releaseRoot "DO_NOT_PUBLISH.txt"), "Local signed test build from an unreviewed worktree. Do not upload or publish these artifacts.`n")
    }
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
    Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $releaseRoot "LICENSE") -Force
    Write-Output "Installer: $installer"
    Write-Output "SHA-256: $digest"
} finally {
    $env:PATH = $originalPath
    if ($null -eq $originalCargoBuildJobs) {
        Remove-Item Env:CARGO_BUILD_JOBS -ErrorAction SilentlyContinue
    } else {
        $env:CARGO_BUILD_JOBS = $originalCargoBuildJobs
    }
    if ($null -eq $originalUpdaterKey) {
        Remove-Item Env:PLA_UPDATER_PUBLIC_KEY -ErrorAction SilentlyContinue
    } else {
        $env:PLA_UPDATER_PUBLIC_KEY = $originalUpdaterKey
    }
    Set-Location -LiteralPath $originalDirectory
}
