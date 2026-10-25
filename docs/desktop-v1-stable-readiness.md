# PLA Desktop v1.0.0 — release freeze and qualification ledger

Date: 2026-10-25. Status: **RELEASED — owner waived the independent clean Windows and full reboot/GUI permission-persistence acceptance gates**.
This ledger preserves pre-release findings and pending test recommendations. The waiver does not constitute a PASS; tagged source `ce6b9b1` passed [Windows CI](https://github.com/ymao31196-blip/plus-local-agent/actions/runs/40173118669) and the stable Desktop release is [`desktop-v1.0.0`](https://github.com/ymao31196-blip/plus-local-agent/releases/tag/desktop-v1.0.0).

Updated distribution decision (2026-10-24): **publish PLA as open-source software through the existing Public source repository**, with the Windows installer attached to its regular GitHub Release. PLA and the independent Skill Library now have local Apache-2.0 LICENSE files; borrowed-code attribution and third-party distribution verification remain open. The earlier binary-only/private-source plan was withdrawn. See [`desktop-v1-opensource-release-plan.md`](desktop-v1-opensource-release-plan.md).

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

- Earlier full Python regression: **986 passed in 165.99 s**, 2026-10-24.
- After Apache-2.0 and Skill Library Wheel updates: **988 passed in 189.29 s**
  (`python -m pytest -q --tb=short`); after the final installer license
  assertion was added, the release-metadata focused suite passed **3/3**.
- Third-party provenance preflight on 2026-10-25: the WPS compatibility patch
  now carries its pinned upstream MIT copyright/license notice and the focused
  license/provenance checks passed **7/7**. PyMuPDF's optional AGPL/commercial
  terms require a separate downstream integration review. See
  [`desktop-v1-third-party-audit.md`](desktop-v1-third-party-audit.md).
- Independent Skill Library source suite: **76 passed, 2 skipped**.
  The reviewed Wheel was rebuilt twice from the clean source snapshot with
  identical SHA-256 `869bd376af7c729172283017b36c694ad0fc128c5d54ffa7a897ce4b4d3f1eb0`.
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
5. **Distribution rights**: keep the Apache-2.0 license for owned source,
   audit borrowed code and redistributable optional packages. The updated
   `chatgpt_skill_library-0.5.0-py3-none-any.whl` now contains LICENSE and
   License-Expression: Apache-2.0. The first-party license does not automatically
   grant redistribution rights to any separately borrowed sources or dependencies
   inside the installation; review these independently.
6. **Release documentation**: update English and Chinese READMEs, support limits,
   changelog, version fields, download links and SHA-256, then publish a tag and
   release corresponding to the verified clean commit.

## Outstanding evidence / blockers (2026-10-24)

- No clean-machine, cold reboot, final-candidate upgrade/uninstall or full fault
  recovery acceptance has been recorded yet.
- Re-enable, revoke and restart-persistence have automated tests, but no
  independent **packaged GUI + remote ChatGPT** manual evidence yet.
- PLA root LICENSE has been added from official Apache-2.0 text (local, unpushed).
- The reviewed Skill Library wheel now carries Apache-2.0 license metadata and file.
  Its independently licensed third-party dependencies still require review.
- Third-party attribution/source audit is recorded at local commit
  `b75a558b71ee0ea57161a8b21af44395a8f2dee0` (not yet pushed).
  Local Desktop candidate version fields are now set to 1.0.0, but no
  clean final NSIS installer or public v1.0.0 Release exists.

## Freeze policy

No new capability families, generalized shells, UI redesign, updater, signed
distribution, or optional-plugin expansion before v1.0.0. Only fixes required by
the above gates are in scope. Do not mark PASS on historical evidence, publish
`v1.0.0`, or push a stable tag until the final candidate passes all release
gates and the owner approves its distribution terms.

For the detailed existing installation checklist see
[`desktop-v1-acceptance.md`](desktop-v1-acceptance.md).
