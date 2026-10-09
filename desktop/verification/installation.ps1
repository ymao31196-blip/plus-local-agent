[CmdletBinding()]
param([Parameter(Mandatory)][string]$Installer, [string]$Python = "python.exe")
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$buildRoot = Join-Path $projectRoot ".desktop-build"
$installRoot = [IO.Path]::GetFullPath((Join-Path $buildRoot "installed"))
if (-not $installRoot.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe isolated installation target" }
$existingProducts = @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } | Where-Object DisplayName -eq "PLA Desktop")
if ($existingProducts.Count -gt 0 -or (Test-Path -LiteralPath (Join-Path $installRoot "pla-desktop.exe"))) { throw "An existing PLA Desktop installation was detected; refusing to replace it in this test." }
$evidence = Join-Path $buildRoot "installation-evidence"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$tests = [Collections.Generic.List[object]]::new()
$defaultData = Join-Path $env:LOCALAPPDATA "io.pla.desktop"
$marker = Join-Path $defaultData ("uninstall-preservation-" + [guid]::NewGuid().ToString("N") + ".txt")
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
    New-Item -ItemType Directory -Force -Path $defaultData | Out-Null
    [IO.File]::WriteAllText($marker,"PLA Desktop uninstall preservation test; created by the isolated acceptance harness.")
    & $Python (Join-Path $PSScriptRoot "packaged_mcp.py") --resources (Join-Path $installRoot "resources") --report (Join-Path $evidence "installed-mcp-report.json") --browser *> (Join-Path $evidence "installed-mcp.log")
    if ($LASTEXITCODE -ne 0) { throw "Installed MCP acceptance failed; see installed-mcp.log" }
    $authorizedReport = Get-Content -LiteralPath (Join-Path $evidence "installed-mcp-report.json") -Raw | ConvertFrom-Json
    $tests.Add(@{test="Actual installed MCP and browser execution";status="PASS"})
    & node.exe (Join-Path $PSScriptRoot "native-ui.cjs") --executable (Join-Path $installRoot "pla-desktop.exe") --output $evidence *> (Join-Path $evidence "native-ui.log")
    if ($LASTEXITCODE -ne 0) { throw "Installed native UI acceptance failed; see native-ui.log" }
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
            $tests.Add(@{test="Uninstall preserves default private data marker";status=$(if (Test-Path -LiteralPath $marker) {"PASS"} else {"FAIL"});marker=$marker})
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
    [ordered]@{installer=[IO.Path]::GetFullPath($Installer);installer_sha256=(Get-FileHash -LiteralPath $Installer).Hash.ToLowerInvariant();isolated_installation=$installRoot;clean_windows="NOT TESTED";os=[Environment]::OSVersion.VersionString;tests=$tests} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence "installation-report.json") -Encoding utf8
}
