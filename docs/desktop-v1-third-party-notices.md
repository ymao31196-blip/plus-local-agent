# PLA Desktop candidate — dependency notices

This local delivery preserves the component texts, inventories and relevant source archives. It does not establish approval for a signed public release or select a public license for the owner's first-party PLA source.

| Component | Pinned input and evidence |
| --- | --- |
| Tauri / single-instance | 2.12.2 / 2.5.2, Cargo.lock; per-crate license metadata and copied texts in third-party-licenses/runtime |
| Frozen Python Runtime | Python 3.11, FastMCP 4.0.3, MCP 2.2.0; python-distributions.json and distribution notice texts |
| Standalone execution Python | Official Python 3.11.9 Windows embeddable archive; PSF notices in PYTHON-EXECUTION-LICENSE.txt |
| Node / npm | Official Node 22.16.0 Windows archive, NODE-LICENSE.txt; bundled npm package license files retained in node-runtime |
| Playwright MCP | 0.0.82, exact npm versions and SRI hashes in browser-component/package-lock.json; installed package texts preserved |
| Secure MCP Tunnel | Official runtime-cloudflared 0.0.16; Apache LICENSE/NOTICE, release dependency-license report and SPDX in third-party-licenses/tunnel |
| cloudflared | Companion 2026.8.2 pinned by the Tunnel release's manifest and dependency reports |
| WebView2 loader/runtime | Microsoft SDK1.0.3800.47 LICENSE/NOTICE; system Evergreen runtime and official signed bootstrapper |
| PyInstaller | 6.16.0 build tool/bootloader; distribution license and bootloader redistribution exception preserved |
| Required Cargo dependencies | Actual Windows build target's normal/build set identified in rust-resolved-dependencies.json; missing required license evidence fails the build |
| MPL dependencies | Original cssparser, cssparser-macros, dtoa-short, option-ext and selectors source archives, lock-file hashes and source URIs in mpl-source-archives.json and mpl-sources |
| Supplemental notices | Known omitted wheel/crate texts from version-specific reviewed upstream assets with fixed hashes; British LICENCE names and nested notices are included |
| uv optional installer | Official uv0.12.24 is downloaded on demand with archive/executable hashes; its binary is not redistributed in the base installer. MIT/Apache texts retained |
| Optional Provider packages | Fixed provider_specs describe separately installed components; installation does not make them part of the base redistributed payload. Git-based sources use fixed reviewed revisions |
| Skill Library | User-owned v0.5.0 source/wheel only; absent established redistribution permission, the independent service repository and personal Skills are not bundled |

The Cargo inventory includes non-target packages for reference and explicitly identifies the target's required normal/build set. That set conservatively includes build dependencies and does not claim every entry is linked into the runtime binary. Original source archives are provided unchanged. Third-party texts remain authoritative over this concise inventory.

System Windows DLLs are not copied into this installer. Windows Authenticode and future Tauri updater signatures are independent and unconfigured; automatic updates remain disabled. No public-release qualification is claimed.
