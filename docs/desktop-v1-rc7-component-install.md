# PLA Desktop RC.7 — component delivery and custom MCP installation (development)

Date: 2026-10-10. This document describes **unreleased source changes** on `codex/pla-desktop-v1`. RC.6 installed binary is unchanged until a new build is performed.

## Goals and implemented first phase

Desktop uses the same Provider manifests, reviewed source selection, capability broker and lifecycle as PLA. The UI now separates a compact Provider list from advanced management, offers an explicit **启用并连接** action when dependency files exist, folds verbose installation JSON/logs, and replaces the raw `Unknown capability: skill-library.states` error with an onboarding explanation when Skill Library is unavailable.

### Official starter pack

The fixed starter pack contains exactly seven independently pinned environments: `computer`, `docx`, `markitdown`, `office-enhancement`, `pdf`, `software-migration`, and `windows-management`. A `provider_bundle` management operation returns an immutable digest of every reviewed dependency plan, requires explicit confirmation of that digest, checks that no relevant Provider is running, and starts one owned serial installer. The real `installation_status` reports the job as installed only if the process exits 0 and **all seven** independent install receipts exist. Partial failures remain visible and can be repaired per Provider. The batch never enables MCPs or grants access. Browser is already bundled; WPS depends on separate verified Git/npm source installation; WinGet lacks a bundled install recipe; and Skill Library requires the user's own reviewed v0.5.0 source/wheel due to redistribution constraints.

This reduces repeated manual installation clicks, but **does not yet preinstall all third-party dependencies inside the NSIS installer**, nor establish clean-Windows offline installation or permission-free auto-enablement.

### User-owned custom Provider installation

The `provider_package` management API requires the user to **import and review a unique manifest first**. It accepts only exact-version package names for PyPI (`name==version`) or npm (`@scope/name@version`), up to 24 entries. No Git URLs, shell commands, local arbitrary scripts, unpinned dependencies, or source-substitution for bundled Provider IDs. The package plan SHA-256 covers the selected pins, manifest bytes and existing private spec digest, and a second explicit confirmation is required to save it. It is stored only in `components/custom_specs/` under private Desktop user data. Installer lookup prefers immutable bundled specifications and rejects custom attempts to shadow their manifests. Each custom provider uses `components/.provider_envs/<provider-id>` with Python 3.11.9 via verified uv or installed bundled Node. Custom Python installation requires wheels only (`--only-binary :all:`); custom npm uses `--ignore-scripts`. This prevents arbitrary package *build* scripts, but third-party packages still execute runtime code once intentionally enabled. Source URL provenance and integrity beyond exact package versions remain a future hardening item.

