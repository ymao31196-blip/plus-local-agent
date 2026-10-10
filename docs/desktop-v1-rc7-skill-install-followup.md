# RC.7 follow-up: installed Provider triage and Skill Library bridge repair

Date: 2026-10-10. This is a **source-only fix**, not an update to the user's currently installed `PLA Desktop 1.0.0-rc.7`.

## Diagnosis from the real installed RC.7 / PLA-TEST

The actual `runtime.provider_catalog` reports 11 manifests, 8 configured/ready and three not configured: `skill-library`, `wps-office`, `winget`. The eight ready providers have previously passed live doctor probes. A successfully parsed manifest means the component is known; it does not establish that its executable or independent environment exists.

- **Skill Library:** `components/.provider_envs/skill-library/Scripts/python.exe` is missing, so no skill capabilities exist yet. The required pinned provider spec is installed as part of resources, but `runtime.desktop_install_preview` in RC.7 accepts only `provider_id`; it fails with `Select the reviewed Skill Library v0.5.0 source or wheel`. The user-owned source exists at `workspace/skill-library` in the original PLA repository, with `pyproject.toml` reporting `chatgpt-skill-library==0.5.0`. The repository's Git state remains clean. Desktop GUI supports the `skill_package` parameter; the ChatGPT Broker did not. This is a missing API link, not evidence of lost user Skills.
- **WPS Office:** node.exe is present, but the provider's source working directory does not exist. The installed RC.7 supports a source-locked `wps-office.source.json` installation plan pointing to the reviewed `lc2panda/wps-skills` Git revision and compatibility patch. Its plan preview succeeds without enabling or installing. The corresponding user-initiated installation must be followed by live discovery and read-only tool verification; merely having Node does not establish readiness.
- **WinGet:** standalone `WindowsPackageManagerMCPServer.exe` is missing and the installed RC.7 has **no** reviewed `winget` installer specification. The plan preview fails accordingly. Installing the operating system's `winget.exe` does not supply that MCP server. A separately reviewed acquisition/build/provenance and licence route is required, or the standalone provider should be clearly marked unsupported/optional. Do not call `provider_enable` until there is an actual executable.

## Repair implemented in source

The ChatGPT component bridge now supports an optional, explicit `skill_package` string on both:

- read-only `runtime.desktop_install_preview({provider_id:"skill-library",skill_package:<absolute local path>})`
- privileged `runtime.provider_setup({provider_id:"skill-library",skill_package:<same path>,expected_sha256:<reviewed hash>})`

The package argument is rejected for other providers and for starter-pack; the existing `ComponentInstaller.plan` validates `chatgpt-skill-library==0.5.0` source/wheel metadata and checks the source hash. The second call must exactly match the previously reviewed plan hash. Setup runs in the dedicated Desktop components directory with its cross-process installation lock; **the result remains disabled until the user explicitly enables the Provider**. The source-only PLA runtime rejects the `skill_package` argument so it does not silently misroute it to the old source installer.

## Actual test evidence

`desktop/verification/chatgpt_skill_install_e2e.py` ran the full real source Desktop Runtime via the stable Broker against a fresh Windows Temp private data root, using only the user's already reviewed v0.5.0 source without changing it. **8 checks passed**:

1. SHA-bound local Skill source plan
2. stale/invalid hash rejected
3. confirmed privileged installation queued
4. actual installer exit 0 and private receipt
5. MCP Provider ready with 17 real tools
6. real Skill source sync and search
7. real SKILL.md content load and exact match
8. unchanged Runtime PID after disable

The actual report is `.desktop-build/rc7-skill-chatgpt-e2e.json`. Unit and regression tests live in `tests/test_desktop_skill_install_bridge.py`.

## Release and permission boundary

No installed RC.7 files or credentials have been changed, and no WPS or WinGet third-party packages were enabled, downloaded or installed in this repair. This source fix must be built into a future candidate (preferably a new RC version to distinguish it from the already installed RC.7). Verify frozen binary and native UI/Tunnel again before release. Existing RC.7 GUI may still install Skill Library by explicitly selecting the local source folder, then independently enabling Skill Library; the ChatGPT-native direct installation flow is repaired only in the new source.
