# Desktop RC.8 component-install UI repair — 2026-10-10

Context: user screenshot showed `wps-office error` with the misleading action `启用并连接`, and Skill Library install form repeatedly asked to `预览固定依赖` even though no Skill Library source path had been entered.

## Observed actual installed state (read-only PLA-TEST diagnostics)

- `wps-office`: configured, Node exists, lifecycle `state=error`, `MCPError: Connection closed`, 3 failures, 0 tools; recorded install receipts contain only the seven starter-pack providers, not WPS. A working directory can exist despite unfinished source/npm installation. The exact cause of the process exit is not yet proven; source package logs must be checked on the installed machine.
- `skill-library`: its isolated `Scripts/python.exe` is absent, `configured=false`, and the screenshot left the necessary source/wheel input blank. User-owned reviewed v0.5.0 source was previously confirmed at `D:\AI_Tools\plus-local-agent\workspace\skill-library`; no automatic bundling of that source is assumed.

## Implemented source changes

- One primary `安装 / 修复` action transparently regenerates a plan with the existing `provider_install(confirmed=false)`, shows a **single explicit confirmation** describing provider, pinned dependency contents, source, target and SHA-256, then starts `provider_install(confirmed=true,expected_sha256=plan.sha256)`. This retains the broker's race/stale-plan checks; cancellation makes no install changes. `查看依赖详情（可选）` is no longer a gate. A currently running installer blocks concurrent starts.
- Already enabled providers are explicitly identified in the plan confirmation. Following consent, the GUI disables the provider using the existing protected management operation before starting the installer; it never silently re-enables it.
- Starter-pack (7 components) follows the same one-click review-and-confirm pattern.
- Provider card explicitly distinguishes `缺少运行组件`, `连接失败` (including actual `state=error`) and `MCP 已连接`. Connection errors are visible outside the collapsed advanced panel. A broken WPS card offers retry and direct repair, not a deceptive `启用并连接`.
- Backend WPS presence check now also requires `dist/index.js` and `node_modules` in its reviewed working directory before declaring `execution_files_present` true. `provider_action(enable)` refuses an obviously incomplete WPS install. This test does **not** prove these two assets explain the current process failure; it rules out a common false positive.
- Skill Library still needs the user's actual v0.5.0 source or wheel path. Nothing changes or copies private Skill repositories without user authorization.

## Tests and limitations

- Python test contract `tests/test_desktop_component_ui_flow.py`, WPS fixture regression in `tests/test_desktop_install_usability.py`, and expanded JavaScript DOM smoke in `desktop/verification/components-ux-smoke.cjs`.
- The local PLA tool policy does not allow direct `node`, so the updated JavaScript smoke must be run in the trusted developer terminal: `node desktop/verification/components-ux-smoke.cjs`. Native WebView2/Tauri behavior and installed binary must also be tested after packaging.
- Only RC.8 **development source** was edited. The currently installed RC.7/RC.8 application, personal configuration, WPS documents and Skill sources have not been changed; this UI fix requires a new build before visible to the user.
