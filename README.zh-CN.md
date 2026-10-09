# plus-local-agent

**ChatGPT是界面；PLA是运行时。**

[English](README.md) | **简体中文**

plus-local-agent（PLA）为ChatGPT提供一个运行在你自己Windows电脑上的受控本地执行环境。ChatGPT继续承担推理、规划与编排；PLA负责经过审查的本地能力，包括文件、进程、Git、Python、浏览器自动化、Windows UI自动化、文档、应用管理、Artifact以及其他MCP Provider。

ChatGPT客户端不需要与PLA运行在同一台电脑上。只要Secure MCP Tunnel保持连接，你可以从手机、网页端或桌面端与ChatGPT对话，而实际本地执行发生在目标Windows电脑上的PLA中。

## 快速开始

### 环境要求

- Windows 10/11 x64
- Git for Windows
- Python 3.11
- 当前版本的Node.js与npm
- Microsoft Edge
- 目标电脑自己的Secure MCP Tunnel配置
- 如需完整Windows软件包管理能力，还需要WinGet MCP runtime
- 如需WPS Office Provider，还需要安装WPS Office

### 下载

先确定安装目录。下面示例把PLA安装到当前Windows用户目录下的`AI_Tools\plus-local-agent`；如需安装到其他位置，只修改`$PlaRoot`即可。

~~~powershell
$PlaRoot = Join-Path $env:USERPROFILE "AI_Tools\plus-local-agent"
New-Item -ItemType Directory -Force -Path (Split-Path $PlaRoot) | Out-Null
git clone https://github.com/ymao31196-blip/plus-local-agent.git $PlaRoot
Set-Location $PlaRoot
~~~

如需稳定部署，可以在安装前切换到对应release tag。

### 安装

执行仓库安装器：

~~~powershell
.\install.ps1
~~~

安装器会创建本地Python环境、安装经过审查的Provider依赖、检查运行条件并执行验证。如果此时尚未配置这台电脑自己的Secure MCP Tunnel，安装结果可能显示为**PARTIAL**。这通常表示本地PLA已经安装完成，只差Tunnel等外部连接条件，并不等于安装失败。

