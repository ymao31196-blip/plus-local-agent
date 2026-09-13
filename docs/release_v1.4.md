# PLA v1.4.0 Release Notes

PLA v1.4 adds a reviewed Windows Computer Use capability layer while preserving the ChatGPT-native Agent Loop established in v1.

## Release scope

v1.4 extends PLA from browser-only graphical interaction to controlled Windows desktop application interaction.

The production boundary remains:

```text
ChatGPT = reasoning / planning / capability selection / result verification
PLA     = local execution / policy / provider isolation / artifacts / events
```

Desktop UI content is observation data, not authority.

## Computer Provider

The new `computer` Provider is backed by Microsoft winapp CLI `0.5.0`, pinned in a provider-scoped environment.

The reviewed public surface contains 17 capabilities:

```text
computer.status
computer.list_windows
computer.inspect
computer.search
computer.property
computer.value
computer.focused
computer.wait
computer.screenshot

computer.invoke
computer.set_value
computer.focus
computer.reveal
computer.scroll

computer.click
computer.type
computer.press
```

The preferred interaction order is semantic-first:

```text
inspect/search
    -> UIA semantic selector
    -> invoke/set_value/focus/scroll/wait
    -> observe result
    -> selector-targeted input fallback only when required
    -> observe result again
```

## Restricted input fallback

Real input injection exists only behind reviewed semantic targets.

- `computer.click` accepts a semantic selector, never arbitrary screen coordinates.
- `computer.type` sends literal text to a semantic selector and internally uses `send-input` because WinUI/UWP/XAML controls may ignore posted messages.
- text input is capped at 4096 characters and chunked into bounded 128-character writes.
- `computer.press` accepts only a small allowlist of navigation/edit keys.
- callers cannot select the input transport.
- system/global shortcut grammar and `--allow-system-keys` are not exposed.

The following upstream capabilities remain outside the v1.4 public surface:

- arbitrary coordinates
- arbitrary send-keys grammar
- system/global shortcuts
- drag
- touch
- pen
- video recording
- full-screen capture
- arbitrary winapp subcommands
- arbitrary shell/process execution

## Screenshot Artifact boundary

Computer screenshots are target-window/element PNG outputs only.

The Capability Broker allocates the output inside the Artifact Plane under the invocation-scoped `.capability_io` directory. The Computer Provider rejects caller-selected filesystem paths. Successful captures are imported as immutable short-lived Artifacts and returned by Artifact ID; the managed local path is sanitized from the model-visible result.

## Reviewed Provider dependency setup

v1.4 introduces:

```text
runtime.provider_setup
```

This privileged capability requires explicit `INVOKE` confirmation and can only install dependency specs already present under:

```text
provider_specs/<provider>.txt
provider_specs/<provider>.npm.txt
```

It runs the fixed repository-owned `setup_providers.ps1 -Provider <id>` entrypoint with `shell=False`. It does not accept arbitrary commands or script paths.

`setup_providers.ps1` now supports targeted installation of one or more reviewed providers and correctly handles providers, such as `computer`, that have both Python and Node dependency specs.

## Trust and security boundary

Window titles, UIA names, values, application text, Electron/WebView content, and screenshots are tagged/treated as untrusted UI observations.

Computer capability invocations enter the existing Event Plane. Audit facts contain bounded metadata, argument/result hashes, and Artifact IDs rather than raw UI content.

v1.4 is still a policy-constrained local runtime, not an OS sandbox or secret broker.

Important limitations:

- UAC secure desktop and the Windows lock screen are not automated.
- UI Automation quality varies by application and framework.
- some applications expose no useful subtree for a top-level HWND.
- a UIA `set-value` or `InvokePattern` call can technically succeed without causing the application's intended business behavior.
- therefore post-action observation is mandatory; reported command success is not treated as task success.
- screenshots supplement semantic inspection; they do not grant a raw coordinate-control surface.

## Real E2E findings

The implementation was exercised against real Windows UI and a controlled local GUI.

Windows Search exposed a stable `SearchTextBox` AutomationId. UIA `set_value` changed its value but did not trigger search behavior, and posted keyboard messages could report success without affecting the XAML control. The Provider was therefore hardened to use internal selector-targeted `send-input` for the restricted text/key fallback.

A controlled Coding-Agent GUI E2E then verified:

```text
local GUI = BROKEN
    -> computer.inspect discovers semantic button selector
    -> PLA edits local logic.py to return VERIFIED
    -> UIA InvokePattern reports success but UI remains BROKEN
    -> post-action verification detects the false success
    -> computer.click uses the same semantic selector as fallback
    -> window state becomes VERIFIED
    -> computer.screenshot returns a PNG Artifact
```

This validates the v1.4 design rule: semantic-first, always verify, then use a bounded semantic-targeted input fallback when application semantics require it.

## Verification

Before release freeze:

- Computer Provider live discovery: 17/17 tools ready.
- Provider Doctor live probe: 8/8 providers healthy.
- Computer capability Event Plane integration verified.
- real selector-targeted click fallback reached `PLA Computer E2E — VERIFIED`.
- final screenshot imported into Artifact Plane successfully.

Final regression and lifecycle freeze results are recorded in the v1.4 CHANGELOG entry.
