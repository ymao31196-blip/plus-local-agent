# PLA 源码整理后的启动链回归修复

验证日期：2026-10-07（Asia/Shanghai）。保留现有未提交的 `src/ + scripts/`
整理；没有执行 Git 写操作，没有修改 Engineering Bridge、Tunnel 配置或凭据。

## 根因与修复

1. **Browser 的直接故障**：单独执行 `scripts/start_browser_runtime.ps1`
   得到 `RuntimeError: Browser Session Keeper script is missing`。
   `browser_runtime.py` 仍引用根目录的 `browser_session_keeper.py`，因此尚未
   创建 Playwright MCP 进程就失败。改为解析自身旁边的 Keeper 文件。
2. **Python 入口环境不一致**：Elevation 的已有 `PYTHONPATH` 修复只覆盖
   单个入口，Lifecycle/HTTP 未明确设置，其他入口重复实现设置逻辑。
   新增 `runtime_common.psm1`，所有 Python 启停助手在执行 Python 前统一
   将绝对 `src` 路径置于 `PYTHONPATH` 首位，去重且保留原有其他路径。
3. **Python 内部 Runner 启动**：Runner service 原先自行修改 `sys.path`。
   改为由 `runner_transport.start_detached_runner` 显式提供子进程环境和
   项目根工作目录，移除 service 中的路径补丁。测试验证从外部工作目录、
   清空父进程 `PYTHONPATH` 后仍能启动真实 detached Runner。
4. **Browser 外层等待掩盖错误**：`start_all` 在隐藏窗口启动 Browser，
   外层仅等待端口 30 秒，既丢失真实异常，也未等待 Keeper 会话就绪。
   改为同步调用 supervisor 启动助手，保留真实 stderr，并等待其完整就绪
   检查结束后再验证监听进程的 Playwright CLI 路径。
5. **身份检查过于宽泛**：根目录字符串加 basename 无法区别旧入口与新
   入口。Broker、HTTP 的启停和 HTTP restart 现在校验当前 `src` 文件的
   完整绝对路径；Browser 校验项目 provider 环境中的完整 CLI 路径。
   保留文件方式启动，未改用 `-m`，因此启停身份约定保持一致。
6. **Tunnel 启停配置不一致**：start 使用 `tunnel.local.yaml` 或环境覆盖，
   stop 固定使用 `tunnel.yaml`。三处统一调用 `Resolve-PlaTunnelConfig`，
   相对覆盖路径均基于项目根解析，没有更改配置内容或 credential。
7. **停止遗留风险**：HTTP 原先仅终止主 PID，可能遗留 Provider 子进程；
   强制停止 Broker 不会运行 Python finally，可能留下 `running` 状态。
   HTTP stop/restart 在身份验证后关闭整个进程树；Broker 确认退出后更新
   stopped 状态并清空 PID/活动请求字段，HTTP 停止时也清理其状态记录。

全部组件脚本的 `$projectRoot = Split-Path -Parent $PSScriptRoot` 已审查并
实测；根入口仍基于其自身文件路径。Browser profile、output、state、
provider 环境，以及 Broker/Runner/Lifecycle 状态继续解析到项目根，
没有落入 `scripts/` 或 `src/`。

## 本次修改文件

以下仅列本次修复范围，不把此前未提交的目录整理算作新修复：

- 根入口：`start_all.ps1`、`stop_all.ps1`、`restart_pla.ps1`。
- 公共助手（新增）：`scripts/runtime_common.psm1`。
- 启动助手：`scripts/start_browser_runtime.ps1`、
  `scripts/start_elevation_broker.ps1`、`scripts/start_execution_runner.ps1`、
  `scripts/start_http.ps1`、`scripts/start_lifecycle_broker.ps1`、
  `scripts/start_tunnel.ps1`。
