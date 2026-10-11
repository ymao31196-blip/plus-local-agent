# PLA Desktop RC.11 — requirement and delivery audit

2026-10-11. This is an unsigned local candidate. Full V1 qualification is **INCOMPLETE**. The user owns reinstallation and has now updated D:\PLA Desktop to RC.11. Native and Runtime binaries match this candidate exactly; the installer interaction itself was not observed.

## Candidate identity

- Installer: `dist/desktop-v1/PLA Desktop_1.0.0-rc.11_x64-setup.exe`, 91,897,489 bytes.
- SHA-256: `41423a8b54bce58bdbe146c5d6cea9f1b9070dcef671b90649674a05f17bcbeb`.
- Build source: `fd0448bc8d29092229afdb92101acf9ff5a493a2`; content-clean at build time. Later commits update evidence/documentation only.
- Frozen Runtime SHA-256: `2459e912fa0eaac48a281bbfb72f82709f7a7a4026364b557810e3cc874d0775`.
- Unsigned; automatic updates disabled; no public push or Release performed.

The final desktop rebuild changed two maintenance-page labels and reused this identical, already-verified frozen Runtime. `SkipRuntime` and `SkipAcceptance` are not new Runtime test passes. The earlier full RC.11 build and its actual packaged tests provide the Runtime evidence.

## Evidence inspected

| Evidence | Actual scope and result |
|---|---|
| `.desktop-build/rc11-source-tests.log` | 931 source tests PASS, no warnings, 174.74 seconds; does not prove a clean installation |
| `desktop/verification/component-navigation.cjs` | Actual Edge DOM, simulated RPC; three tabs, combined repository version/integrity/reviewed uninstall controls, draft preservation, search/filter behavior PASS |
| `.desktop-build/packaged-mcp-report.json` | Actual RC.11 frozen payload: MCP discovery, file reads/create/modify, authorized Python process, named-pipe runner, transactional changeset, actual browser Provider, traversal/private-path rejection, stop/restart/persistence PASS; Tunnel and ChatGPT NOT TESTED |
| `C:\Users\26286\AppData\Local\Temp\pla-repository-e2e-kerll6b1\repository-evidence.json` | Frozen Manager stdio RPC and owned worker; Python dependency installation, upgrade, real MCP/Broker calls, enabled-uninstall rejection, disable/uninstall/reinstall and unchanged Runtime PID: 7 PASS |
| `C:\Users\26286\AppData\Local\Temp\pla-node-repository-e2e-05cdd3ti\node-repository-evidence.json` | Frozen Manager, actual isolated npm dependency, Node MCP/Broker call, disable/uninstall/cleanup: 2 PASS |
| `.desktop-build/rc11-build.log`, `.desktop-build/rc11-final-build.log` | Full build and final desktop/NSIS repack both terminated with exit code 0 |
| `.desktop-build/rc11-delivery-audit.json` | 17 local artifact/inventory checks PASS; installer/SHA file match, 77 Python and 229 target-required Rust packages have preserved license texts, 5 MPL source archive checksums match, Node/uv/WebView2/Tunnel notices present, source snapshot absent |
| `.desktop-build/rc11-native-ui/native-ui-report.json` | Harness exited 1 before UI checks: an already-running RC.10 instance intercepted the temporary test launch. NOT a native RC.11 PASS. Computer Use confirmed the user's Runtime is active; it remains running |
| `.desktop-build/rc11-installed-observations.json` | Current user-installed RC.11: matching binaries, native three-tab/removal/starter-pack/merged repository card inspection and actual installation-integrity RPC PASS. Runtime and existing Provider discoveries observed ready. These checks do not replace the full 13-item native lifecycle matrix |
| Current desktop Tunnel/PLA-TEST | FAIL: new client repeatedly returns HTTP 401 `invalid_api_key`; matching PLA-TEST connector reports `tunnel_client_not_seen`. Source plus-local-agent connector succeeds against different roots and cannot qualify desktop E2E |
| RC.10 upgrade and native reports | Historical only: four private-config hashes preserved and native UI 13/13. Do not transfer these passes to RC.11 |

These paths identify retained local evidence. Exported delivery copies include the current machine-readable reports; Temp roots remain available on this test host, not promised on another user's machine.

## Original requirements and remaining gates