从OpenAI官方的[Tunnels管理页面](https://platform.openai.com/settings/organization/tunnels)（推荐）或[官方tunnel-client releases](https://github.com/openai/tunnel-client/releases)下载Windows x64 Tunnel client。选择`windows-amd64` ZIP，并解压到PLA目录旁边的独立目录中。程序文件名是`tunnel-client.exe`（中间是连字符`-`）。

官方配置入口：

- [创建或管理Tunnel并复制Tunnel ID](https://platform.openai.com/settings/organization/tunnels)
- [创建Secret API Key](https://platform.openai.com/api-keys)
- [Secure MCP Tunnel官方文档](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- 如果Tunnel访问需要组织Owner或RBAC管理员授权，可查看[Roles](https://platform.openai.com/settings/organization/people/roles)和[Groups](https://platform.openai.com/settings/organization/people/groups)

创建或编辑Tunnel需要**Tunnels Read + Manage**权限；运行`tunnel-client`需要**Tunnels Read + Use**权限。请在正确的Platform组织/项目中创建这台机器专用的Secret Key，并把它作为runtime API key使用。PLA不应使用Organization Admin Key。

在`tunnel-client.exe`同一目录创建runtime key文件：

~~~powershell
$TunnelDir = Join-Path (Split-Path $PlaRoot -Parent) "tunnel-client"
$TunnelExe = Join-Path $TunnelDir "tunnel-client.exe"
$TunnelKey = Join-Path $TunnelDir "control-plane-api-key.txt"

# 在记事本中只粘贴这台电脑专用的runtime API key，然后保存并关闭。
# 不要把真实key直接写进PowerShell命令，以免进入命令历史。
if (-not (Test-Path -LiteralPath $TunnelKey)) {
    New-Item -ItemType File -Path $TunnelKey | Out-Null
}
notepad.exe $TunnelKey

Test-Path -LiteralPath $TunnelExe
Test-Path -LiteralPath $TunnelKey
~~~

最后两条命令都应返回`True`。key文件中只放API key本身，不要加入引号、变量名或`Bearer `前缀，也不要把真实key直接粘贴进PowerShell命令历史。

目录结构通常类似：

~~~text
AI_Tools\
|-- plus-local-agent\
|   `-- install.ps1
`-- tunnel-client\
    |-- tunnel-client.exe
    `-- control-plane-api-key.txt
~~~

然后配置Tunnel并启动PLA：

~~~powershell
.\install.ps1 `
  -TunnelId "tunnel_CUSTOMER_ID" `
  -TunnelClient $TunnelExe `
  -TunnelCredential $TunnelKey `
  -PersistEnvironment `
  -Start
~~~

PLA会把这台机器的Tunnel配置写入`config/tunnel.local.yaml`。该文件不会进入Git。不要复用其他电脑或其他用户的Tunnel ID、credential，也不要把key文件放进PLA仓库。

如果只想先检查前置条件，不执行安装：

~~~powershell
.\install.ps1 -ValidateOnly
~~~

更完整的部署说明见[docs/customer_installation.md](docs/customer_installation.md)。若使用用户可配置的Skill Source，独立安装与来源授权步骤请参考[Skill Library Provider说明](docs/skill_library_provider.md)；普通PLA安装不会预设开发者的个人Skill仓库。

## 核心能力

| 领域 | PLA提供的能力 |
| --- | --- |
| 本地执行 | 受控文件、进程、Python和本地程序执行；受治理的WSL访问；allow-list控制的`latexmk`/`xelatex`；以及对进程、服务、TCP/UDP端点、网络适配器、路由、IP接口metric/DHCP/MTU、IP/DNS配置、有界System/Application事件日志、计划任务状态、文件签名和workspace ACL的结构化PowerShell诊断 |
| 浏览器 | 独立Browser Runtime、PLA管理的持久profile、可复用登录态，以及基于语义accessibility/ref的浏览器交互 |
| Computer Use | 基于Windows UI Automation的桌面操作，并限制selector定向的输入注入fallback |
| 文档 | WPS Office自动化，以及经过审查的PDF和Markdown转换能力；旧的独立DOCX Provider仍保留源码，但默认不启动 |
| Windows管理 | 系统观察、WinGet集成以及边界严格的应用管理能力 |
| Artifact | 结构化Artifact导出、元数据、分块和provenance校验 |
| Provider | 基于manifest的MCP Provider、隔离运行环境与热更新 |
| Transaction | 带checkpoint、确认与恢复状态的持久多步骤动作 |
| Event与Policy | Event、Observer和Gate平面，用于审计与策略执行 |
| Workspace | 与PLA源码分离的本机授权root |
| Git | expected-HEAD、显式path的stage/remove、commit、tag与受控push |
| Runtime生命周期 | 独立Lifecycle Broker、受控HTTP重启、生产Execution Runner与健康检查 |

Browser Provider使用PLA管理的持久profile。你在该profile中登录网站后，后续浏览器任务可以复用保存的登录状态，而不需要每次从全新浏览器会话开始。PLA Browser Profile与日常Edge profile相互独立，也不会直接继承已经运行中的Edge登录态。

系统写操作使用独立的`windows.*` Capability边界，而不是不断扩大通用PowerShell工具权限。`windows.flush_dns_cache`需要显式`INVOKE`确认。`windows.service_control_preflight`是只读能力，在产生任何提权请求前报告服务状态、依赖服务状态、策略授权、Broker就绪状态和具体阻塞原因。`windows.service_control`还要求Transaction上下文，并要求本机`config/windows_actions.local.json`中存在对**精确服务+精确操作**的规则，因此授权`restart`不会自动等价于授权`stop`。服务操作会在排队前和Interactive Elevation Broker执行前再次检查；disabled或不稳定服务、无法停止的服务、以及仍有active dependent services时的stop/restart都会被拒绝。调用方不能自行提供可执行文件或命令行。

External-pending动作可以声明一个固定只读completion verifier。Transaction Envelope会在收到pending结果时立刻验证该verifier，并拒绝指向write、需要confirmation或需要transaction的Capability。被接受的completion contract会被持久化，普通`succeeded` checkpoint无法绕过；`core.transaction_complete_external`只能调用之前记录的verifier，并且只有verifier返回`completed`后才会把该step标记为成功。受Completion Gate保护的running动作即使PLA Runtime重启，也可以作为新的Transaction revision继续保持`running`并恢复；没有可信completion contract的普通running step仍会恢复为`interrupted`并被阻塞。Windows service control和提权软件迁移流程都会使用该Completion Gate。`windows.elevation_broker_restart`是独立确认型生命周期动作；当Broker繁忙时会拒绝重启，并且在替换前校验进程身份。如果你确实要在某台机器上授权特定服务操作，可复制`config/windows_actions.example.json`创建本机策略。该本地策略文件不进入Git，并且受普通PLA文件访问保护。

## 从任何设备使用ChatGPT

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
        +-- Semantic / Execution Policy
        +-- Production Execution Runner
        +-- Persistent Session Runtime
        +-- Browser / Computer Providers
        +-- Artifact Plane
        +-- Durable Task / Transaction Store
        +-- Event / Observer / Gate Plane
        +-- Runtime Lifecycle Broker
        +-- Interactive Elevation Broker
        +-- External MCP Providers
~~~

PLA内部没有第二套自主规划器，也不再把MCP Sampling作为主线执行路径。Provider只暴露经过审查的Capability；ChatGPT保持Agent Brain身份，并决定如何把这些能力组合成多步骤工作流。

## 稳定Capability入口

外部Provider的大量工具不会全部直接塞进ChatGPT的MCP schema。PLA只保留少量高频hot-path顶层工具；专业能力和低频操作通过Capability Registry按需发现。

v2.0.0 runtime保留**23个顶层MCP工具**。这些工具覆盖Capability Router，以及高频文件、进程、Artifact导出、Task、Git status/diff和恢复/诊断路径。

更大的运行时Capability面通过以下入口按需发现：

~~~text
capability_search
capability_describe
capability_invoke
~~~

v2.0.0发布点的live registry包含**412个动态Capability**。ChatGPT会先搜索Registry，再查看选中Capability的完整描述，最后只加载当前任务真正需要的能力，而不是一开始就接收所有Provider schema。

Provider manifest定义工具allowlist、risk level、confirmation要求、transaction要求、Artifact策略、runtime约束和dependency setup。

Python Provider运行在隔离的Provider环境中。经过审查的Node和native executable Provider也可以接入，而不需要开放任意shell执行。基于源码的Node Provider还可以固定到某个精确GitHub commit、应用仓库内维护的兼容patch，并通过固定Provider setup路径构建；真正运行时仍然受普通manifest与Capability Policy边界约束。

## Provider管理

基于manifest的Provider可以通过内置Runtime Capability检查和管理：

~~~text
runtime.provider_status
runtime.provider_setup
runtime.provider_rescan
runtime.provider_reload
runtime.provider_enable
runtime.provider_disable
~~~

Provider setup只会通过仓库自带entrypoint安装经过审查并固定版本的依赖。Runtime变更不会绕过manifest校验、tool allowlist、confirmation policy、transaction policy或execution boundary。

会修改Provider状态的操作需要显式**INVOKE**确认。

## 持久Transaction与确认机制

高风险多步骤工作可以使用持久Transaction Capability：

~~~text
core.transaction_create
core.transaction_get
core.transaction_checkpoint
core.transaction_finalize
core.transaction_invoke
~~~

Transaction会持久保存plan state、乐观revision、verification checkpoint、rollback状态以及interrupted-step信息。

标记为`requires_transaction`的Capability不能绕过Transaction直接调用；标记为`requires_confirmation`的Capability还需要显式**INVOKE**授权。

## Event、Observer与Gate平面

PLA记录有界执行事件，例如：

~~~text
capability.gate_denied
capability.before_invoke
capability.succeeded
capability.failed
~~~

默认情况下，Event只保存有界metadata与hash，不直接保存完整Capability参数或原始结果。

Observer Hook可以对已经持久化的事件作出反应，但不会改变被选Capability的执行结果。Gate Hook在真正执行前运行，并采用deny-overrides策略决定ALLOW/DENY；Gate自身失败时fail closed。

只读检查入口包括：

~~~text
core.event_query
core.hook_status
core.hook_invocation_query
core.gate_status
core.gate_decision_query
~~~

外部Observer插件可以通过经过审查的manifest和隔离runtime接入。第三方代码不会因为注册Observer而获得静默扩大PLA执行边界的权限。

## Runtime生命周期与独立Execution Plane

PLA把HTTP Control Plane与默认generic one-shot Execution Plane分离。普通`run_process`默认由经过认证的**独立Execution Runner**执行；persistent process session和structured PowerShell继续沿用各自专用路径。

每个one-shot都有精确的`execution_request_id`。因此HTTP重启后，新Control Plane可以用同一个request ID重新连接Runner保留的结果，而不是把命令重新执行一遍。

生产Runner在启动child前会独立检查自己的进程身份、协议版本、workspace/root policy、program allowlist、环境变量override和semantic execution policy。Task取消也按精确request ID转发，Runner只能终止该request自己拥有的child tree；PLA没有开放任意PID kill接口。

PLA还保留独立Runtime Lifecycle Broker，用于受控HTTP重启：

~~~text
runtime.lifecycle_status
runtime.restart_http
runtime.restart_status
runtime.execution_runner_status
runtime.execution_runner_result
~~~

`runtime.restart_http`需要显式**INVOKE**。Broker会先验证目标PLA HTTP进程身份，再执行重启；Secure MCP Tunnel和生产Execution Runner都与HTTP进程分离。正常`start_all.ps1`启动会ensure Runner，`stop_all.ps1`会请求Runner进行graceful shutdown。

连接中断本身不会被视为“操作成功”的证据；PLA会显式检查restart status、Runner identity和保留的execution state。

## Interactive Elevation

PLA正常以非管理员权限运行。需要UAC的操作会交给登录Windows会话中的Interactive Elevation Broker。

Broker不会提供任意`runas`。它只接受经过审查的固定request shape，例如边界严格的软件卸载或WinGet安装操作。Windows UAC仍然是最终本机用户授权边界。

## Runtime Workspace Registry

PLA把产品源码与客户workspace授权分开。

内置root：

- **pla**：PLA源码与开发root
- **workspace**：仓库内默认Runtime workspace

其他本机customer root保存在Git忽略文件：

~~~text
config/workspaces.local.yaml
~~~

可以通过以下Capability查看和管理：

~~~text
core.workspace_roots_get
core.workspace_root_upsert
core.workspace_root_remove
~~~

Workspace修改需要显式**INVOKE**，并要求乐观config-SHA匹配。Customer root不能与PLA源码树重叠。

## 当前经过审查的Provider

当前生产Provider集合包括：

- Browser自动化
- Windows Computer Use
- Markdown / 文档转换
- PDF处理
- WinGet软件包发现与经过审查的安装
- Windows系统观察与边界严格的应用管理
- 软件迁移工作流
- WPS Office自动化，通过经过审查的`lc2panda/wps-skills` MCP Provider覆盖文档、表格、演示文稿和更广泛的Office工具面
- Office补强（`office-enhancement`）：artifact-tool可编辑PPT overlay、ZIP/OpenXML精细merge、Microsoft PowerPoint COM渲染/溢出检查，以及PyMuPDF图片提取
- Skill Library：提供可复用`SKILL.md`经验，支持cache-first搜索/读取以及Git-backed refresh

WPS Office Provider以source-backed Node Provider方式安装，并固定到经过审查的upstream commit。PLA会在build前应用自己维护的Windows COM兼容patch，再把upstream MCP tools接入同一个Capability Registry。常见表格读取被分类为`read`；普通写入、格式化和保存为`write_local`；删除工作表/行/列等破坏性操作以及raw method execution仍然需要confirmation。WPS仍是日常文档、表格和演示编辑的主要Office自动化路径；旧的独立DOCX Provider源码保留，但不再自动启动。Windows环境要求WPS Office与Node.js 18+。

### Office补强

`office-enhancement`用于普通Office编辑不足以完成的精细PPT任务。它把四种互补技术收在同一个经过审查的Provider后面：

~~~text
structured artifact-tool overlay
        ↓
zipfile + lxml OOXML merge
        ↓
Microsoft PowerPoint COM render / inspect
        ↓
PyMuPDF PDF image extraction / region rendering
~~~

artifact-tool adapter只接受结构化shape、text、image和connector，不执行调用方提交的任意JavaScript。它会在调用时发现本机Codex primary-runtime安装的`@oai/artifact-tool`包，并解析该package声明的export entry。因此该包属于可选本机runtime dependency，而不是vendored到PLA仓库中的依赖。

OOXML merge只复制overlay元素实际引用的relationship，保留目标PPTX原有package/master/layout结构，并重新映射relationship ID与shape ID，同时为引用的media分配新的package名称。PowerPoint COM用于真实Microsoft PowerPoint渲染以及文本几何检查，包括可能的overflow。PyMuPDF负责提取PDF内嵌图片和页面/区域渲染，因此即使是vector chart也可以恢复为PNG。

日常Office工作优先使用WPS；需要外科式PPTX修改、可编辑技术框图、保格式merge、真实PowerPoint QA或PDF图像提取时，使用Office补强。

Skill Library Provider刻意保持轻量。Skill属于建议型可复用经验，用于减少重复错误、保留稳定工作流和提醒容易忘记的约定；它不会覆盖当前用户指令、当前任务事实或ChatGPT的判断。

实际加载的Provider集合可通过`runtime.provider_status`查看。v2.0.0发布点当前有**10个active external Providers**；WPS暴露250个Capability，Skill Library暴露4个。

## 软件迁移

PLA包含经过审查的软件迁移流程，核心是“验证迁移”而不是盲目卸载/重装：

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

迁移层刻意比通用package manager或任意shell访问更窄。

## 受控Git写入

Git mutation依赖显式仓库状态与文件身份检查。

`git_stage`要求expected HEAD和每个目标文件的SHA-256；`git_commit`只提交精确的已stage path集合。面向release的tag和push同样要求精确branch/HEAD匹配与显式确认；force push不可用。

Generic process execution不能借机绕过PLA源码仓库的Git边界。如果ChatGPT尝试通过`run_process`并指定`root="pla"`执行Git，PLA会返回结构化`specialized_capability_required`决策，并列出应优先考虑的受治理Git工具/Capability。PLA不会自动替换并执行建议动作。

这样ChatGPT可以处理真实Git仓库，但Git不会退化为无限制shell逃逸口。

## Capability Steering

PLA为那些“generic execution与某个受治理专业Capability重叠”的场景维护确定性routing rule。Steering rule不会自动调用建议的替代Capability，只返回结构化decision，包括匹配domain、routing mode、attempted route和按顺序排列的Capability建议；下一步仍由ChatGPT决定。

当前enforced route包括：PLA源码Git通过`run_process`时必须转向受治理Git面；Windows service状态修改通过`run_powershell`时必须转向专用Capability。只读动态Capability`core.capability_route`也可以在执行前评估一个`capability_invoke`。Enforced overlap返回`specialized_required`；advisory overlap返回`specialized_preferred`；无关route返回`generic_allowed`。

第一条advisory rule处理“通过Computer Provider显式指向浏览器”的场景。如果`computer.inspect/click/type/press/wait/search/screenshot`等Capability的`app`参数明确指向Edge、Chrome、Chromium或Firefox，PLA会优先建议对应`browser.*` Capability，同时保留Computer Use作为fallback。PLA不会仅凭一个opaque HWND推断浏览器身份。

Routing仍然复用既有`capability_invoke`入口，因此Steering可以扩展而不需要再增加顶层MCP工具。只读`Get-Service`、GitHub CLI `gh`以及customer-workspace Git不会被这些enforced rule无条件重定向。

外部Provider还可以在manifest中声明routing metadata。Capability descriptor带有`routing_authority`以及`preferred_over`、`fallback_for`或`supersedes`关系，并可附带argument condition。外部manifest只允许`recommendation`或`preferred` authority；`enforced`只保留给PLA内建policy。Routing每次查询都读取当前Live Capability Registry，因此Provider add/change/enable/disable/remove经过正常hot-rescan后即可影响routing，无需重启routing层。

只读动态Capability`core.routing_audit`会检查完整Capability Registry和维护中的Routing Catalog。它验证catalog rule仍然引用真实Capability，然后基于action alias、risk compatibility、shared tags和显式provider relationship给出保守的cross-provider overlap候选。候选只是review hint；audit不会创建、修改或执行routing rule。

Audit候选分为三种review状态：`covered`表示已有active routing rule处理该overlap；`reviewed_parallel`表示已经人工审查并决定保留两套不同职责的Capability；只有`uncovered`仍需要routing review。宽泛的search类action默认会被忽略，除非Provider显式声明关系，从而避免把无关UI、process和package search误判为重复能力。

## 启动与停止

启动PLA和所需本地服务：

~~~powershell
.\start_all.ps1
~~~

停止：

~~~powershell
.\stop_all.ps1
~~~

源码修改后只重启PLA HTTP Runtime：

~~~powershell
.\restart_pla.ps1
~~~

## 验证

运行完整回归测试：

~~~powershell
python -m pytest -q
~~~

Runtime state、Provider环境、credential、本地Tunnel配置、log、IDE state和customer workspace输出都不会进入Git。

源码目录约束与各模块职责见`docs/source_layout.md`。仓库根目录不再放Python实现文件，低层Runtime启停脚本统一位于`scripts/`；`tests/test_source_layout.py`负责防止目录结构回退。

v2.0.0 Runtime/Execution Plane主线的最终全仓基线为：

~~~text
762 passed
~~~

## 设计原则

1. **ChatGPT始终是Agent Brain。** PLA不会再增加一套竞争性的自主规划器。
2. **ChatGPT始终是UI。** PLA专注本地执行与安全，不再另造聊天界面。
3. **Capability必须经过审查并保持有界。** 任意shell执行不是默认集成方式。PowerShell继续采用固定cmdlet/parameter allowlist；诊断输出被投影成有界结构化数据；Task取消只能终止Runtime自己拥有的process tree。
4. **本地状态留在本地。** Credential、customer workspace root和deployment配置都不是产品源码。
5. **高风险动作必须显式。** Confirmation、Transaction、UAC和Git precondition继续作为相互独立的安全层。
6. **Provider可以扩展，但不能把安全边界拍平。** 新MCP Capability仍然必须经过同一套Runtime Policy。
7. **顶层工具面保持小而稳定。** 高频恢复路径直接可见；专业能力、治理能力和Provider专属操作通过Capability Registry按需发现。
8. **Control Plane与Execution Plane分离。** 默认generic one-shot由独立Runner执行；HTTP重启不应重放已提交命令。Persistent Session继续保留专用Runtime，直到真实需求证明需要迁移。
9. **不伪装成OS sandbox。** 当前filesystem boundary仍属于PLA wrapper policy；`sandbox_mode=none`会明确记录。真正OS级sandbox以后由独立需求驱动，不通过文档措辞夸大现有边界。