- 停止助手：`scripts/stop_browser_runtime.ps1`、
  `scripts/stop_elevation_broker.ps1`、`scripts/stop_execution_runner.ps1`、
  `scripts/stop_lifecycle_broker.ps1`。
- Python：`src/browser/browser_runtime.py`、
  `src/execution/runner_transport.py`、`src/execution/execution_runner_service.py`。
- 测试：`tests/test_source_layout.py`、`tests/test_runner_transport_spike.py`、
  `tests/test_execution_runner_runtime.py`、`tests/test_customer_installer_contract.py`。
- 文档：`docs/source_layout.md` 及本报告。

## 测试结果

- Source layout：**40 passed**。新增覆盖入口存在性、外部 cwd 下项目根
  解析、所有 Python 助手使用公共环境、实际包导入、旧根入口身份拒绝、
  Browser 持久目录、Tunnel 默认/相对覆盖路径、停止状态和 PowerShell 解析。
- Source layout、Elevation、Browser/Download、Lifecycle、Execution Runner、
  Runner transport 专项：**91 passed**。
- 最终完整 pytest：**802 passed in 129.42s**。
- `git diff --check` 通过。

完整 pytest 使用 Conda `plus-local-agent` Python。默认 Codex PowerShell 7
宿主环境下曾得到 **799 passed / 3 failed**：Python 直接调用 Windows
PowerShell 5.1 时继承了 PowerShell 7 模块目录，Security/Diagnostics 的
自动加载失败。三个失败是 Get-WinEvent、Get-AuthenticodeSignature 和
Get-Acl。对应结构化 PowerShell 脚本与 HEAD 完全相同；单独调用宿主
cmdlet 也能复现。仅在测试命令的进程环境中将 `PSModulePath` 设为
Windows PowerShell 5.1 的用户/ProgramFiles/SystemRoot 模块目录后，
三个测试及完整 suite 均通过。未修改系统环境或相关业务实现。

## 真实启停与健康验证

- 真实 `start_all → stop_all → start_all` 已执行通过，两次均输出完整
  Broker、Lifecycle、Runner、Browser、HTTP、Tunnel 就绪链及 `PLA READY`。
- 冷启动也从 `%TEMP%` 执行，入口使用绝对路径，父进程 `PYTHONPATH`
  为空，验证不依赖项目 cwd 或偶然继承的源码路径。
- 停止核对覆盖全部记录的组件、Provider、Node、console 子进程。
  最后一次停止：**41 个记录进程全部退出，三个端口无监听，没有 stale
  running 状态**。Broker PID 为 0；Runner 状态 stopped；Browser
  runtime/keeper ready 文件已清理。Runner 保留历史身份字段，但对应
  进程已经退出。
- 独立执行 `restart_pla.ps1 -ExpectedPid ... -Json` 成功，HTTP PID 更新，
  旧 HTTP 及全部 24 个子进程退出，Browser 和 Tunnel 保持原 PID。
- 实际 MCP 初始化和工具目录读取成功，返回 23 个工具；Browser/Keeper
  ready、Runner production_ready 均为 true；Tunnel `/healthz` 返回 200。
- 最终再次冷启动后保持 PLA 运行。

证据位于忽略的 `state/`：`startup_regression_e2e.log`、
`startup_regression_final_stop.log`、`startup_regression_final_start.log`、
`startup_regression_pytest_windows51.log`。

额外 HTTP restart 首次在长执行会话中校验时，该工具会话意外结束，未
取得结果；随后独立执行 restart、核对旧进程树、实际 MCP/健康探测及
再次启停均通过。该中断不计为成功的 restart 证据。

## 剩余边界

当前审查与测试范围内没有未解决的目录迁移启动回归。仍须注意上述
PowerShell 7/5.1 宿主模块环境差异，以及测试不能与正式 Runner 并行
争用项目互斥锁。此验证覆盖本机链路和 Tunnel 本地健康，没有扩大为
远端客户凭据/控制平面调用验收。工作区仍保留原来的未提交整理状态。
