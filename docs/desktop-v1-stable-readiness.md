# PLA Desktop v1.0.0 — release freeze and qualification ledger

Date: 2026-10-24. Status: **RELEASE FROZEN / NOT YET QUALIFIED**.
This is a working qualification record, **not** approval to publish.

Updated distribution decision (2026-10-24): **publish PLA as open-source software through the existing Public source repository**, with the Windows installer attached to its regular GitHub Release. An actual open-source license and rights review are still outstanding. The earlier binary-only/private-source plan was withdrawn. See [`desktop-v1-opensource-release-plan.md`](desktop-v1-opensource-release-plan.md).

## Stability contract (v1.0.0)

The guaranteed surface is the Windows x64 Desktop installer, configuration and lifecycle
manager, Secure MCP Tunnel forwarding, core MCP/Runtime, Execution Runner,
user-controlled workspace/executable permissions, diagnostics and basic file/process
execution. Claims apply to supported configurations and must be backed by actual tests.

Browser, Office, Skill Library, desktop companion and other third-party MCPs are
**optional extensions**; their individual operations are outside the v1.0.0 stability
guarantee. Optional components must still be isolated: failure to install or load an
extension must not prevent the core Runtime from starting or degrade its permissions.

Windows Authenticode signing and automatic updates are **not** supported for v1.0.0;
disclose unsigned installers, manual upgrades and lack of OS-level sandboxing.
External ChatGPT and Tunnel account eligibility remain third-party prerequisites.

## Evidence obtained

- Local **full Python regression**: **986 passed in 165.99 s**, 2026-10-24;
  `python -m pytest -q --tb=short`, from the current developer worktree.
- Live packaged PLA-TEST Runtime/production Runner: adding a full-path
  `D:\\nodejs\\node.exe` grant allowed `--version` and a real `-e` JavaScript
  execution (exit code 0); the bare `node.exe` name and `powershell.exe` remained
  rejected; after the user disabled the grant in the Desktop GUI, the same full path
  was immediately rejected without restarting Runtime. These tests were completed
  in the ChatGPT conversation on 2026-10-24.
- The user independently reported a successful local Desktop rebuild and visible
  GUI. The inspected local `dist/desktop-v1/build-manifest.json` remains
  **v1.0.0-rc.18**, `source_dirty=true`; this is a developer candidate and not a
  clean, publishable release artifact.
- Previous RC.18 CI passed 972 Python tests and the Windows Desktop packaging job;
  that evidence applies to the earlier published commit, **not** to v1.0.0.

## Release-blocking gates — verify on final exact source commit

1. **Immutable candidate**: approved source diff, no private data in Git, full CI
   including GUI smoke, frozen MCP/Runner acceptance, NSIS artifact, clean
   build manifest (`source_dirty=false`), source commit, artifact checksum.
2. **Fresh Windows setup**: install without a developer checkout/Node/Python/Rust,
   configure and start Runtime/Tunnel, authorize the MCP connection in ChatGPT,
   read/write an isolated test workspace and execute a harmless allowed program.
3. **Lifecycle and upgrades**: clean tray exit, restart, reconnect after Tunnel
   interruption, no accidental orphan process, upgrade from RC.18 with workspace
   grants and DPAPI-protected credentials intact. Verify ordinary user data and
   authorized external workspaces survive a default uninstall.
4. **Permissions**: new grant, blocked bare-name and unapproved program, disable,
   re-enable, revoke, Desktop restart persistence and Runner enforcement. A revoked
   grant affects future requests, not an already-running process.
5. **Distribution rights**: choose and publish an explicit first-party PLA source
   license; audit dependencies and redistributable optional packages. The bundled
   `chatgpt_skill_library-0.5.0-py3-none-any.whl` currently has no LICENSE or
   license metadata inside its wheel. Its distribution authorization must be
   established by its rightsholder or the wheel removed from the published base
   installer. A fixed SHA-256 does **not** establish redistribution rights.
6. **Release documentation**: update English and Chinese READMEs, support limits,
   changelog, version fields, download links and SHA-256, then publish a tag and
   release corresponding to the verified clean commit.

## Outstanding evidence / blockers (2026-10-24)

- No clean-machine, cold reboot, final-candidate upgrade/uninstall or full fault
  recovery acceptance has been recorded yet.
- Re-enable, revoke and restart-persistence have automated tests, but no
  independent **packaged GUI + remote ChatGPT** manual evidence yet.
- The standalone PLA repository currently has no root LICENSE.
- The optional Skill Library v0.5.0 wheel's license metadata and license files
  are absent; owner/distribution decision required.
- Feature and release-freeze documentation changes are committed locally at
  `aa3d3e9005800ff71565a9bf172be5a43bc800bd`, not yet pushed.
  A stat-only Cargo.toml change remains. The RC.18 version fields have
  **not** yet been changed to v1.0.0; no clean final-candidate build exists.

## Freeze policy

No new capability families, generalized shells, UI redesign, updater, signed
distribution, or optional-plugin expansion before v1.0.0. Only fixes required by
the above gates are in scope. Do not mark PASS on historical evidence, publish
`v1.0.0`, or push a stable tag until the final candidate passes all release
gates and the owner approves its distribution terms.

For the detailed existing installation checklist see
[`desktop-v1-acceptance.md`](desktop-v1-acceptance.md).
