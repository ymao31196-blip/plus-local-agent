# PLA Desktop 登录自启动与签名更新交接

## 实施状态（2026-11-03）

代码位于 `D:\AI_Tools\plus-local-agent`，目前仍是**未提交的开发工作树**。这次更新涉及上一阶段的登录自启动和本阶段的签名更新链路。

- **登录自启动**：Windows HKCU Run 项使用 `"pla-desktop.exe" --autostart`；登录后隐藏主界面、拉起Runtime，配置有Tunnel ID和DPAPI凭据后尝试连接Tunnel。GUI会核查真实注册表项。安装升级后，如果保存的偏好为开启，首次再次启动应用时会恢复当前安装路径的启动项。首次用户勾选操作仍然需要完成一次。
- **未签名构建**：通过Python读取GitHub稳定版信息，给出手动更新链接，**绝不执行远程下载的安装包**。
- **签名构建**：仅当构建时提供可信的Tauri更新公钥，Rust端才注册Updater；使用固定HTTPS地址查询`latest.json`，默认拒绝降级。用户点击“安装已签名更新”之后，Rust在下载前后分别检查Runtime、Tunnel及其他管理进程、组件安装锁都已停止，同时只读检查持久化任务数据库中不存在`queued/running`任务；任务库损坏或不可读取时拒绝安装。Tauri会在安装时再次校验字节签名，然后由Windows安装器替换程序。下载后发现服务重新启动将取消安装。
- **权限边界**：`desktop/src-tauri/capabilities/main.json`未授予任何`updater:*`前端插件权限；网页只可调用经过进程状态检查的两个自定义Rust命令。
- **发布产物**：本项目配置为`createUpdaterArtifacts: true`，因此Tauri v2将产出`PLA.Desktop_X.Y.Z_x64-setup.exe`安装包及其`.exe.sig`签名（源码产物文件名有空格，发布时会统一重命名）。对应生成`latest.json`、`SHA256SUMS.txt`和`build-manifest.json`。只有将`createUpdaterArtifacts`设为`"v1Compatible"`才产生`.nsis.zip`；当前方案不采用该格式。Windows Authenticode证书与Tauri更新签名相互独立，后者不会消除SmartScreen警告。

## 首次签名密钥准备（发布者自行操作）

**不要把私钥、口令发送到ChatGPT、提交Git或存到PLA工作区内。** 在可信的本地PowerShell终端使用Tauri CLI生成一对密钥，并把私钥离线备份：

```powershell
Set-Location "D:\AI_Tools\plus-local-agent\desktop"
npm.cmd ci
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.tauri" | Out-Null
npx.cmd tauri signer generate -w "$env:USERPROFILE\.tauri\pla-desktop.key"
```

保管`pla-desktop.key`私钥和对应口令；`pla-desktop.key.pub`可以公开，在本构建中只会嵌入公钥。私钥丢失意味着旧客户端难以继续信任以后发布的更新，切勿每次发版重新生成密钥。

## 构建前置步骤

`desktop/src-tauri/Cargo.toml`新增了`tauri-plugin-updater`，`Cargo.lock`已更新。先前执行`cargo check`曾因GNU工具链缺少`dlltool.exe`、`as.exe`而失败；用户已安装/恢复工具链。`build.ps1`会优先识别现有PATH、旧`.desktop-build/toolchain/mingw64/bin`以及`C:\msys64\mingw64\bin`。仍需要在本机确认Rust编译真正出现`Finished`，并在提交前检查`Cargo.lock`差异与测试结果：

```powershell
Set-Location "D:\AI_Tools\plus-local-agent"
cargo.exe update --manifest-path "desktop\src-tauri\Cargo.toml"
cargo.exe check --manifest-path "desktop\src-tauri\Cargo.toml"
```

源码版本已统一预设为`1.1.0`，包括`Cargo.lock`中的根包版本；这不代表已发布。必须在本机确认Rust编译成功，再考虑发布。当前Git有大量其他功能开发修改，正式签名构建要求干净工作树，不能为了通过检查直接丢弃、混入未审查改动。

本地签名构建后续到达Rust许可证收集阶段时，曾报告`tauri-plugin 2.7.2`缺少许可证文本。已从`tauri-plugin-v2.7.2`官方标签保留MIT许可证原文，并加入严格SHA-256绑定的补充许可证清单；许可证工具现在能保留并检查Cargo锁定的未展开`.crate`缓存。新增针对性测试通过，但实际完整许可证清单仍需在原Windows环境中验收。

## 签名测试构建和正式发布

本地开发阶段使用`-LocalSignedTest`，允许未提交工作树参与签名**测试**，产物隔离到`dist\\desktop-local-signed-test`并写入`DO_NOT_PUBLISH.txt`，不允许直接作为GitHub正式发行版。正式签名构建不带此参数，继续要求工作树干净。