Python manifest's runtime interpreter must resolve to `.provider_envs/<id>/Scripts/python.exe` inside managed component data. npm manifest's runtime executable must resolve to the **absolute bundled `node.exe`** (not the system PATH's `node.exe`) and its `cwd` must be `.provider_envs/<id>`. Entry arguments and tool allowlists are validated by the original manifest loader. Additional `runtime.discovery_timeout_seconds` may be required for cold-starting Python MCP servers. The existing `provider_install` job manager handles installation and receipts; `provider_action` separately enables and discovers actual tools. Existing `pla-development` source access remains disabled and is not required.

### Important boundaries

- The same user-owned package workflow is now also exposed to **ChatGPT through Desktop-only Capability Broker entries**: `runtime.desktop_package_preview` (read), `runtime.desktop_package_commit` (privileged, INVOKE), `runtime.desktop_install_preview` (read), and `runtime.desktop_install_status` (read). The existing privileged `runtime.provider_setup` requires an exact reviewed plan SHA-256 to start an asynchronous Desktop installation; `runtime.provider_import`, `runtime.provider_enable`, and `runtime.provider_disable` complete the lifecycle. These entries do not appear on source-only PLA runtimes, do not mutate PLA core code, and do not make untrusted packages safe by default. Arbitrary Git-hosted source installation is deliberately not implemented; ChatGPT can prepare reviewed supported packages, not bypass installation safeguards.
- Native Desktop GUI and ChatGPT install jobs now share an OS-managed cross-process lock in the same private component directory. Only one path can modify a component environment at a time.
- This is an opt-in local environment installer and **not an operating-system sandbox**. Confirm untrusted packages before starting them; no arbitrary shell or remote repository install adapter has been added.
- User-owned Skills continue through the existing independent Skill Library package, skill-source permissions and cached catalog. Ordinary Skill import does not require PLA core source development.
- Running RC.6 Desktop, current user credentials, Tunnel and existing Provider configuration are not modified by this development pass.

## Executed checks and evidence

- `tests/test_desktop_custom_packages.py`: manifest binding, pinned Python/npm validation, non-overridable bundled specs, import-before-install, stale-plan checks, independent core development permissions.
- `tests/test_desktop_starter_bundle.py`: exact reviewed bundle, confirmation integrity, simulation of incomplete receipts and asynchronous job state, Tauri whitelist and UI handler contracts.
- `desktop/verification/custom_provider_mcp.py`: **actual source-manager E2E** using a fresh Temp data root and real independent uv-managed Python environment, real installed FastMCP and MCP SDK, live Runtime, Provider enable/discover, real tool invocation returning `PLA_RC7_CUSTOM_MCP_OK`, independent disable and PID preservation. Report: `.desktop-build/rc7-custom-provider-e2e.json`.
- Full Python regression after ChatGPT-native bridge and cross-process locking: **879 passed in 157.93s**. Focused Desktop/Bridge regressions: **39 passed**. Frozen Runtime native build still requires the trusted Windows build script.
- RC.7 version metadata is aligned across Python, npm, Tauri, Cargo and Cargo.lock. No user-install update or automatic migration has occurred.
- Actual starter-pack dependency installation in fresh user Temp data: seven pinned environments, seven receipts and seven real Python executables, job exit 0. Report: `.desktop-build/rc7-starter-pack-e2e.json`.
- Actual seven MCP startup and tool discovery: all 7 ready; 65 available tools total, main Runtime PID preserved. MarkItDown initially failed with MCP SDK 1.8.1 vs Pydantic 2.14.0; pinned Pydantic 2.10.6 and extended discovery timeout to 90 seconds, real compatibility probe passed. Reports: `.desktop-build/rc7-starter-discovery-e2e.json`, `.desktop-build/rc7-markitdown-compat.json`.
- Actual Desktop Runtime using only stable Capability Broker actions: import new manifest, preview and commit exact dependency pins, queue real private installer, poll its receipts and exit code, hot-enable MCP, invoke a real test tool and disable without Runtime restart; 9/9 passed. Report: `.desktop-build/rc7-chatgpt-native-e2e.json`.
- Python regression and Broker install lock tests included; the installed RC.6 was not touched. Native RC.7 NSIS installation, its real WebView2 GUI and JavaScript Node DOM smoke **remain unverified**; no build, push, or public release is implied.

## Next verification gate

The source-stage E2E gates (all seven installs, 7/7 MCP discovery, ChatGPT-native real MCP installation and private cross-process lock) have passed. Remaining before a release:
1. From the normal native Windows developer terminal, run `node desktop/verification/components-ux-smoke.cjs` and fix any GUI DOM regression. This tool is not permitted through the present PLA run_process policy; don't bypass its allowlist.
2. Build the candidate using `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\desktop\\packaging\\build.ps1`; verify `dist/desktop-v1/PLA Desktop_1.0.0-rc.7_x64-setup.exe` and build-manifest/SHA-256. Do not use `-SkipRuntime` or `-SkipAcceptance` for the release candidate.
3. Perform native installed WebView2 GUI E2E in a separate Windows user/VM, including uninstall/upgrade retention, visible install success/failure/retry, verified ChatGPT capability discovery through installed Tunnel, private user source installation, user-owned npm package, WPS external prerequisite, Skill Library onboarding, and clean-Windows license/provenance review.
4. Installation is still user-initiated: bundled starter pack is one-click, not fully preinstalled. Evaluate signed reproducible prebundled third-party environments for the future stable 1.0 installer after verifying redistribution rights and size.
No RC.7 installer build or release has been executed in this development session.
