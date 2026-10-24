# PLA Desktop v1.0.0 — open-source release plan

Decision date: 2026-10-24. State: **PUBLIC SOURCE REPOSITORY RETAINED; LICENSE NOT YET SELECTED; v1.0.0 NOT RELEASED**.

## Scope and repo

- Keep the existing public source, issue, CI and GitHub Releases repository:
  `https://github.com/ymao31196-blip/plus-local-agent`.
- Publish the v1.0.0 installer, its checksum, manifest and release notes from that
  repository on the same source commit/tag. Do **not** create a private-source/
  public-download split repository; the earlier proposal in
  `desktop-v1-distribution-plan.md` was superseded by the owner's newer decision.
- PLA must be explicitly licensed under a recognized open-source license before
  describing v1.0.0 as open source. Merely leaving a GitHub repository public is
  not a license grant. No license or rightsholder name is being fabricated here.

## Recommended license: Apache-2.0 (requires owner selection)

Recommendation: Apache License 2.0 for PLA-owned code if permissive reuse
(including commercial and proprietary reuse), an explicit patent grant, preservation
of license/attribution, and community integrations are acceptable. Use the unmodified
standard `LICENSE` text, a verified copyright holder/year statement where appropriate,
and `NOTICE` only when warranted by actual attributions.

Alternatives:
- MIT: shorter and simpler, still permits commercial/proprietary forks.
- GPL-3.0-only: distribution-triggered reciprocal source-sharing obligations;
  comparatively more packaging/integration review for this mixed Windows
  Desktop/third-party-component ecosystem.
- MPL-2.0: file-level copyleft; relevant if preserving openness of modified
  PLA files matters more than maximizing permissive integrations.

**Owner decision required before adding root `LICENSE`**: pick actual license,
confirm rights to contributed/borrowed code, and confirm copyright holder.
Published permissive license grants generally cannot be retroactively revoked for
already licensed copies; newer versions can use different terms only to the
extent the owner holds the necessary rights.

## Third-party distribution / bundled wheel

An Apache/MIT/GPL root license covers only rights the author actually controls.
Do not pretend it automatically licenses PyInstaller, Python, Node, Tauri, Tunnel,
Office components, providers or optional Skill Library source.

As of 2026-10-24, the packaged reviewed
`chatgpt_skill_library-0.5.0-py3-none-any.whl` has no embedded LICENSE or
license metadata. The nested source also has no top-level LICENSE or project
license metadata. Before v1.0.0:
1. Verify authorship and permission to distribute/relicense this independent
   service, including code provenance; **or**
2. Exclude the wheel from the base installer and revalidate the packaged
   optional-component install workflow, including UI messaging.

Retain the current third-party notices/inventories. Check any inbound borrowed
sources and downstream license obligations independently.

## Stable v1.0.0 release gates

1. Adopt verified license and accurate first-party/third-party notices;
   do not publish the former binary-only EULA as the open-source license.
2. Review source disclosure: no credentials, `*.local.json`, developer secrets,
   private materials or unlicensed redistributed code in tracked files.
3. Finish the user-controlled exe permission tests (grant, disable, regrant,
   revoke, restart persistence, private Desktop bridge, production Runner).
4. Confirm complete source regression, Desktop UI smoke, frozen Runtime/Runner
   acceptance and Windows NSIS packaging on an **exact clean commit**.
5. Verify an independent Windows user's installation, first-time Tunnel/ChatGPT
   invocation, workspace rights, restart/fault recovery, upgrade from RC.18 and
   default uninstall data preservation.
6. Set version consistently in `desktop/package.json`, `desktop/package-lock.json`,
   `desktop/src-tauri/Cargo.toml` and `desktop/src-tauri/tauri.conf.json`, as
   appropriate; update English/Chinese README and CHANGELOG.
7. Attach exact verified installer, SHA256SUMS, build manifest, release notes and
   required third-party notices to a public `v1.0.0` GitHub Release; check that
   source/tag/CI artifact identity matches. Document unsigned/manual-updater
   limitations and limits of executable/root policy.

## Present evidence and constraints

- 986 Python tests passed on the 2026-10-24 developer worktree.
- Packaged PLA-TEST confirmed full-path Node execution, bare-name/shell denial,
  and immediate denial following GUI disable.
- Earlier RC.18 public release has its own successful CI but does not qualify
  the uncommitted/current candidate.
- No clean Windows final acceptance, post-RC.18 upgrade/recovery qualification,
  final clean v1.0.0 build or open-source license selection has been completed.
- Do not publish a stable tag, push without governed permission, change repo
  visibility, or mislabel the build as stable while these gates remain.

See `desktop-v1-stable-readiness.md` for verification details.
