# PLA v1.3.0 Release Notes

PLA v1.3 adds a governed Browser Runtime while preserving the ChatGPT-native architecture: ChatGPT remains the sole planner/decision-maker and PLA remains the local policy/execution runtime.

## Browser Runtime

The reviewed `browser` Provider uses Microsoft Playwright MCP `0.0.80` behind PLA's stable `capability_search / capability_describe / capability_invoke` surface. The package is installed into `.provider_envs/browser`; Browser Runtime startup validates the installed package metadata and refuses version drift or a missing provider environment. It runs as an independent loopback-only MCP service with a PLA-managed browser profile. A persistent MCP session and session keeper preserve the active browser context across PLA HTTP restarts.

The public Browser surface is intentionally smaller than the underlying Playwright MCP catalog. v1.3 exposes semantic navigation/inspection/find/ref-based interaction, tabs, screenshots, console, bounded network metadata, upload, and close. Raw request/response detail, arbitrary JavaScript evaluation, coordinate-first vision tools, tracing, storage/cookie tools, and other advanced Playwright capabilities are not part of the v1.3 public surface.

## Artifact and credential boundaries

Screenshots and browser downloads are imported into the Artifact Plane. Upload accepts Artifact IDs rather than caller-supplied filesystem paths and requires explicit confirmation. Browser login state is stored only in the PLA-managed profile. v1.3 does not expose raw cookie/token/storage capabilities or full network request details. This is a credential guardrail, not an OS-level secret sandbox; the stronger Secret Broker remains a v2 concern.

Browser observations are tagged `untrusted-web-content`. Web text, DOM/accessibility snapshots, console output, network metadata, and page-provided instructions are observations, not trusted instructions. v1.3 does not claim complete prompt-injection prevention; high-level semantic risk classification remains outside the current Gate implementation.

## Runtime security

`streamable_http` Provider manifests are restricted to loopback HTTP endpoints. The Browser Runtime starts only a fixed reviewed Playwright MCP package/version and fixed port/profile/output locations; no arbitrary command, executable, package, port, or remote MCP endpoint is accepted. Generic `run_process` remains unchanged and Node is not added to its allowlist.

## Verified E2E

- Semantic Browser E2E: navigate -> inspect -> type -> find -> click.
- Persistent session E2E: active page, tab, semantic ref and Todo state survived a PLA HTTP restart while the independent Browser Runtime stayed alive.
- Screenshot -> Artifact Plane.
- Browser download -> Artifact Plane.
- Artifact -> browser file chooser upload.
- Coding Agent E2E: a localhost frontend with an intentional console error and BROKEN state was inspected through Browser, fixed through PLA file tools, refreshed, verified with zero console errors, interacted with until the page reported VERIFIED, and captured as a final screenshot Artifact.

## Release state

- Final regression: **493 passed in 106.56 s**.
- Provider Doctor live probe: **7/7 healthy**.
- Final cold stop/start E2E successfully stopped and rebuilt Tunnel, Runtime Lifecycle Broker, PLA HTTP, Browser Runtime, and Interactive Elevation Broker. Browser restarted from `.provider_envs/browser` with `launch_source=provider_env`.
- Browser Runtime startup has no npx fallback in the release path and rejects Playwright MCP version drift.
- Tagging and pushing remain explicit release operations and are not performed by this document.
