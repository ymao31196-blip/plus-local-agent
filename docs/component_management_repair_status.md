# 组件管理修复进度

2026-10-11。当前候选 RC.11；源码、打包与实际安装证据分开记录。

## 当前改动

- 统一入口「组件与 MCP 插件」现在只有三个页签：本机组件、仓库管理、浏览组件。仓库组件版本、完整性检查和卸载与原有启停、热插拔、清单编辑、完整工具参数同卡展示。保留原表单 DOM 与草稿。缺失清单的安装记录仍可查看和处理。
- 按用户要求删除可选自开发 UI、三个管理 RPC、源码准备模块和发行包源码快照。既有工作区授权与源码目录不做自动迁移或删除。
- 常用组件包改为六项，不包含 DOCX。单独 DOCX 安装和既有文件保留。
- 修复卸载后空资产目录和 Python 字节码缓存阻碍重新安装的问题；仅移除明确拥有的空目录，Python 使用 -B，不删除用户新增文件。增加目录重定向检查。
- 修复市场搜索框过窄和分类/文字筛选相互覆盖；HTTPS 资产路径正确编码；列表加载错误不再显示为空列表。

## 当前验证

- PASS：仓库路径、摘要、所有权、回滚、目录重定向、空目录清理和合并目录信息 18 项。
- PASS：实际 Edge DOM 验证三页签、合并完整性检查及 SHA 绑定卸载、草稿/DOM 保留、仓库浏览联动、搜索宽度及组合筛选。管理 RPC 为模拟，不能代替真实安装验收。
- PASS：现有组件界面 smoke 回归；常用包、桌面管理、用户组件、版本一致性等选定回归 50 项（与上述仓库项重叠）。
- PASS：源码真实 Python 仓库安装、Broker 调用、升级、停用、卸载，再次安装/启用/调用/卸载，共 7 项；证据 C:\Users\26286\AppData\Local\Temp\pla-repository-e2e-77mytdg0\repository-evidence.json。
- PASS：RC.11 全量源码回归 931 项，无警告，174.74 秒；日志 `.desktop-build/rc11-source-tests.log`。
- PASS：RC.11 冻结运行时实际 Python 仓库安装/升级/Broker 调用/卸载/再次安装 7 项，以及 Node 实际安装/调用/卸载 2 项。证据分别为 `C:\Users\26286\AppData\Local\Temp\pla-repository-e2e-kerll6b1\repository-evidence.json` 和 `C:\Users\26286\AppData\Local\Temp\pla-node-repository-e2e-05cdd3ti\node-repository-evidence.json`。复现工具 `desktop/verification/repository_frozen.py --resources desktop/src-tauri/resources --fixture python`（或 node），仅使用新 Temp 数据。
- PASS：完整构建退出码 0，固定资源 MCP 验收通过；发行资源中已无 `developer-source.zip`。候选 `dist/desktop-v1/PLA Desktop_1.0.0-rc.11_x64-setup.exe`，源码提交 `fd0448bc8d29092229afdb92101acf9ff5a493a2`，构建时工作树干净；SHA-256 `41423a8b54bce58bdbe146c5d6cea9f1b9070dcef671b90649674a05f17bcbeb`。
- 最终两处维护页提示统一为「本机组件」后，Edge DOM 回归再次通过；桌面与安装包重新构建成功。最终封装使用 SkipRuntime/SkipAcceptance，运行时二进制 SHA-256 2459e912fa0eaac48a281bbfb72f82709f7a7a4026364b557810e3cc874d0775 与已通过真实验收的载荷一致，不重复执行未改变的 Runtime 验收。
- 未执行新版安装：用户负责重装。尝试独立临时原生 UI 副本时，正式 RC.10 正在运行，单实例机制使测试副本无法取得 WebView2 调试目标，验收脚本退出码 1，未执行界面检查（原始记录 `.desktop-build/rc11-native-ui/native-ui-report.json`）。Computer Use 确认正式版 Runtime 已就绪，保留其服务运行。RC.11 原生 UI/实际升级验证待用户安装后进行；不把 RC.10 的 13/13 代替本轮验收。

## RC.10 历史安装证据

RC.10 源码全量 929 PASS，无警告；构建来源 3668134d8cac4fe3c7f222875300267f78d2b876，干净工作树。安装包 SHA-256 bf0e6c865a2f1c47798cfb06781b74568dc609fb9ea72f4daab6bf62191c42b4。

已实际升级 D:\PLA Desktop；桌面配置、Provider 偏好、加密凭据及工作区配置四文件摘要均保留，安装后二进制与构建一致。已安装原生 UI 验证 13/13；冻结 Python 与 Node 仓库真实 MCP 安装/调用/升级/卸载通过。Computer Use 实测正式应用添加仓库成功并显示 Test Echo Provider，原 undefined 错误不再出现。

上述 RC.10 证据不自动算作 RC.11 验收。用户随后已更新到 RC.11；桌面程序和 Runtime 与候选完全匹配。原生页面确认三页签、自开发入口移除、常用包无 DOCX、仓库安装信息和操作合并；实际点击完整性检查返回 installation_verified。它不替代完整原生生命周期和远程验收。

## 发行边界

未验证新候选的 Tunnel/ChatGPT 链路、干净 Windows 10/11 和 OS 重启；未签名，自动更新关闭，未公开推送或发布。组件依赖隔离并非 OS 沙箱，完整性检查并非真实 MCP 连接证明。

## 后续现场诊断

用户已自行完成 RC.11 更新。Computer Use 和文件摘要对比证据见 `.desktop-build/rc11-installed-observations.json`；原生测试副本先前因单实例未启动的失败记录保留。正式 Runtime 与多个 Provider 曾观察就绪，三个工作区存在；未替用户停止服务或改密钥。

重启后的正式 Tunnel 客户端仍持续收到 401 invalid_api_key。DPAPI 保存值与用户此前提供的 test.txt 密钥一致，不含掩码或空白。PLA-TEST 指向同一桌面 Tunnel，返回 tunnel_client_not_seen；plus-local-agent 插件实际通向另一套源码 root，不能计为桌面远程通过。需要有效 runtime API key 才能继续该链路验收。
