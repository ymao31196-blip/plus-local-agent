# PLA Desktop WPS installation repair: frozen Runtime npm command

Date: 2026-10-10. Status: **source fix and mock-process regression validated; currently installed Desktop WPS NOT repaired**.

## Actual installed Windows Desktop outcome

Current PLA-TEST is connected to the installed Desktop Runtime at `D:\PLA Desktop`, with its private data at `C:\Users\26286\AppData\Local\io.pla.desktop`. The user authorized stopping and repairing only `wps-office`:

1. Real `runtime.provider_doctor`: `state=error`, `MCPError: Connection closed`, three consecutive startup failures and 0 tools. Read-only filesystem inspection found an existing WPS source directory and `package-lock.json`, but **no** `node_modules/`, **no** `dist/index.js` and **no** WPS receipt.
2. Real `runtime.desktop_install_preview`: reviewed `wps-office.source.json` pinned to `lc2panda/wps-skills` at `a82533662268b3245f93d8685bc45dffede048b6`, reviewed Windows COM patch, SHA-256 plan `2e0271d8e90507de16918fde44bc3f96644d5dea147ef0961e41eb1afcd83a6b`.
3. Real `runtime.provider_disable` with explicit INVOKE: completed, `enabled=false`.
4. Real `runtime.provider_setup` with exact reviewed SHA-256 and explicit INVOKE: started job `desktop:wps-office`, PID 32612, without activating the provider.
5. Real `runtime.desktop_install_status`: **FAILED**, exit code `1`, no new receipt. Frozen entrypoint traceback ended in `provider/source_provider_setup.py`, line 290, at `npm ci --ignore-scripts --no-audit --no-fund`. The captured Windows stderr says that the specified path could not be found (encoding garbled).

Confirmed from the installed resources: `D:\PLA Desktop\resources\node.exe` and the complete bundled `node-runtime/node_modules/npm/bin/npm-cli.js` tree exist. The former setup routine discovered `npm.cmd` on the system PATH and executed the Windows batch wrapper from `D:\nodejs`. This is a host-environment-dependent command, inconsistent with the managed packaged Node/npm runtime.

## Source fix in development branch

The Desktop `ComponentInstaller.install` source-package branch now passes its already bundled `node.exe` and `node-runtime/node_modules/npm/bin/npm-cli.js` directly into `setup_git_npm_source`. The latter:

- accepts both managed paths together, rejects missing or partial pairs;
- runs `[managed_node, "--version"]` for the reviewed Node major version check;
- runs `[managed_node, managed_npm_cli, "ci", "--ignore-scripts", "--no-audit", "--no-fund"]`;
- runs `[managed_node, managed_npm_cli, "run", "build", "--ignore-scripts"]`;
- retains `shell=False`, the fixed GitHub URL/pinned SHA, patch checks, package-lock requirement, private component directory, no implicit activation, and legacy system npm resolution *only for standalone/non-Desktop callers*.

This removes the dependence on `npm.cmd` and matches the already working fixed Node/npm invocation used elsewhere by Desktop.

`tests/test_desktop_wps_bundled_npm.py` validates exact argument sequences without executing external binaries, rejection of partial/missing bundled assets, installation receipt creation when the reviewed helper succeeds, and absence of automatic Provider enablement. Focused `pytest` on it and existing source/Desktop tests: **23 passed in 2.23s**.

## Remaining release gate

The installed frozen Runtime cannot be fixed by editing Git source. **Do not claim WPS Ready; keep the existing instance disabled until repaired.** The current PLA execution policy does not permit direct `node`/PowerShell process launches, and it would violate the execution boundary to smuggle those programs through Python.

Rebuild the installer with the reviewed source fix, verify its PyInstaller embedded module, then perform a fresh (or explicitly approved isolated test-user) installation followed by WPS install and actual read-only MCP `wps_common_ping` / `wps_check_connection` calls. The goal is a successful WPS receipt, `node_modules` and `dist/index.js`, live MCP Ready, and no other Provider regression. The new source does not modify the already installed Desktop user data, configuration or WPS documents.
