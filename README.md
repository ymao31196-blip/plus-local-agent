# plus-local-agent

**ChatGPT is the interface; PLA is the runtime.**

plus-local-agent (PLA) gives ChatGPT a controlled execution environment on your own Windows PC.
ChatGPT remains the reasoning and orchestration layer; PLA provides reviewed local capabilities for
files, processes, Git, Python, browser automation, Windows UI automation, documents, application
management, artifacts, and other MCP providers.

The ChatGPT client does not need to run on the same computer as PLA. With the Secure MCP Tunnel
running, you can talk to ChatGPT from mobile, web, or desktop while PLA executes on the target PC.

**Installation:** [English](#quick-start) | [中文](#中文安装指南)

## Quick start

### Requirements

- Windows 10/11 x64
- Git for Windows
- Python 3.11
- current Node.js with npm
- Microsoft Edge
- Secure MCP Tunnel credentials for the target machine
- WinGet MCP runtime for the full Windows package-management feature set

### Download

Choose an installation directory first. The example below uses a dedicated folder
under the current Windows user profile; change `$PlaRoot` if you prefer another location.

~~~powershell
$PlaRoot = Join-Path $env:USERPROFILE "AI_Tools\plus-local-agent"
New-Item -ItemType Directory -Force -Path (Split-Path $PlaRoot) | Out-Null
git clone https://github.com/ymao31196-blip/plus-local-agent.git $PlaRoot
Set-Location $PlaRoot
~~~

For a stable deployment, you may check out the release tag you want before installation.

### Install

Run the repository installer:

~~~powershell
.\install.ps1
~~~

The installer creates the local Python environment, installs reviewed Provider dependencies,
checks prerequisites, and runs validation. If customer-specific Tunnel credentials are not yet
configured, a safe local installation may finish as **PARTIAL**.

To configure the customer Tunnel and start PLA:

~~~powershell
.\install.ps1 `
  -TunnelId "tunnel_CUSTOMER_ID" `
  -TunnelClient "C:\path\to\tunnel-client.exe" `
  -TunnelCredential "C:\path\to\control-plane-api-key.txt" `
  -PersistEnvironment `
  -Start
~~~

Machine-specific Tunnel configuration is written to config/tunnel.local.yaml, which is ignored
by Git. Do not reuse another machine's Tunnel ID or credential.

For a non-mutating prerequisite check:

~~~powershell
.\install.ps1 -ValidateOnly
~~~

More deployment details are available in
[docs/customer_installation.md](docs/customer_installation.md).

## 中文安装指南

如果你只想尽快把PLA装起来，可以直接按下面的顺序操作。

### 1. 准备环境

目标电脑需要：

- Windows 10/11 x64
- Git for Windows
- Python 3.11
- 当前版本的Node.js与npm
- Microsoft Edge
- 目标电脑自己的Secure MCP Tunnel配置
- 如需完整Windows软件管理能力，还需要WinGet MCP runtime

### 2. 下载PLA

先确定安装目录。下面示例把PLA安装到当前Windows用户目录下的
`AI_Tools\plus-local-agent`；如果你想安装到其他位置，只需要修改`$PlaRoot`。

~~~powershell
$PlaRoot = Join-Path $env:USERPROFILE "AI_Tools\plus-local-agent"
New-Item -ItemType Directory -Force -Path (Split-Path $PlaRoot) | Out-Null
git clone https://github.com/ymao31196-blip/plus-local-agent.git $PlaRoot
Set-Location $PlaRoot
~~~

这样无论你从哪个PowerShell目录开始执行，PLA都会被clone到明确的目标位置，而不是落在当前目录。

如果你希望使用稳定版本，可以在安装前切换到对应的release tag。

### 3. 安装本地运行环境

执行：

~~~powershell
.\install.ps1
~~~

安装器会自动创建本地Python环境、安装经过审查的Provider依赖、检查运行条件并进行验证。

如果此时还没有配置这台电脑自己的Secure MCP Tunnel，安装结果可能显示为**PARTIAL**。
这通常表示本地PLA已经安装完成，只差Tunnel等外部连接条件，并不等于安装失败。

### 4. 配置Secure MCP Tunnel并启动

准备好这台电脑自己的Tunnel ID、tunnel-client和credential后执行：

~~~powershell
.\install.ps1 `
  -TunnelId "tunnel_CUSTOMER_ID" `
  -TunnelClient "C:\path\to\tunnel-client.exe" `
  -TunnelCredential "C:\path\to\control-plane-api-key.txt" `
  -PersistEnvironment `
  -Start
~~~

PLA会把本机Tunnel配置写入config/tunnel.local.yaml。这个文件不会进入Git。
不要复制或复用其他电脑、其他用户的Tunnel ID或credential。

### 5. 只检查环境，不执行安装

如果想先确认电脑是否满足要求：

~~~powershell
.\install.ps1 -ValidateOnly
~~~

### 6. 安装完成后的常用命令

启动PLA：

~~~powershell
.\start_all.ps1
~~~

停止PLA：

~~~powershell
.\stop_all.ps1
~~~

仅在修改源码后重启PLA HTTP：

~~~powershell
.\restart_pla.ps1
~~~

PLA运行并连接Secure MCP Tunnel后，你可以继续直接使用ChatGPT作为界面。
ChatGPT可以来自同一台电脑，也可以来自手机、网页端或另一台设备；真正的本地执行仍发生在运行PLA的Windows电脑上。

更完整的部署说明见[docs/customer_installation.md](docs/customer_installation.md)。

## Core capabilities

| Area | What PLA provides |
| --- | --- |
| Local execution | Controlled file, process, Python, PowerShell and local program execution |
| Browser | Independent browser runtime with semantic accessibility/ref-based interaction |
| Computer Use | Windows UI Automation with restricted selector-targeted input fallbacks |
| Documents | Reviewed DOCX, PDF and Markdown conversion capabilities |
| Windows management | Observation, WinGet integration and narrowly controlled application operations |
| Artifacts | Structured artifact export, metadata, chunking and provenance checks |
| Providers | Manifest-backed MCP providers with isolated environments and hot reload |
| Transactions | Durable multi-step actions with checkpoints, confirmation and recovery state |
| Events and policy | Event, Observer and Gate planes for audit and policy enforcement |
| Workspaces | Deployment-local authorized roots separated from PLA source |
| Git | Expected-HEAD, explicit-path staging, commits, tags and controlled pushes |
| Runtime lifecycle | Independent lifecycle broker for bounded HTTP restart and health checks |

## Use ChatGPT anywhere

~~~text
ChatGPT Agent Brain
        |
        | MCP through Secure MCP Tunnel
        v
Stable Capability Surface
capability_search / capability_describe / capability_invoke
        |
        v
PLA on the target Windows PC
        +-- Capability Registry / Broker
        +-- Local execution boundary
        +-- Browser / Computer providers
        +-- Artifact Plane
        +-- Durable Transaction Store
        +-- Event / Observer / Gate Plane
        +-- Runtime Lifecycle Broker
        +-- Interactive Elevation Broker
        +-- External MCP providers
~~~

PLA does not contain a second autonomous planner. Providers expose reviewed capabilities;
ChatGPT decides how to combine them into multi-step work.

## Stable capability surface

External Provider tool catalogs are not copied wholesale into ChatGPT's MCP schema. PLA keeps a
small stable surface:

~~~text
capability_search
capability_describe
capability_invoke
~~~

ChatGPT searches the runtime registry, inspects the selected capability, and invokes only the tool
needed for the current step.

Provider manifests define tool allowlists, risk levels, confirmation requirements, transaction
requirements, artifact policy, runtime constraints, and dependency setup.

Python Providers run in isolated Provider environments. Reviewed Node and native executable
Providers can also be integrated without opening arbitrary shell execution.

## Provider management

Manifest-backed Providers can be inspected and updated through the built-in runtime capabilities:

~~~text
runtime.provider_status
runtime.provider_setup
runtime.provider_rescan
runtime.provider_reload
runtime.provider_enable
runtime.provider_disable
~~~

Provider setup installs only reviewed, pinned dependency specifications through repository-owned
entrypoints. Runtime changes do not bypass manifest validation, tool allowlists, confirmation
policy, transaction policy, or execution boundaries.

Mutating Provider operations require explicit **INVOKE** confirmation.

## Durable transactions and confirmation

High-risk multi-step work can use durable transaction capabilities:

~~~text
core.transaction_create
core.transaction_get
core.transaction_checkpoint
core.transaction_finalize
core.transaction_invoke
~~~

Transactions persist plan state, optimistic revisions, verification checkpoints, rollback state,
and interrupted-step information.

A capability marked requires_transaction cannot be called directly. A capability marked
requires_confirmation additionally requires explicit **INVOKE** approval.

## Event, Observer and Gate planes

PLA records bounded execution events such as:

~~~text
capability.gate_denied
capability.before_invoke
capability.succeeded
capability.failed
~~~

Event records use bounded metadata and hashes instead of storing raw capability arguments or raw
results by default.

Observer Hooks can react to persisted events without changing the selected capability's result.
Gate Hooks run before execution and can allow or deny a capability under a deny-overrides policy.
Gate failures fail closed.

Read-only inspection includes:

~~~text
core.event_query
core.hook_status
core.hook_invocation_query
core.gate_status
core.gate_decision_query
~~~

External Observer plugins are supported through reviewed manifests and isolated runtimes.
Third-party code does not receive authority to silently expand PLA's execution boundary.

## Runtime lifecycle

PLA uses an independent Runtime Lifecycle Broker for controlled HTTP restart:

~~~text
runtime.lifecycle_status
runtime.restart_http
runtime.restart_status
~~~

runtime.restart_http requires explicit **INVOKE**. The broker validates the expected PLA HTTP
process before restart, while the Secure MCP Tunnel remains separate from the HTTP process.

A dropped connection is not treated as proof of success; restart status and process identity are
checked explicitly.

## Interactive elevation

PLA normally runs without administrator privileges. UAC-sensitive operations are separated into
an Interactive Elevation Broker in the signed-in Windows session.

The broker does not provide arbitrary runas. It accepts only reviewed request shapes, such as
narrowly defined uninstall or WinGet installation operations. Windows UAC remains the final local
user boundary.

## Runtime workspace registry

PLA separates product source from customer workspace authorization.

Built-in roots:

- **pla**: PLA source and development root
- **workspace**: repository-local default runtime workspace

Additional customer roots are stored in the Git-ignored:

~~~text
config/workspaces.local.yaml
~~~

They can be inspected and managed through:

~~~text
core.workspace_roots_get
core.workspace_root_upsert
core.workspace_root_remove
~~~

Workspace mutations require explicit **INVOKE** and optimistic config-SHA matching. Customer roots
cannot overlap the PLA source tree.

## Current reviewed Providers

The production Provider set includes capabilities for:

- Browser automation
- Windows Computer Use
- Markdown/document conversion
- DOCX generation
- PDF processing
- WinGet package discovery and reviewed installation
- Windows observation and narrowly controlled application management
- Software migration workflows

The exact loaded Provider set can be inspected at runtime with runtime.provider_status.

## Software migration

PLA includes a reviewed software-migration flow designed around verification rather than blind
uninstall/reinstall:

~~~text
assess
-> prepare snapshot
-> verify backup / preconditions
-> transaction-gated uninstall
-> Interactive Elevation Broker + UAC
-> transaction-gated install to target
-> verify registered install location
-> verify user data
-> commit or rollback
~~~

The migration layer is intentionally narrower than arbitrary package-management or shell access.

## Controlled Git writes

Git mutations use explicit repository state and file identity checks.

git_stage requires an expected HEAD and SHA-256 for each selected file. git_commit commits only
the exact staged path set. Release-oriented tag and push operations also require exact branch/HEAD
matching and explicit confirmation; force push is unavailable.

This lets ChatGPT work on real repositories without turning Git into an unrestricted shell escape.

## Start and stop

Start PLA and the required local services:

~~~powershell
.\start_all.ps1
~~~

Stop them:

~~~powershell
.\stop_all.ps1
~~~

Restart only the PLA HTTP runtime after source changes:

~~~powershell
.\restart_pla.ps1
~~~

## Validation

Run the full regression suite:

~~~powershell
python -m pytest -q
~~~

Runtime state, Provider environments, credentials, local Tunnel configuration, logs, IDE state,
and customer workspace outputs are excluded from Git.

## Design principles

1. **ChatGPT stays the Agent Brain.** PLA does not add a competing autonomous planner.
2. **ChatGPT stays the UI.** PLA focuses on execution and safety rather than building another chat app.
3. **Capabilities are reviewed and bounded.** Arbitrary shell execution is not the default integration model.
4. **Local state stays local.** Credentials, customer workspace roots and deployment configuration are not product source.
5. **High-risk actions are explicit.** Confirmation, transactions, UAC and Git preconditions remain separate safety layers.
6. **Providers are extensible without flattening security.** New MCP capabilities still pass through the same runtime policy boundary.
