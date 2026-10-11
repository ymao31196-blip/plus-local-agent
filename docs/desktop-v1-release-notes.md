# PLA Desktop 1.0.0-rc.11 — unsigned local test candidate

RC.11 merges repository components and existing MCP Provider controls into one **组件与 MCP 插件** entry with three tabs: 本机组件、仓库管理、浏览组件. Existing live status, enable/disable/reload, manifest editing, tool allowlists and complete capability schemas remain available. Skill List, Skills Management and multiple workspace roots remain supported. The optional self-development UI, RPCs and bundled source snapshot are removed at the user’s request. Existing source directories and workspace grants are preserved. DOCX is excluded from the six-component starter bundle and remains available separately.

Repository installation now uses the private management RPC allowlist, complete verified package snapshots, SHA-256-bound review and confirmation, and a fixed owned background worker. Confirmed requests return a job promptly rather than blocking the native management bridge for dependency installation. Python and Node dependencies use isolated version directories. Installation never auto-enables a provider.

Upgrades preserve previous-version ownership. Uninstall rejects modified registered files, preserves unrelated files, verifies ownership and stages reversible changes before committing state. Explicit Provider disable/reload/removal now closes the owned stdio transport even without a persistent Client, releasing Windows interpreter locks.

Actual source-mode Python and Node repository install/Broker-call/uninstall checks passed in fresh private Temp directories. Python upgrade/reconnect and enabled-uninstall rejection also passed. Browser navigation tests passed for three tabs and merged repository integrity/uninstall controls, preserved manifest drafts and original controls. Full source regression and historical RC.10 frozen, native UI and NSIS acceptance are recorded in [the repair status](component_management_repair_status.md); pending checks are not inferred from earlier releases.

Limitations: only local file and HTTPS repository URLs are supported; Git sources and signed indexes are not implemented. Checksums bind reviewed content but do not authenticate a publisher. Dependency isolation is not an OS sandbox. Exact PyPI/npm dependency pins are required; npm lifecycle scripts and repository executable hooks are disabled. File health checks are installation-integrity checks; actual connection status is read from Runtime.

This is an unsigned local test candidate with automatic updates disabled. Existing data and credentials are retained on upgrade/uninstall. Clean Windows10/11, OS reboot, public signing and distribution require separate evidence; no public push or Release is authorized. Skill Library still requires the user's matching source/wheel because redistribution permission is not established.

The user will reinstall the application; no RC.11 installation is performed automatically.

Expected installer: `PLA Desktop_1.0.0-rc.11_x64-setup.exe`. Actual SHA-256 and source revision are emitted into SHA256SUMS.txt and build-manifest.json beside the built installer. See the final validation record for the actual result.

## Historical RC.5 notes

# PLA Desktop 1.0.0-rc.5 — unsigned local test candidate

RC.5 exposes the complete live capability catalog through the existing capability_invoke wrapper as runtime.capability_catalog. Clients which do not expose the additional standalone wrapper can still retrieve full schemas and availability without the 100-item search bound. This read-only path preserves the original Broker and policy checks.

The license packager now preserves British LICENCE names and nested notices, supplements known omitted wheel/crate texts from version-specific reviewed hash-bound assets, checks required target dependency evidence, and includes the five MPL dependencies' original checksum-verified Cargo source archives. Delivery notices are refreshed from the actual build.

RC.4 introduced full Provider management/import/configuration/hot-plug, independent optional component installation, the separate cached Skill List alongside 17 Skills Management workflows, and opt-in source self-development. RC.3's multi-root add/edit fix is retained. No separate agent engine is added.

844 source tests passed on the RC.5 code. A real license audit preserved texts for77 Python distributions and229 normal/build Cargo dependencies required by the Windows GNU target, plus5 MPL source archives with valid lock-file checksums. RC.5 packaged/install/ChatGPT verification is recorded separately as it executes; older RC.4 passes are historical evidence, not automatically substituted.

RC.4 actual ChatGPT Provider-directory/file/process/transaction acceptance passed, along with its frozen MCP, Skill service, authenticated network-loss/recovery and native Skill List checks. See [the RC.4 record](desktop-v1-rc4-followup.md).

This release is not fully qualified. Clean Windows10/11, actual OS reboot and complete tray-menu acceptance remain open. Skill Library lacks established redistribution permission and requires the user's own matching v0.5.0 source or wheel. Optional missing dependencies, including Office artifact-tool, remain explicitly unavailable. No developer environments or personal Skills are imported.

The installer is unsigned; automatic updates are disabled. Windows Authenticode and Tauri updater signing are separate and unconfigured. Manual upgrade retains user data and requires verifying source and SHA-256 before running the reviewed installer. No public push, Release or hosting is performed.

Installer: `PLA Desktop_1.0.0-rc.5_x64-setup.exe`. The authoritative SHA-256 and exact source commit are emitted into `SHA256SUMS.txt` and `build-manifest.json` alongside the installer; the final build result is recorded in its verification report. Earlier candidate packages and evidence remain separate.