Tauri CLI生成的`.key.pub`为Base64编码的minisign公钥。`build.ps1`会在本机解码并检查标识和公钥行，但嵌入客户端的仍是原始Base64字符串。此前误将`.pub`视为明文`RW...`格式的校验已修复；不要重新生成密钥，更不要传输私钥或口令。

`cargo build --release`与NSIS安装器已在本机成功生成，但随后Tauri CLI报`failed to decode pubkey: Missing comment in public key`：先前模板`tauri.updater.conf.json`的`plugins.updater.pubkey`为空，Rust编译期公钥不能替代CLI签名配置。修正后，`build.ps1`在`.desktop-build/tauri.updater.generated.conf.json`中生成含已校验公钥的本机临时构建配置，再通过`--config`提交给Tauri CLI，仓库模板仍不存公钥内容。此前还出现`__TAURI_BUNDLE_TYPE`标记缺失警告。经逐版本比对，根因是Rust端Tauri固定为2.12.2，而npm CLI仍固定2.8.4：旧CLI在Windows二进制中查找`.taubndl`节，新Rust已改为`__TAURI_BUNDLE_TYPE_VAR_UNK`标记。新CLI 2.12.2与Rust 2.12.2使用一致机制，`desktop/package.json`已更新为2.12.2；需要在本机运行`npm.cmd install --save-dev --save-exact @tauri-apps/cli@2.12.2`生成正确`package-lock.json`。构建脚本加入了最前置锁文件校验，避免再次消耗大量时间后才发现版本失配；仍保留标记缺失的拒绝发行保护。若只想验证本次签名阻塞，可暂时加`-SkipRuntime -SkipAcceptance`利用之前已验证的资源缓存；正式发行仍须完整构建和验收。

近期一次重试触发`os error 1455`（Windows页面文件/提交内存不足），伴随`STATUS_STACK_BUFFER_OVERRUN`及`cannot find crate for std`等次生错误，并非Updater签名缺陷或Rust源码缺失。本机观测到内存负载96%、可用提交内存约1.79GiB，`PBZ.exe`两进程合计专用提交内存约11.79GiB。进行Release构建前先正常退出大型游戏，必要时关闭其他重负载程序。`build.ps1`现默认将Cargo并发限制为2，可追加`-CargoJobs 1`进行更低内存压力的签名测试；构建结束恢复原有`CARGO_BUILD_JOBS`。如进程清理后仍触发1455，再检查Windows系统管理页面文件设置和磁盘剩余空间，不应删除Rust标准库或贸然重装Rust。\n\n在本机PowerShell中以受保护的环境变量提供私钥文件路径；下列命令只保留在本机执行，不在聊天或日志中输出口令：

```powershell
Set-Location "D:\AI_Tools\plus-local-agent"
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.tauri\pla-desktop.key"
$securePassword = Read-Host "Tauri signing password (leave blank if none)" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
try {
    & ".\desktop\packaging\build.ps1" -UpdaterPublicKeyFile "$env:USERPROFILE\.tauri\pla-desktop.key.pub" -LocalSignedTest
} finally {
    Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
}
```

正式发行前要求工作树干净，私钥和公钥缺失则提前拒绝，不能以未签名安装包冒充自动更新。验收通过后重新运行相同命令但**去掉`-LocalSignedTest`**；正式候选产物位于`dist\desktop-v1`。构建仍需现有Python、Node、Rust、Skill Library及Windows NSIS工具链。

将上述稳定版资源上传到GitHub Release，Tag使用`desktop-vX.Y.Z`，设为正式发布（**不是pre-release**）。`latest.json`、`.exe`与`.exe.sig`需要位于同一个Tag的Assets中，Tauri客户端固定从官方仓库的`releases/latest/download/latest.json`获取元数据。用`desktop-v1.1.0`首次引入更新功能时，现有`1.0.0`用户需手动安装一次；要验证真实的客户端自动更新，至少还需一个更高版本（如`1.1.1`）的签名测试包。不要在验证前发布稳定版。

## 验收状态与边界

已通过本地Python语法检查、模拟Release发现、静态权限与流程约束、签名元数据生成/拒绝缺签名等轻量测试。v1.1.0的Rust Release和NSIS已在用户Windows机器上编译完成，本地签名测试产物包含安装器、`.exe.sig`及`latest.json`；用户于2026-11-06反馈真实安装、系统重启及Windows登录自启动均正常。**尚未实测**从v1.1.0向更高版本的签名升级、篡改签名拒绝及全部可选组件集成。首次发布必须从干净Git源码重新生成正式签名产物，不能发布带`DO_NOT_PUBLISH.txt`的本地测试构建；真实签名升级测试应在后续更高版本验证。

现有v1.0.0安装包没有内置Tauri Updater，必须手动安装一次支持签名的新版，后续才能通过签名通道升级。

官方技术参考：<https://v2.tauri.app/plugin/updater/>
