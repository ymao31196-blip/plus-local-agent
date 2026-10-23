# RC.13: Skill Library stale workspace-root permission repair

Date: 2026-10-12. Repository `D:\\AI_Tools\\plus-local-agent`, branch `codex/pla-desktop-v1`.

## Verified field incident (installed RC.12; independently connected PLA and PLA-TEST)

- Developer PLA Skill Library: healthy, enabled, 17 tools, no package drift.
- Installed Desktop PLA-TEST Skill Library: enabled but `error`, 0 tools, `MCPError: Connection closed`. Dependencies fastmcp 4.0.3, mcp 2.2.0, PyYAML 6.0.3 match their pinned versions.
- User's Desktop runtime log (2026-10-12 around 14:10 local) contains the more informative Python startup traceback:
  `ValueError: Skill read roots must match current workspace permissions`
  at `components/providers/skill_library_provider.py` line 44.
- Installed `components/config/skill-library.desktop.json` listed `D, work, workspace` in **both** `local_roots` and `writable_roots`. Current `config/workspaces.local.yaml` lists `D, Desktop, skill-library, my-skills`, plus the built-in private `workspace`. The old name `work` has been removed from workspace registry and is rejected by the provider at import time, before MCP tool discovery.
- The issue therefore arises from dangling **named** Skill permission references when the workspace registry changes. It is not a corrupt package, wrong Skill source directory, or a stale Python process.

## Live mitigation, verified in the installed Desktop instance

Made a narrow, atomic repair of the installed Desktop Skill permission file: removed only the invalid `work` entry from read and write lists, retaining `D` and `workspace` without widening privileges. Original bytes backed up alongside as `skill-library.desktop.before-stale-root-repair.bak`. No Skill source content or cached Skill was modified.

Through `PLA_TEST`, `runtime.provider_reload(skill-library)` succeeded with `state=ready`, `enabled=true`, `tool_count=17`. Direct calls to `skill-library.states`, `skill-library.sources`, and `skill-library.find(include_best_content=true)` succeeded. Five personal Skills in `my-skills` and one prior E2E fixture Skill were present; `academic-writing` loaded with 7,490-character content. Provider doctor became `healthy`, zero failures.

## Durable source repair for next Desktop build

In `src/desktop_runtime/manager.py`, add `Manager._reconcile_skill_permissions` that checks all named Skill permission roots against the current workspace registry before spawning Runtime, after workspace updates/removals, and when showing/editing the Skill permissions. Missing root names and revoked read/write capability are dropped from saved Skill permissions using an atomic update. No names/paths are added, and malformed files are refused and preserved. Only the revoked name lists are logged. The existing provider-side strict fail-closed checks remain intact.

`tests/test_desktop_skill_stale_permissions.py` covers revocation on workspace delete, on downgrading write permission, startup migration after external registry change, malformed permission file preservation, and no-op on valid unchanged grants.

Release version synchronized to `1.0.0-rc.13`; the installed RC.12 remains unchanged until rebuilt and installed by the developer.

## Developer validation and delivery

Run locally in PowerShell; no installation or rebuild has been performed by ChatGPT:

```powershell
Set-Location "D:\\AI_Tools\\plus-local-agent"
python -m pytest -q tests
node .\\desktop\\verification\\components-ux-smoke.cjs
node .\\desktop\\verification\\component-navigation.cjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\desktop\\packaging\\build.ps1"
```

Expected installer: `dist/desktop-v1/PLA Desktop_1.0.0-rc.13_x64-setup.exe`. Before installing for daily use, validate via `desktop/verification/installation.ps1 -Installer ... -AllowSameMachine`, and check run-scoped installation/native MCP/UI reports. Note that the current PLA process execution policy does not allow `node` or arbitrary PowerShell builds; do not bypass the policy.

Avoid silent overbroadening of permissions; if user wants read or write access to a replacement root, they must explicitly grant it in the workspace and Skill permissions UI.
