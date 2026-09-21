# PLA v1.7.0

PLA v1.7.0 adds governed browser downloads, capability routing/steering, routing coverage audit, and declarative dynamic routing for hot-pluggable providers while keeping ChatGPT as the Agent Brain and PLA as the bounded local runtime.

## Governed Browser downloads

The Browser Provider gains a dedicated `browser.download` capability for authenticated browser-session downloads.

- Download metadata is extracted through a fixed semantic browser helper rather than caller-supplied code.
- Download requests reuse the managed browser session's user agent, referer, and cookies.
- Loopback/private download targets are rejected.
- Size limits and content-integrity checks are enforced before an artifact is accepted.
- Browser download output is returned through the Artifact Plane instead of exposing arbitrary filesystem destinations.

## Capability Steering

PLA now routes overlapping execution paths through a deterministic steering layer.

- PLA-source Git through generic `run_process` is rejected with structured suggestions for the governed Git surface.
- Windows service state changes through generic `run_powershell` are rejected with structured suggestions for the governed service-control flow.
- Routing decisions are explicit: `specialized_required`, `specialized_preferred`, `specialized_recommended`, or `generic_allowed`.
- Steering never silently invokes the suggested replacement; ChatGPT remains responsible for the next action.

## Routing Audit and Catalog

The dynamic read-only `core.routing_audit` capability inspects the complete Capability Registry rather than the bounded search view.

- Static routing rules are checked for catalog/runtime drift and missing target capabilities.
- Cross-provider overlap candidates are classified as `covered`, `reviewed_parallel`, or `uncovered`.
- Reviewed WinGet/software-migration install and uninstall overlaps are retained as intentional parallel capability surfaces.
- Broad unrelated search operations are filtered to reduce duplicate-detection noise.

## Declarative Dynamic Routing

External Provider manifests can now declare routing metadata directly.

Supported relations:

- `preferred_over`
- `fallback_for`
- `supersedes`

Relations may include case-insensitive argument conditions using `contains_any` or `equals_any`.

External providers are limited to `recommendation` or `preferred` routing authority. `enforced` authority remains reserved for PLA built-in policy, so a third-party `supersedes` declaration cannot promote itself to an enforced route.

Routing resolves against the live Capability Registry snapshot on every query. Provider add/change/enable/disable/remove operations therefore update routing after normal hot-rescan without restarting the routing layer.

The Browser Provider is the first production user of manifest-driven routing: seven Browser-over-Computer relationships are declared in `browser-playwright.json` instead of being hard-coded in the steering registry.

## Validation

- Full v1.7.0 split regression: **665 / 665 passed**.
- Python compilation checks passed for capability, routing, provider, browser, artifact, runtime, and server modules.
- Loaded-runtime E2E verified:
  - 116 capabilities across 11 providers;
  - eight external providers ready after HTTP restart;
  - Browser-over-Computer routing resolved through `declared_capability_routing`;
  - static routing catalog reduced to the two built-in enforced rules: PLA-source Git and Windows service writes;
  - seven manifest-declared Browser relations reported as covered;
  - three reviewed parallel install/uninstall overlaps;
  - zero uncovered routing candidates.
- Hot-plug regression verified that a provider routing declaration can be added, disabled, re-enabled, and removed without restarting the routing layer.
- No browser click, Git write, Windows service state change, software installation, or software uninstallation was executed during routing validation.
