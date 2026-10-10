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

- These new user-owned package operations are presently available via the **Desktop management IPC / native UI**, not yet as a dedicated ChatGPT-invocable installer capability. ChatGPT-native automatic exploration, generation and registration of arbitrary MCP code needs a separately reviewed broker surface and capability schemas; do not use a writable PLA source root as a substitute.
- This is an opt-in local environment installer and **not an operating-system sandbox**. Confirm untrusted packages before starting them; no arbitrary shell or remote repository install adapter has been added.
- User-owned Skills continue through the existing independent Skill Library package, skill-source permissions and cached catalog. Ordinary Skill import does not require PLA core source development.
- Running RC.6 Desktop, current user credentials, Tunnel and existing Provider configuration are not modified by this development pass.

## Executed checks and evidence

- `tests/test_desktop_custom_packages.py`: manifest binding, pinned Python/npm validation, non-overridable bundled specs, import-before-install, stale-plan checks, independent core development permissions.
- `tests/test_desktop_starter_bundle.py`: exact reviewed bundle, confirmation integrity, simulation of incomplete receipts and asynchronous job state, Tauri whitelist and UI handler contracts.
- `desktop/verification/custom_provider_mcp.py`: **actual source-manager E2E** using a fresh Temp data root and real independent uv-managed Python environment, real installed FastMCP and MCP SDK, live Runtime, Provider enable/discover, real tool invocation returning `PLA_RC7_CUSTOM_MCP_OK`, independent disable and PID preservation. Report: `.desktop-build/rc7-custom-provider-e2e.json`.
- Full Python regression on development source: **872 passed in 149.61s**. After final UI status fixes, targeted Desktop tests **32 passed in 13.37s**. Four critical Python modules also passed py_compile.
- Native NSIS installation, actual installed RC.7 WebView GUI behavior, Node DOM smoke and ChatGPT-native optional-component install are **NOT YET VERIFIED**. Neither Git push nor public release is implied.

## Next verification gate

Run the Node DOM component smoke, rebuild and hash a distinct RC.7 NSIS candidate, and execute native installed GUI E2E in isolation. Exercise starter-pack installation against a clean user environment (full seven real installs), user-owned npm custom package, failure/retry, installed component receipts, existing RC.6 config preservation and separate ChatGPT runtime capability discovery. Decide whether to bundle redistributable mature components directly after audit of installer size/licenses and reproducible dependency provenance.
