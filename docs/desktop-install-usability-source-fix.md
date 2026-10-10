# PLA Desktop — MCP安装与Skill列表可用性修复（源码阶段）

日期：2026-10-10

## 问题和原因
- RC.4的「安装 / 更新」按钮仅选择Provider并生成计划；真正执行还需要在安装区域二次确认，且不自动轮询。后台安装错误会在顶部通知中出现，用户停留在较低的安装区域时容易误认为没有响应。
- 「清单已注册」和「执行依赖已安装」此前未明确分开。WPS Office在源码依赖目录尚未存在时可被启用，最终出现WinError 267。
- Skill Library是需用户提供v0.5.0源码/wheel的独立组件；未预装、未启用或未同步来源时，Skill列表无法读取。因独立项目再分发许可未确立，不能直接随包分发其源码或隐式导入私人Skill。

## 已写入源码
- src/desktop_runtime/manager.py：Provider目录增加install_supported、directory_exists、execution_files_present、installation_recorded；启用前检查执行文件和工作目录；安装任务返回真实退出状态、近30条脱敏日志和已有收据，退出码0且缺收据不能伪装安装成功（浏览器已打包组件例外）。
- desktop/ui/components.js：分开显示缺依赖、未安装、实际MCP连接；未安装时不提供启用/重载；安装按钮明确导航至预览区域；预览/确认错误在安装面板原地显示；安装开始后自动轮询，完成/失败后刷新Provider目录；按钮支持检测重新确认路径变化。
- desktop/ui/index.html：安装任务即时状态与首次使用Skill Library提示；Skill列表新增跳转安装及经现有Skill授权与Broker执行的本地Source新增/同步入口。未安装或未同步不会显示假Skill内容。
- tests/test_desktop_install_usability.py：缺失WPS工作目录阻止启用、缺安装规格时不宣称可安装、任务回执/日志/退出状态、前端HTML/JS控件契约。
- desktop/verification/components-ux-smoke.cjs：新增Node标准库+mock DOM可重复模拟前端真实事件处理，覆盖预览→确认安装→状态轮询、WPS未安装时禁用启用按钮、Skill来源注册→同步→目录刷新。
- .github/workflows/desktop-build.yml：在NSIS构建之前增加上述UI smoke验证；CI仍需要真实Windows环境运行，脚本语法和本次mock执行逻辑在隔离V8中已检查。
- 发行候选版本字段统一更新为1.0.0-rc.6：Tauri、Cargo.toml/Cargo.lock、npm package/package-lock以及Python Runtime VERSION保持一致；增加test_desktop_release_version_consistent_across_build_systems自动校验。旧安装版及RC.5安装包保留原状。

## 已实际执行
- 完整源码pytest：847 passed in 140.69s（运行开始时已经包含前三项新增回归；随后新增第4项静态契约测试并单独通过）。
- 最终专项15项测试：15 passed in 14.86s。
- Python py_compile：PASS。
- 当前用户安装的RC.4运行环境没有被替换，用户凭据、Tunnel和Provider配置未改动。

## 未完成，不能冒充通过
- 该轮的Tauri/WebView2安装版构建、安装/原生真实点击和Skill来源现场加载：NOT TESTED。当前PLA执行白名单不允许node.exe/powershell.exe，本轮不能通过受控进程工具运行Tauri/NSIS构建脚本。
- 未修改发行版本号、未生成新安装包、未commit/push/release。既有Cargo.toml工作树修改在本轮之前就已存在，不归入本轮修复。
- WPS实际Git/npm安装需要在隔离用户组件目录上复测；源码修复只消除了缺目录情况下错误启用与误导反馈，并没有宣称已安装WPS。

## 发行前收尾
1. 在具备构建授权的原生Windows构建环境中审查本轮diff，保留Cargo.toml既存改动，确认选择下一RC版本号，再执行desktop/packaging/build.ps1。
2. 用隔离用户目录启动新安装包，实点MCP安装按钮、预览、确认、安装进度/失败恢复、安装后单独启用。每项检查任务日志、真实文件和实际Capability状态。
3. 用用户已授权的Skill Library v0.5.0源码/wheel，从GUI预览→安装→启用→授权读取root→添加来源→同步→列表读取完整SKILL.md，验证关闭/重启后仍可用。
4. 重新执行WPS从未安装目录开始的Git/npm安装及错误场景，确认WinError 267不再被伪装为就绪。
5. 后续再做正式干净机和系统重启验收，不能继承RC.4历史通过项作为新版本PASS。
