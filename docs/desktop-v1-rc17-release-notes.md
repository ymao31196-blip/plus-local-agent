# PLA Desktop v1.0.0-rc.17（Windows x64 预发布）

**候选草稿，尚未发布：GitHub CI 源码回归失败，安装包上传已取消。请继续使用 RC16 的公开下载；本草稿的资源不完整，不可分发。** 诊断见 [RC17 验收记录](desktop-v1-rc17-validation.md)。本地通过记录不代表 CI 通过。

RC17 增加默认关闭的桌宠，保留 RC16 的 Skill Library 与组件来源管理，并改善宽屏布局。

- **桌宠可选启用**：在「组件与 MCP 插件」中启用或停用。随 Runtime 启停，只读本地工具活动，显示工作、等待、空闲和失败状态；不会增加 ChatGPT 工具。缺少素材时保持停用，桌宠启动失败不阻止 Runtime。
- **自有形象素材**：安装包不包含第三方角色图片。请准备自有或获授权的 32 张 PNG；可让 ChatGPT 通过 PLA 的受控文件能力协助替换，无需素材导入界面。路径和规格见[桌宠说明](desktop-agent-pet.md)。冻结 Runtime 包含 Tkinter，用户无需另装 Python/Tk。
- **组件管理与布局**：保留来源发现、安装、启停、卸载、Skill 管理及现有组件交互优化；宽屏内容在侧边栏右侧居中。
- **独立 DOCX MCP 淘汰**：不再提供旧 DOCX 清单或安装依赖。WPS Office、PDF、MarkItDown 及通用 DOCX 文件文本读取继续保留；构建时清理旧资源残留。PDF 文本读取依赖纳入冻结 Runtime。
- **兼容性修复**：损坏的桌宠偏好不会阻断 Desktop 状态查询，手动关闭桌宠后可重新启用。外部伴随型 MCP 已有的能力验证不引入新 Hook，也不把实验 MCP 打包为内置组件。

下载本次 Release 的 Windows x64 安装程序、`SHA256SUMS.txt` 和 `build-manifest.json`。运行前使用 `Get-FileHash -Algorithm SHA256 -LiteralPath <安装包路径>` 核对哈希。安装包未签名，自动更新关闭；延续现有预发布方式。

本轮证据和未验证事项见 [RC17 验收记录](desktop-v1-rc17-validation.md)。全新 Windows 10/11、真实系统重启、完整托盘行为及远程 ChatGPT/Tunnel 的本轮验收不由本地测试替代。可选 WPS/Office 能力仍依赖目标机器安装对应应用。

本机已有 RC10，安装验收脚本为保护既有安装而在操作前停止，本轮未执行真实 NSIS 安装/卸载或升级。已验证供安装器打包的原生 EXE/资源启动、MCP 和生命周期；已有登录启动项同样保留，未进行该项实测。

旧版说明保留在 [RC16 Release Notes](desktop-v1-release-notes.md)。