| Requirement | Current finding | Evidence or remaining proof |
|---|---|---|
| Existing ChatGPT-native architecture, headless mode, MCP/Broker and permissions retained | Local evidence PASS | Existing source regression and actual frozen MCP/Broker/runner/browser operations; no new model engine or generic Shell UI |
| Independent Windows installer and reproducible build route | Local artifact PASS; installed binary match PASS; first-run/installer UI unobserved | Actual NSIS, locked build script, source manifest and checksums. CI configuration exists; hosted CI and a fresh build machine are not claimed tested |
| First-run wizard, workspace selection, diagnostics and real statuses | Implemented; installed navigation/integrity RPC PASS; full first-run matrix unverified | Source/DOM checks and frozen RPC operations; native installed wizard remains a gate |
| Tunnel setup guidance and real connection | Guidance present; RC.11 control-plane authentication FAIL | Must see fresh remote readiness and real client calls through the new candidate |
| Real ChatGPT tools, account authorization and return path | RC.11 NOT TESTED | Actual ChatGPT evidence through the new Runtime/Tunnel; older candidate conversations are historical |
| Files/read/create/modify, controlled process and complex capability | Local packaged PASS; remote gate NOT TESTED | Frozen protocol tests cover files, named-pipe process, transaction and browser; these are local calls |
| Tray, login opt-in, single instance, crash cleanup and recovery | Implemented; candidate-native gate pending | Source tests plus RC.10 historical installed proof; RC.11 native test did not begin |
| Config/credentials/workspaces preserved across upgrade and uninstall | Implemented; RC.11 install/uninstall NOT TESTED | Existing code and historical upgrade proof; user owns the new installation |
| DPAPI, redaction, minimum IPC, private-data and workspace boundaries | Local regression PASS; complete remote misuse matrix pending | Source tests and frozen traversal/private-path rejects; no security checks disabled |
| Wrong key, network break, Tunnel crash, Runtime crash, port collision, no workspace permission, missing component and repeat launch | Source coverage/partial historical proof; complete RC.11 installed matrix NOT TESTED | Need reproducible observations against the new installed candidate. A simulated fault test alone cannot certify the full matrix |
| Clean Windows 10/11 and OS reboot | NOT TESTED | Need an independently controlled/restartable guest or machine. This active developer host is not a clean-machine pass |
| Browser, Office, Skill availability and optional installation | Partial local proof; optional component limits retained | Current browser and repository components actually tested. Office/Skills prior independent proofs are historical; Skill Library needs the user's matching package because redistribution permission is not established |
| License/dependency disclosure and trusted updates | Inventory PASS; no trusted-public-release claim | Preserved texts/SBOM/archive hashes; unsigned candidate allowed by task, updates disabled. Inventory completeness is not a substitute for an owner's legal/distribution decision |
| Unified MCP/component UI and complete Skill catalog | Source/DOM and installed three-tab/removal checks PASS; full tool workflows pending | Three component tabs, full parameter views, separate Skill List/17 management actions; no duplicate installed tab |
| Latest requested removals | PASS for source and payload | Self-development UI/RPC/source-snapshot removed; starter bundle is six items without DOCX; existing directories/grants and standalone DOCX preserved |

## Deliverables

1. Source and Runtime changes: local `codex/pla-desktop-v1` commits, without public push.
2. Installer, actual path, SHA-256 and source provenance: candidate identity above; `build-manifest.json`, `SHA256SUMS.txt` beside it.
3. Build/CI: `desktop/packaging/build.ps1`, `.github/workflows/desktop-build.yml`; CI configuration present, hosted run unverified.
4. Wizard/use and final acceptance handbook: `docs/desktop-v1-acceptance.md`, updated for RC.11 and the three component tabs.
5. English/Chinese installation descriptions: `README.md`, `README.zh-CN.md`, updated to RC.11.
6. Architecture, permission and dependency boundaries: `docs/desktop-v1-architecture.md`, `docs/provider_repository_protocol.md`.
7. Source/frozen/DOM reports and remaining E2E gaps: this audit, `docs/component_management_repair_status.md`, retained machine reports.
8. Version/release notes/licenses: `docs/desktop-v1-release-notes.md`, delivery `THIRD-PARTY-NOTICES.md` and `third-party-licenses/`.
9. Known limits: this requirement table and handbook; unexecuted tests remain NOT TESTED.
10. User acceptance steps: handbook covers new installation, account setup, actual tools, exceptions, reopen/reboot and uninstall preservation.

## Next evidence required

RC.11 installation and native/Runtime binary comparison are now confirmed. Computer Use inspected the merged page and invoked real installation integrity. Do not reinstall or stop the user's active services automatically. Remote acceptance requires a valid runtime API key: the current saved value still matches the previously supplied test.txt key, without masking or whitespace, but the control plane rejects it. Capture fresh desktop Tunnel/PLA-TEST/ChatGPT calls after valid credentials are saved; do not substitute the separately connected source service. A clean Windows/reboot/uninstall environment and the remaining native lifecycle matrix are separate gates.

Official credential/permission requirements were checked on 2026-10-11 in [OpenAI Secure MCP Tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels): runtime key, matching tunnel and organization Tunnels Read + Use are required. The documentation does not establish that this particular saved key is valid; actual 401 evidence takes precedence.

The overall goal remains unfinished while these gates lack evidence. Producing the candidate and passing local tests does not establish that a new Windows user can complete the full ChatGPT/Tunnel/local-task/restart/uninstall workflow.
