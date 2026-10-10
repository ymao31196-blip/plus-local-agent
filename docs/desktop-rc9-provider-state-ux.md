# PLA Desktop 1.0.0-rc.9 — Provider enablement & Skill Library clarity

Date: 2026-10-10. Source branch: `codex/pla-desktop-v1`.

## Root cause found through actual MCP calls

Current **PLA-TEST** installed Desktop runtime:

- `skill-library` source environment exists and pinned dependencies match; 17 allowed MCP tools discovered. Its lifecycle had `state=ready` but `enabled=false`, with `temporarily_disabled=true`. This is a historical successful discovery, NOT current availability. A real `skill-library.states` call failed `Capability is unavailable: skill-library.states`.
- A real confirmed `runtime.provider_enable` returned `state=ready`, `enabled=true`, `tool_count=17`. Following that, real `skill-library.states`, `skill-library.sources`, and `skill-library.find` calls all completed without tool errors. The installed Desktop Skill Library has **zero configured sources, zero skills, zero search results**. Installation does not import personal Skills.
- The development **PLA** is a distinct MCP node, with a configured user-owned `my-skills-local` source and 5 enabled local Skills; a real `skill-library.find` with `include_best_content=true` found `academic-writing` and returned the full `SKILL.md` via `loaded.content`. This proves both discoverability and Skill loading, but only for the separately configured dev PLA. No user Skill files were changed.

The original GUI had treated `lifecycle.state==='ready'` as enough to display `MCP 已连接` and omit the quick enable action, even when `lifecycle.enabled===false`. The cached Provider `ready` observation outlives a manual disable, leading to a confusing raw capability-unavailable error.

## UI changes

- Every Provider card always shows two independent at-a-glance indicators: `启停：已启用/已停用/待连接/未启用` and `连接：MCP已连接/未连接/连接失败/缺少运行组件` as supported by actual state. This applies to **all** Provider cards and does not conflate activation with a successful connection.
- Connected or enabled Providers have a visible `停用` quick action; installed inactive Providers have `启用并连接`. Both use existing confirmed `provider_action` and refresh the actual catalog. Failed-but-enabled Providers keep retry/repair controls and can be disabled without expanding advanced management.
- A failed or disabled Skill Library read has actionable explanation instead of displaying the raw `Capability is unavailable` message.
- The Skill Library install link points to its Provider card when the executable already exists, rather than incorrectly instructing an installed user to install again.
- If a connected Skill Library has no configured sources, the Skill List explains that the user must register a Skill directory and sync; installation of the Library program alone does not import the user's Skills.

## Test and delivery gates

- Enhanced `desktop/verification/components-ux-smoke.cjs` mocks enabled, disabled-but-historical-ready, failed, and missing-component states; checks header labels and the quick enable/disable transitions, as well as empty-source / disabled Skill views.
- `desktop/verification/native-ui.cjs` now checks EVERY Provider card in real WebView2 for both indicators. This requires the **new RC.9 installer** (RC.8 remains unchanged and previously accepted).
- Python static/regression coverage in `tests/test_desktop_component_ui_flow.py`. Version synchronized across Rust, Tauri, npm, Python, and lock files as `1.0.0-rc.9`, preserving previous RC.8 installer hash and evidence.

**Not yet performed:** Node UI smoke on actual developer Windows; RC.9 frozen app/NSIS build; RC.9 native WebView2/ChatGPT Tunnel full acceptance. The PLA runner's program allowlist does not admit `node.exe` or arbitrary PowerShell builds, and this boundary is not bypassed by source changes. The installed Desktop UI will only show the updates after an authorized developer build and installation.

The provider enable action invoked directly through `runtime.provider_enable` is a current-process hotplug, not a persistent user preference. For durable enablement, use Desktop's `启用并连接` action, which calls the Manager's `provider_action` and saves Provider selection. Keep dev PLA and PLA-TEST state separate.
