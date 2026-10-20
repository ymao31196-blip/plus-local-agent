# RC16 发布验收记录（2026-10-20）

本记录只描述 RC16 本轮证据。安装包对应的最终提交、干净状态与 SHA-256 由本次 Release 附件 `build-manifest.json` 和 `SHA256SUMS.txt` 给出；二进制验收汇总在 Release Notes「本轮验证」中列明。

## 源码与界面

- 使用项目 Python 3.11 环境执行完整 pytest 回归。首次 954 通过、2 失败，均为新增活动模块/构建门禁对应的测试清单滞后，已修订并专项复测通过；最终完整回归 **956 项全部通过（165.12 秒）**。
- RC16 专项 78 项通过：版本一致性、17 项能力清单/权限契约、默认拒绝本地读写、工作区删除与写权限收缩时撤销失效 Skill 授权、内置 Wheel 篡改拒绝、安装失败回滚、活动仅记元数据等。
- 四项 Node 界面验收通过：`components-ux-smoke.cjs`、`skill-quick-source-smoke.cjs`、`activity-toast-smoke.cjs`、`verification-status-smoke.cjs`。Skill 页面在真实 Edge DOM 中运行，管理 RPC 使用测试替身；这不是已安装原生 WebView 验收。
- 真实隔离数据目录的 `chatgpt_skill_bundled_e2e.py` 8 项检查通过：默认内置包计划、过期哈希拒绝、后台安装、真实安装回执、17 项真实能力发现、来源同步搜索、正文读取、启停不重启 Runtime。这里的 ChatGPT 路径指同一管理能力 Broker 调用，不代表实际 ChatGPT 云端/Tunnel 本轮端到端测试。
- Wheel 在临时的已跟踪文件快照中构建，SHA-256 与固定值 `5fee6f1cfd32c896b6d951e69b9631c167398d26e04bccfa9f18536d67611909` 一致，独立 Skill Library 仓库未修改。

## 已知失败分类

默认系统 Python 缺少 pytest，打包 venv 缺少可选 lxml，首次启动测试分别无法执行/收集。改用已有的完整项目环境解决，没有删除测试或降低安全策略。历史日志中的 PowerShell Get-WinEvent/Get-AuthenticodeSignature/Get-Acl 失败不作为本轮结果；本轮首轮完整执行这些测试通过。

新增模块和构建命令数量的两个测试清单失败属于本次测试维护问题，已显式补充模块和门禁计数，保留原断言范围。修订后的完整回归已重新执行，956 项全部通过；任何后续关键失败均阻断发布。

## 发布边界

README 下载版本、npm/Cargo/Tauri/Runtime 版本均为 1.0.0-rc.16。只提交审核的源码、测试和文档；本地配置/凭据、state、个人 Skills、缓存、resources、target 与 dist 不入 Git。构建不使用 `-SkipRuntime` 或 `-SkipAcceptance`。

本轮未执行：全新 Windows 10/11 安装矩阵、真实系统重启、完整托盘、真实 ChatGPT/Tunnel 外网验收。保留为预发布限制，不能由历史通过记录替代。安装包未签名，自动更新关闭。
