# RC.4 — Provider / Skills / 可选自开发（验收进行中）

用户当前安装为RC.3。此文件记录RC.4候选实现和真实证据，未声明新安装包已完成验收。

## 行为

- 所有11个已审阅Provider清单/规格/适配器进入资源层，依赖和新增清单位于独立用户组件目录；保留新清单、用户更改和启动偏好。
- 已有清单支持原文件hash与新内容hash的双重预览校验，保存不自动重载；内置文件仅在仍与上次生成hash相符时迁移到新安装路径，手工修改保留。
- 启用、停用、重载、重扫通过原权限Broker；停机时只保存启动选择，并明确未连接。新增插件可从GUI或`runtime.provider_import`先预览SHA-256再注册，再单独启用。三种原有transport、工具白名单及原有权限不变。
- 完整`capability_catalog`展示全部工具及输入/输出Schema、风险、确认/事务要求、路由和实际可用状态，没有100项搜索截断。
- 固定可选组件安装任务按需从官方源下载uv0.12.24，校验发布归档和执行文件双hash后使用；安装包不再分发该二进制。私有管理Python3.11.9、已审阅规格与Node22.16.0/npm；任务、日志、失败状态可见。MCP连接状态与依赖安装成功独立呈现。
- Skills页面覆盖17项新接口，含三种来源local/github/git、搜索正文/资源、同步、验证、启停、两步创作、改名/归档/恢复、发布方案/逐文件本地仓库转移。应用仍需原服务版本hash和显式确认；没有自动push。
- Skills读写范围从工作区root再作独立选择，默认空。MCP stdio只传必要上下文，不传Tunnel/API key。
- 自开发默认关闭，授权专用`pla-development`源码root；可从安装资源准备公开源码快照到空目录，逐文件校验，不复制私人配置和工作区。检测到Git时创建无远程的本地基线。准备源码不自动授予权限，关闭授权保留文件。安装资源继续只读，候选须验收后显式安装切换。

## 已执行

- 全部源码回归841 PASS，耗时177.02秒；旧Agent自动测试已移到独立工作区和独立Runner状态，避免修改仓库手工样例或借用原Runner。
- 新管理/导入/根权限/默认关闭/撤销、真实MCP热插拔与持久启动专项通过；主Runtime PID保持。独立源码快照hash、目标绑定、越界和文件保留专项通过。
- 独立可选组件实际安装和Skill MCP验收7组PASS，报告`.desktop-build/components-mcp-report.json`。17个真实描述符、正文/资源/启停阻止、草稿写入、改名、归档恢复、Git仓库本地复制均独立核对文件；不等同于冻结和ChatGPT验收。
- 初版RC.4冻结Runtime同样完成独立安装与Skill MCP七组PASS，报告`.desktop-build/rc4-components-frozen-report.json`；初版安装包构建成功，但source_dirty=true，仅用于开发验收。现正在纳入最后的清单编辑、UV按需下载和大正文RPC容量改动，准备从干净提交重新构建，旧候选不作为最终交付。
- 官方UV下载在冻结客户端中曾返回403；为客户端设置明确产品User-Agent后官方请求200，校验完整下载成功。源码manager真实自动下载/双hash/独立Python与Skill安装、17项接口工作流、60080字节UTF-8中文草稿8组PASS，报告`.desktop-build/components-mcp-report.json`。修复必须重新冻结后再验收，不能使用旧冻结结果代替。
- 原开发服务8766/8931/18081 PID30744/19464/20620保持；当前用户RC.3 Runtime18766/Tunnel18082仍运行，未迁移个人Skills或凭据。

## 待执行/限制

RC.4冻结构建、真实GUI与冻结MCP、配置保留更新、ChatGPT新增能力复测尚待执行。uv及新安装组件许可证需完成最终清单。Skill Library独立项目没有明确再分发许可证，目前仅支持用户选择自有v0.5.0源码或wheel安装。Office的artifact-tool不借用Codex环境且未随包提供；Git/Rust是可选组件与自开发的系统前提。

干净Windows、实际系统重启、Windows代码签名/正式发布条件仍未满足；保持未签名候选和禁用自动更新。没有公开push或release。

## Skill 列表补充

保留 Skills 管理的全部工作流，新增独立 Skill 列表入口。进入时通过真实 skill-library.states 与 sources 读取全部已缓存 Skill，包含单项及来源停用状态；支持名称/来源文本筛选、来源与状态筛选、启用项完整正文阅读，以及跳转原管理工作流。未同步来源不伪装成已缓存目录；缺失服务时提供明确提示。页面切换回到顶部，避免继承前页滚动位置。



最新完成情况以 [RC.4 验证记录](desktop-v1-rc4-followup.md) 为准：干净提交构建、冻结 Skill 8 组、原 MCP 10 项、异常 7 项、真实断线恢复 4 项、独立 Skill 列表 Computer Use、原位置 NSIS 升级和配置保留、正常 Windows 启动及 Runtime/Tunnel 恢复均已执行。上文各阶段“待构建/待冻结/RC.3仍运行”是阶段记录，现用户安装已为 RC.4。RC.4 ChatGPT 新增能力、干净 Windows 和系统重启仍未验证。
