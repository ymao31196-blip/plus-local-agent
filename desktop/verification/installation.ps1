[CmdletBinding()]
param([Parameter(Mandatory)][string]$Installer, [string]$Python = "", [switch]$AllowSameMachine)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$buildRoot = Join-Path $projectRoot ".desktop-build"
# Use the Python environment that compiled and verified the frozen Runtime.
# An arbitrary active Conda/base Python may lack FastMCP test dependencies.
$verificationPython = if ($Python) { $Python } else { Join-Path $buildRoot "venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $verificationPython -PathType Leaf)) {
    throw "Verification Python is missing: $verificationPython. Build the Desktop first or specify -Python <full path>."
}
function Invoke-LoggedNative([string]$Phase, [scriptblock]$Command, [string]$Log) {
    # Windows PowerShell 5.1 turns native stderr into NativeCommandError with
    # global Stop: an early throw can discard the entire traceback/log.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        . $Command
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $exitCode -or $exitCode -ne 0) {
        throw "$Phase failed with exit code $exitCode. Full stdout/stderr: $Log"
    }
}
$installRoot = [IO.Path]::GetFullPath((Join-Path $buildRoot "installed"))
if (-not $installRoot.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe isolated installation target" }
$defaultData = Join-Path $env:LOCALAPPDATA "io.pla.desktop"
# A same-machine test uses a distinct app installation folder and temporary
# Runtime data. Keep the user's existing default data completely out of the
# test: in this mode no preservation marker is written there.
$hadDefaultData = Test-Path -LiteralPath $defaultData
if ($hadDefaultData -and -not $AllowSameMachine) {
    throw "Existing PLA Desktop private data detected at $defaultData. Re-run with -AllowSameMachine to use an isolated local installation test without touching existing private data."
}
$existingProducts = @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } | Where-Object DisplayName -eq "PLA Desktop")
if ($existingProducts.Count -gt 0 -or (Test-Path -LiteralPath (Join-Path $installRoot "pla-desktop.exe"))) { throw "An existing PLA Desktop installation was detected; refusing to replace it in this test." }
# Each test run owns a distinct evidence directory. Older reports can never
# masquerade as this run's results after an early failure.
$runId = [guid]::NewGuid().ToString("N")
$evidence = Join-Path $buildRoot (Join-Path "installation-evidence" $runId)
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
Write-Output "Installation test evidence: $evidence"
$tests = [Collections.Generic.List[object]]::new()
$marker = if ($hadDefaultData) { $null } else { Join-Path $defaultData ("uninstall-preservation-" + [guid]::NewGuid().ToString("N") + ".txt") }
$installed = $false
$authorizedReport = $null
$legacyBefore = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in 8766,8931,18081 | Select-Object LocalPort,OwningProcess)
try {
    $setup = Start-Process -FilePath ([IO.Path]::GetFullPath($Installer)) -ArgumentList @("/S", "/D=$installRoot") -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "NSIS installation exit code $($setup.ExitCode)" }
    $installed = Test-Path -LiteralPath (Join-Path $installRoot "pla-desktop.exe")
    if (-not $installed) { throw "Installed native executable missing" }
    foreach ($file in @("WebView2Loader.dll", "resources\runtime\pla-runtime.exe", "resources\python\python.exe", "resources\tunnel\tunnel-client.exe", "resources\node.exe")) {
        if (-not (Test-Path -LiteralPath (Join-Path $installRoot $file))) { throw "Installed dependency missing: $file" }
    }
    $tests.Add(@{test="Actual current-user NSIS installation and payload presence";status="PASS"})
    if (-not $hadDefaultData) {
        New-Item -ItemType Directory -Force -Path $defaultData | Out-Null
        [IO.File]::WriteAllText($marker,"PLA Desktop uninstall preservation test; created by the isolated acceptance harness.")
    }
    $mcpLog = Join-Path $evidence "installed-mcp.log"
    Invoke-LoggedNative "Installed MCP acceptance" {
        & $verificationPython (Join-Path $PSScriptRoot "packaged_mcp.py") --resources (Join-Path $installRoot "resources") --report (Join-Path $evidence "installed-mcp-report.json") --browser 2>&1 |
            Out-File -LiteralPath $mcpLog -Encoding utf8 -ErrorAction Stop
    } $mcpLog
    $authorizedReport = Get-Content -LiteralPath (Join-Path $evidence "installed-mcp-report.json") -Raw | ConvertFrom-Json
    $tests.Add(@{test="Actual installed MCP and browser execution";status="PASS"})
    $nativeLog = Join-Path $evidence "native-ui.log"
    Invoke-LoggedNative "Installed native UI acceptance" {
        & node.exe (Join-Path $PSScriptRoot "native-ui.cjs") --executable (Join-Path $installRoot "pla-desktop.exe") --output $evidence 2>&1 |
            Out-File -LiteralPath $nativeLog -Encoding utf8 -ErrorAction Stop
    } $nativeLog
    $tests.Add(@{test="Actual installed Tauri frontend and lifecycle";status="PASS"})
} catch {
    $tests.Add(@{test="Isolated developer-host installation acceptance";status="FAIL";error=$_.Exception.Message})
    throw
} finally {
    if ($installed) {
        $uninstaller = Join-Path $installRoot "uninstall.exe"
        if (Test-Path -LiteralPath $uninstaller) {
            $uninstall = Start-Process -FilePath $uninstaller -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            while ((Test-Path -LiteralPath (Join-Path $installRoot "pla-desktop.exe")) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250 }
            $gone = -not (Test-Path -LiteralPath (Join-Path $installRoot "pla-desktop.exe"))
            $tests.Add(@{test="Actual NSIS uninstall removes application";status=$(if ($gone) {"PASS"} else {"FAIL"});exit_code=$uninstall.ExitCode})
            if ($hadDefaultData) {
                $tests.Add(@{test="Existing PLA Desktop private data directory retained";status=$(if (Test-Path -LiteralPath $defaultData) {"PASS"} else {"FAIL"});path=$defaultData;note="No test marker was written into the existing data"})
            } else {
                $tests.Add(@{test="Uninstall preserves default private data marker";status=$(if (Test-Path -LiteralPath $marker) {"PASS"} else {"FAIL"});marker=$marker})
            }
            if ($null -ne $authorizedReport) {
                $authorizedMarker = Join-Path $authorizedReport.authorized_workspace "approved.txt"
                $preserved = (Test-Path -LiteralPath $authorizedMarker) -and ([IO.File]::ReadAllText($authorizedMarker).Trim() -eq "approved-two")
                $tests.Add(@{test="Uninstall preserves explicitly authorized workspace and MCP-created user file";status=$(if ($preserved) {"PASS"} else {"FAIL"});path=$authorizedMarker})
            }
        } else { $tests.Add(@{test="Actual NSIS uninstall";status="FAIL";error="Uninstaller missing"}) }
    }
    $legacyAfter = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in 8766,8931,18081 | Select-Object LocalPort,OwningProcess)
    $legacySame = -not (Compare-Object ($legacyBefore | Sort-Object LocalPort) ($legacyAfter | Sort-Object LocalPort) -Property LocalPort,OwningProcess)
    $tests.Add(@{test="Existing developer listeners preserved";status=$(if ($legacySame) {"PASS"} else {"FAIL"});before=$legacyBefore;after=$legacyAfter})
    [ordered]@{installer=[IO.Path]::GetFullPath($Installer);installer_sha256=(Get-FileHash -LiteralPath $Installer).Hash.ToLowerInvariant();isolated_installation=$installRoot;same_machine=$AllowSameMachine.IsPresent;existing_private_data_preserved=$hadDefaultData;clean_windows="NOT TESTED";os=[Environment]::OSVersion.VersionString;tests=$tests} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence "installation-report.json") -Encoding utf8
}
