# PLA Desktop v1.0.0 — open-source release plan

Updated: 2026-10-24. Status: **Apache-2.0 selected and applied in the local worktrees; v1.0.0 NOT RELEASED**.

## Distribution and ownership

- Keep the current public PLA source repository, CI and GitHub Releases:
  `https://github.com/ymao31196-blip/plus-local-agent`.
- PLA's **original first-party source** is licensed under Apache License 2.0, using the
  official unmodified license text at repository-root `LICENSE`. Windows binary
  releases include a copy of this license and independently required third-party notices.
- The independently developed **Skill Library** has also been licensed under
  Apache-2.0 in its own Git repository; the bundled Python Wheel now includes
  `dist-info/licenses/LICENSE` and PEP 639 metadata
  `License-Expression: Apache-2.0`.
- Neither of these licenses overrides third-party copyrights, transitive
  dependencies, external Providers, independently retrieved Skills or bundled
  components authored by others. Their own copyright/NOTICE and license conditions
  must be honored. Validate provenance for any copied or modified third-party code
  included in PLA's first-party directories.
- Rightsholder identity/attribution has **not** been invented. Before the final
  release, confirm proper copyright holder information, earlier contributor grants,
  and any additional NOTICE obligations.
- The previously proposed private-source/public-download split and proprietary
  binary EULA have been withdrawn. Those drafts remain archived and must NOT be
  presented as the current legal terms.

## Skill Library reviewed package (license-only source update)

- Independent source repository commit:
  `13cc22435152d475e72e4cfe1aa0dc4e0657d16f` (local, not pushed yet).
- Package: `chatgpt_skill_library-0.5.0-py3-none-any.whl`.
- Reviewed SHA-256:
  `869bd376af7c729172283017b36c694ad0fc128c5d54ffa7a897ce4b4d3f1eb0`.
- Existing RC.18 used a prior, separately hashed v0.5.0 Wheel. New v1.0.0
  release must use the new reviewed SHA consistently in
  `desktop/packaging/prepare_skill_library.py`,
  `src/desktop_runtime/installer.py`, the reviewed artifact and documentation.
  No old RC release is retroactively changed.
- The reviewed Wheel was built from a clean source snapshot twice and both
  builds yielded the same hash; the independent source test suite completed
  `76 passed, 2 skipped` on 2026-10-24.
- Optional dependency installation and existing RC upgrades still require
  integrated packaged acceptance before v1.0.0.

## Remaining v1.0.0 gates

1. Verify copyright ownership and review copied/bundled third-party materials
   and notice obligations, beyond adding Apache-2.0 for the two owned projects.
2. Inspect tracked files and Git history for credentials and private materials
   before publishing any new refs. Avoid printing secret values in release logs.
3. Rerun the full PLA Python regression, native Desktop UI smoke,
   frozen Runtime/Runner/Skill Library packaging and installer build on an exact
   **clean, committed** source revision (manifest `source_dirty=false`).
4. Complete user-side permission tests (grant, disable, re-enable, revoke,
   restart persistence), clean Windows installation without dev tools,
   Tunnel/ChatGPT remote call, RC.18 upgrade, restart/error recovery, uninstall
   data preservation and check that optional extensions cannot break core startup.
5. Update the v1.0.0 version fields consistently (Tauri/Cargo/npm lock), English
   and Chinese README links, CHANGELOG, source commit/build manifest and
   release notes. Do not point the current download links to a nonexistent release.
6. Publish the verified `v1.0.0` source tag and installer from the *same*
   public GitHub repository with matching checksum, license and dependency
   notices. The installer remains unsigned and manual-update only.
7. After public release, confirm anonymous installation asset access and the
   SHA-256 on GitHub match the locally verified file.

## Evidence and release policy

- Prior complete PLA regression: `986 passed` on 2026-10-24 before
  these licensing/pinning changes; a new full run is required.
- Packaged PLA-TEST previously verified exact-path executable grants and immediate
  refusal following disable; outstanding E2E coverage is tracked separately.
- This is **release preparation**, not v1.0.0 approval. Do not issue a stable tag
  or Release before the acceptance gates are satisfied.

See [`desktop-v1-stable-readiness.md`](desktop-v1-stable-readiness.md) and
[`desktop-v1-acceptance.md`](desktop-v1-acceptance.md).
