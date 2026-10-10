# PLA Desktop RC.4 verification — 2026-10-10

The new Skill List preserves the existing Skills Management page. It automatically reads every cached entry through the original `skill-library.states` and `sources` APIs, including disabled Skills and disabled sources. Name/ref/source text, source and state filters operate on the complete returned cache inventory. Enabled entries can load their complete SKILL.md; management buttons preserve the selected source/name and use the original service workflows and permission checks. Unsynchronized sources are explicitly distinguished from cached Skills.

## Candidate

- Source: `578f9a0da9928c06b97c2d9269f39d2e79b99021`; build source_dirty=false.
- Installer: `D:\AI_Tools\plus-local-agent\dist\desktop-v1\PLA Desktop_1.0.0-rc.4_x64-setup.exe`, 92,547,687 bytes.
- SHA-256: `28b4da3fc6baeb7bd38adeee8af01eb8e401c17e33f6a46dd0707e99743e7ae0`.
- Unsigned local candidate; automatic updating disabled; no public push or release.

## Executed evidence

| Check | Grade | Evidence |
| --- | --- | --- |
| Full source regression before the final UI addition | PASS | 841 tests, 177.02 seconds; expanded-source-tests.log |
| Final UI JavaScript syntax and diff checks | PASS | Node --check for both scripts; git diff --check |
| Frozen base MCP discovery/files/processes/transactions/browser/lifecycle | PASS | 10 checks, packaged-mcp-report.json, repeated by final build |
| Independent frozen optional install and Skill service | PASS | 8 groups, rc4-components-frozen-report.json: official UV download with both hashes, managed Python, user-owned package, 17 descriptors, resource/full text, lifecycle/drafts/large Unicode/publication local copy, PID preservation |
| Frozen failure and security scenarios | PASS | 7 executed checks, rc4-packaged-faults-report.json; network scenario graded separately |
| Genuine authenticated network loss and recovery | PASS | 4 checks, rc4-authenticated-network-report.json; dedicated CONNECT gate, TLS not intercepted, OS settings unchanged |
| Native Skill List GUI | PASS | Computer Use: clear stopped-service message; two real cached entries from two sources; complete text read; test: filter reduced to one entry; selected name/source carried into management; real disable remained visible and hid read action; enable restored |
| NSIS upgrade to existing D:\PLA Desktop | PASS | Installer exit 0; desktop.json, workspaces.local.yaml and tunnel.secret hashes unchanged |
| Ordinary Windows installed launch and recovery | PASS | Opened existing executable in Explorer; real Runtime18766 and Tunnel18082 remote polling recovered |
| Existing developer services preserved | PASS | 8766/8931/18081 PIDs30744/19464/20620 retained |
| RC.4 actual ChatGPT added-capability calls | NOT TESTED | Earlier RC.2/RC.3 ChatGPT file/process/transaction evidence remains historical, not RC.4 proof |
| Clean Windows and actual OS reboot | NOT TESTED | Current workstation cannot establish these results |

Native evidence: `.desktop-build/user-evidence/rc4/skill-list-full-content.jpg`, `skill-list-filter.jpg`, `skill-list-disabled.jpg`. Test Skills were isolated fixtures; no personal Skill import or remote publication occurred.

Directly launching the installed application from the packaged Codex tool environment caused new user-data subdirectories to resolve into Codex's LocalCache overlay, and containment correctly rejected that mixed physical/virtual path. Relaunch through the ordinary Windows Explorer process restored the normal private-data layout and successful service startup. The check was not weakened. Test launches should use isolated Temp data or the ordinary installed Windows launcher; this tool-environment limitation is recorded instead of claiming universal launcher compatibility.

## Remaining qualification

Skill Library has no established redistribution license; only a user's own matching v0.5.0 package is accepted by the optional installer. Office artifact-tool is not borrowed from Codex or bundled and remains unavailable without an independent dependency. Git/Rust prerequisites are observed for relevant optional workflows. Clean Windows, reboot, code signing and RC.4 ChatGPT added-capability acceptance remain open. This is not the completed trusted public Desktop V1 release.
