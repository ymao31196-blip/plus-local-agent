# PLA Desktop V1 — install, use and final acceptance

This handbook separates local packaged checks from the required clean Windows and real ChatGPT acceptance.

## 安装与首次使用

1. 从最终交付目录取得 `PLA Desktop_1.0.0-rc.2_x64-setup.exe` 与 `SHA256SUMS.txt`，核对安装包 SHA-256。它是未签名测试候选，不是已签名正式发行。
2. 双击安装包，以当前用户安装。无需安装 Python、Node 或 Rust。若系统没有 WebView2，安装器使用内置 Microsoft bootstrapper 联网安装；应确保可以连接 Microsoft。
3. 打开 PLA Desktop。首次启动进入「首次配置与连接」。用户数据默认位于 `%LOCALAPPDATA%\io.pla.desktop`。
   如有既有源码安装，可填写其目录并检查布局。该操作仅报告文件是否存在，不读取或导入凭据，也不接管现有服务。
4. 在官方 Tunnel 页面创建或选择此电脑专用 Tunnel，确认组织角色具备管理/使用权限。创建 runtime API key。将 Tunnel ID 和 key 输入界面并保存；密钥以当前 Windows 用户的 DPAPI 加密保存。
5. 打开「授权工作区」，填写现有目录的绝对路径及名称，明确选择读取、写入、受控执行权限。不要授权私人凭据目录或过大的目录范围。移除授权不会删除文件。
6. 点击「启动 Runtime」，确认状态为「MCP 已就绪」。运行「真实本地工具验证」，确认本地 MCP 诊断通过。
7. 点击「连接 Tunnel」，等待「Tunnel 已就绪」。它仅表示本地 Tunnel readiness 通过，不能替代 ChatGPT 授权与远程调用验收。
8. 按照官方说明，在 ChatGPT 侧添加相应的 Tunnel 应用或 MCP 连接，并完成账户授权。
9. 在「版本与维护」可启用打包的浏览器组件。停止服务后保存偏好，再启动 Runtime。需要系统 Microsoft Edge。浏览器使用自己的配置目录与端口。
10. 查看日志或运行诊断，报告保存于用户 `logs` 目录。关闭窗口后应用留在托盘；彻底退出请使用托盘「退出」。登录启动默认为关闭，可自主开启。

官方入口：[Tunnel 管理](https://platform.openai.com/settings/organization/tunnels)、[API Keys](https://platform.openai.com/api-keys)、[连接说明](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。ChatGPT 的实际账户权限和连接界面应以该账户当前可用功能及官方说明为准。

## 真人 ChatGPT 验收 — 每项记录证据

使用一个空的测试目录作为工作区，授权读取、写入与受控执行。在 ChatGPT 连接 PLA 后：

- 请求列出测试目录，并读取其中的一个测试文本。
- 请求创建 `acceptance.txt`，内容为一个唯一测试标记；读取确认，再修改标记并读取确认。
- 请求通过 PLA 的 `run_process` 运行 `python --version` 或一个打印标记的 Python 进程。
- 请求对两个测试文件应用带 SHA-256 的 `apply_changeset`，确认两个文件同时按预期修改。
- 如启用浏览器，请求导航到测试网页并返回语义快照。
- 请求读取授权目录之外的文件，以及执行未授权程序；应看到明确拒绝。

记录 ChatGPT 对话时间、工具名称、输入、脱敏输出及本地诊断报告。只有经 Secure MCP Tunnel 发生的真实远程调用可记为 Tunnel E2E PASS；只有实际 ChatGPT 调用可记为 ChatGPT E2E PASS。本地协议客户端通过不能替代这些项目。

## 干净 Windows 10/11 x64 验收

使用未安装 PLA 开发环境的独立 Windows VM 或测试机，检查安装前没有 Python/Node/Rust/PLA 服务。逐项标记 PASS、FAIL、BLOCKED 或 NOT TESTED：

| 项目 | 必须保存的证据 |
| --- | --- |
| GUI 安装完成 | OS 版本、安装前组件情况、安装日志与应用目录 |
| 首次配置向导 | 向导截图和独立用户数据目录 |
| 工作区权限和配置保存 | 配置持久化结果，报告不含明文 key |
| Runtime 就绪 | 实际 MCP 工具目录及诊断调用结果 |
| Tunnel 实际连接 | `/readyz`、脱敏日志与远程 MCP 调用 |
| ChatGPT 实际工具调用 | 上节测试的真实 ChatGPT 对话证据 |
| 停止、重启和重新打开 | 配置保留，已有开发者进程不被接管 |
| 重复启动与托盘 | 只有一个主实例，打开/状态/启动/停止/退出有效 |
| 异常恢复 | 错误 key、断网、Tunnel 退出、Runtime 崩溃、端口冲突 |
| 系统重启 | 登录启动按偏好生效，用户配置保留，无异常残留 |
| 卸载 | 应用移除，用户配置及授权工作区文件保留 |

当前执行环境的开发机隔离安装测试不能自动把此表标为 PASS。系统重启测试需要在可重启的独立环境完成。

## 升级、卸载和维护

自动更新禁用。手动升级前彻底退出应用，验证新安装包来源和 SHA-256，再运行安装器。用户配置与工作区位于安装目录之外。升级失败时重新安装先前已验证候选，保留用户配置，必要时使用事先保存的配置备份；不能承诺未经测试的自动回滚。

在 Windows「已安装的应用」中卸载 PLA Desktop，或运行安装目录中的卸载程序。**保留默认未勾选的用户数据删除选项**；这样配置、日志和默认内部工作区保留。授权的外部工作区从不属于安装器删除范围。卸载会移除此安装拥有的登录启动注册项。

## Developer rebuild

Build prerequisites: Windows x64, Python 3.11, Node/npm, Rust 1.90.0, a working MSVC or GNU toolchain. These are build-machine requirements only.

```powershell
.\desktop\packaging\build.ps1 -Python <python-3.11-executable>
```

For this host's GNU toolchain, prepend its compiler directory and `%USERPROFILE%\.cargo\bin` to the build process PATH, or pass `-ToolchainBin`. The script locks Python, npm and Cargo inputs, verifies downloaded binary hashes, freezes Runtime, executes packaged MCP acceptance, creates NSIS, and writes `dist/desktop-v1/SHA256SUMS.txt` and a source manifest. `-SkipRuntime` and `-SkipAcceptance` are developer convenience options and must not be used as evidence that skipped phases passed. CI uploads local candidates; it creates no public Release.

## Known external dependencies

Tunnel account/organization access, runtime credentials and ChatGPT authorization are personal/account operations. They cannot be automatically supplied by the app. Office and Skills external environments are not bundled, so their desktop execution acceptance remains incomplete. Windows Authenticode and Tauri updater signatures are separate; neither is configured. A trusted public release additionally needs the owner's distribution/license decision, a certificate/signing route, and all P0 acceptance evidence.
