# PLA Desktop V1 architecture and permissions

PLA Desktop manages the existing ChatGPT-native execution environment. ChatGPT continues to reason, plan and call MCP tools; the existing Python Runtime continues to validate and execute them. No model engine or alternative agent loop is added.

## Components

- Tauri 2 native shell with a static HTML/CSS/JavaScript interface, a single window and a system tray. No frontend framework or web server is required.
- A frozen PyInstaller **onedir** payload, bundled as a complete resource directory, not a single executable sidecar. It includes Python DLLs, extension modules, dependency metadata and first-party Runtime modules.
- A private management process connected to the Tauri parent by anonymous stdin/stdout pipes. It accepts only fixed commands for status, configuration, lifecycle, workspace registration, logs and diagnostics. There is no management HTTP listener, user-selected executable or frontend Shell plugin.
- The unchanged public MCP tool wrappers and capability broker serve tools over a separately configured loopback endpoint. Workspaces reuse the existing root validator and config hash mechanism. Command allowlists, semantic routing, confirmation and gate mechanisms remain in force.
- Fixed-version Python 3.11.9 embeddable distribution provides controlled `python` execution without a system interpreter. The frozen Runtime's Python is independently bundled. This minimal execution interpreter does not include pip or third-party Python packages.
- Checksum-verified official Secure MCP Tunnel v0.0.14 release, including its cloudflared runtime and license/SBOM files. It is launched only after the user supplies their own Tunnel ID and runtime key.
- Opt-in reviewed Playwright MCP 0.0.82 and Node 22.16.0 resources. They use system Microsoft Edge, an independent browser port, and private profiles. The existing Browser Runtime identity and ownership checks are retained.

## Native permissions and process ownership

The Tauri frontend can call fixed management commands, a boolean HKCU login-start preference and three fixed official help links. It has no filesystem, Shell, HTTP, updater or remote-page capabilities. Its Content Security Policy allows local assets and Tauri IPC only. User-provided labels, paths and logs are rendered using text nodes.

The native parent creates a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assigns the waiting management process to it, and retains the handle. Runtime, Tunnel, browser, execution runner and tool subprocesses inherit job membership. An abnormal native parent exit closes the handle and kills the owned tree. The management process additionally reuses `terminate_owned_process_tree` when stopping its own `Popen` handles. It never accepts a caller PID or kills by port number. It refuses an occupied Runtime/Tunnel/browser port instead of adopting its listener.

Source/headless launching remains supported. New `runtime.paths` defaults to the original project root. Desktop-only environment settings select private state databases, workspace configuration, profiles and packaged resources before importing Runtime modules. Frozen subprocess roles explicitly dispatch the runner, browser keeper and Computer Use indicator rather than pretending the frozen executable can run source scripts as a Python interpreter.

## User data and credentials

Normal data location: `%LOCALAPPDATA%\io.pla.desktop`, obtained from the native known-folder API. Application files are installed separately under the current user's installation directory. User data includes `config`, `state`, `logs`, `cache` and the default internal `workspace`.

`desktop.json` stores non-secret settings; `tunnel.secret` stores Windows DPAPI ciphertext bound to the current Windows user. A saved key is never returned to the frontend. On launch it is decrypted in the management process and passed to **only** the Tunnel child through `CONTROL_PLANE_API_KEY`; the command line contains an environment reference. Developer provider settings and inherited API keys are removed from Runtime child environments. Sensitive values are redacted before log persistence and rendering. DPAPI is not a boundary against another process running as the same Windows user.

New user roots cannot overlap application resources or private data. Workspace changes are blocked while Runtime is running; read/write/execute permissions and expected config hashes are validated by the existing registry. Removing an authorization preserves user files. No developer credentials/configuration are imported automatically.

Desktop's application-resource root `pla` permits reads, but rejects file-tool writes and process execution. The same private-data overlap rejection is enforced by the existing Runtime registry validator, including confirmed MCP registration requests. Source/headless root defaults are retained. Root/CWD validation and the existing program/semantic policies are not a Windows OS sandbox: explicitly authorized execution retains the current user's operating-system privileges, as in the original Runtime.

The wizard can inspect a user-selected existing source directory for the expected startup/server files and configuration-file presence. It does not read credential/configuration contents, migrate data or adopt existing processes. Login-start changes also refuse to overwrite a registration for a different installed executable.

## State evidence and limitations

- Runtime readiness requires a successful real MCP tool catalog request, including the expected diagnostic tool. The current protocol's catalog works across MRTR/backchannel modes; a TCP listener or PID alone is insufficient.
- Local verification invokes `diagnose_client`; it does not assert Tunnel traversal or ChatGPT authorization.
- Tunnel readiness is read from the owned Tunnel's loopback `/readyz`. It does not establish ChatGPT account authorization or a remote tool call.
- Browser readiness requires capability discovery by Runtime; Node's PID alone is insufficient. Missing Edge must be diagnosed by an actual browser invocation before browser execution acceptance.
- ChatGPT authorization is not observable locally. ChatGPT end-to-end status remains unverified until human acceptance is recorded separately.
- Office and Skills external provider environments are not bundled in this candidate. Their state is explicitly unavailable. Existing source deployments can continue using their reviewed provider setup flow; the desktop does not silently borrow developer environments.
- Automatic updates are disabled. Windows Authenticode and Tauri updater signing are independent. This candidate has neither certificate nor public update infrastructure.

## Build route

Tauri 2 was successfully compiled on this host with Rust 1.90.0 GNU and a checksum-verified local MinGW toolchain because MSVC Build Tools were absent. The application import table must be checked and WebView2Loader.dll included by the bundler. CI uses the standard Windows MSVC toolchain on Windows Server 2022. Python, npm and Cargo resolution inputs are locked; public binary downloads are hash checked. Rebuilding a source revision is supported; bit-for-bit installer determinism is not claimed.

The NSIS installer targets the current user and embeds the WebView2 bootstrapper. A machine lacking WebView2 needs network access to Microsoft for that prerequisite. The application does not require an administrator launch. The optional uninstall data-removal checkbox belongs to Tauri's standard template and is unchecked by default; leave it unchecked to preserve configuration. External authorized workspaces are never part of installer resources.
