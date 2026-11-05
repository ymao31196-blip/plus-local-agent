# PLA Desktop v1.1.0 — Windows x64

PLA Desktop v1.1.0 introduces opt-in Windows login startup and the first signed in-app update channel. Installation and workspace authorization remain user controlled.

## 主要更新 / What's new

- **开机登录自启动（可选）**：在桌面端启用后登记当前用户的Windows Run项；登录时后台启动Runtime，符合已保存配置时尝试连接Tunnel。可在设置中关闭，正常启动仍保留人工控制。
- **Tauri签名更新通道**：内置更新发现与已签名安装的受控流程。下载与安装使用Tauri的签名校验，升级前检查Runtime/Tunnel、安装锁及未完成任务；不满足安全条件时拒绝继续。
- **组件与工具改进**：统一Office能力入口，更新CHAGENT动态工具路由建议，完善近期工具活动与脱敏故障分类，扩展工作区文件操作、可选Markdown索引和受控网络工具。
- **构建与第三方许可**：对齐Tauri CLI与Rust端依赖版本，保留许可证及源码校验，签名构建要求干净且受审查的源码树；本地测试构建与正式发布产物严格隔离。

## 安装与升级 / Installation

1. 从GitHub Release的Assets下载`PLA.Desktop_1.1.0_x64-setup.exe`及`SHA256SUMS.txt`。对照SHA-256后再运行安装器。无需下载源码、Python或Rust。
2. **v1.0.0不具备应用内签名更新能力，需手动安装v1.1.0一次**，后续版本才可通过Tauri的签名通道更新。
3. 安装包有Tauri Updater签名，但**没有Windows Authenticode发布者代码签名**。Windows仍可能显示“未知发布者”；Updater签名不应被误认为系统级发布者身份认证。
4. 自动更新应由用户明确触发；更新时请保存工作并停止不应中断的任务。不提供自动回滚保证。

## 验收与限制 / Validation and boundaries

- 发布者反馈：本机签名测试安装包已完成安装及真实Windows系统重启，登录自启动表现正常。
- 构建检查：签名测试产物包含安装器、`.exe.sig`与`latest.json`；Tauri CLI/Rust依赖已对齐。
- **尚未覆盖**：从v1.1.0升级至未来更高版本的真实端到端更新、篡改签名拒绝的独立实机复验，以及全部可选组件的逐一集成测试。这些均不应被描述为已经通过。
- PLA不是操作系统安全沙箱。执行权限、工作区Root、组件来源和Tunnel授权仍由用户管理。用户配置与DPAPI保护的本机凭据不得捆绑进发行包。

正式发布须使用`desktop-v1.1.0`标签、干净源码构建，以及`build-manifest.json`中`source_dirty=false`、`publishable=true`的产物；任何带`DO_NOT_PUBLISH.txt`或`local-test-only`标记的测试包都不能上传。
