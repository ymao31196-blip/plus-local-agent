# Skill Library主动发现与单次加载验收记录（2026-10-09）

## 范围与版本

本记录针对PLA的`skill-library` Provider与独立Skill Library源码仓库，检验多来源Skill按任务意图发现、可选单次加载、权限隔离、兼容性及运行时延迟。验收使用用户本地启用的`my-skills-local`来源；`my-skills-github`来源保持禁用。两个仓库分别独立进行源码与测试管理。

PLA实际运行时对外注册17个Skill相关Capability，旧版单来源接口不再通过PLA对外暴露。新版`skill-library.find`具有`include_best_content`布尔参数，默认`false`。传`true`时，`matches`返回可选的候选元数据，并通过`loaded`返回评分最高且可用Skill的完整内容；无候选时无`loaded`。后续若选择的ref与`loaded.ref`相同，使用`loaded.content`即可，无须再次`load`。

## 已有真实运行证据

- 真实Provider热重载成功，状态为Ready，17项工具，`find`参数表包含`include_best_content`。
- 新版单次调用对“论文语言优化”返回`my-skills-local:academic-writing`及7490字符Skill正文；“文档图片素材检索”和“GitHub Markdown公式”分别加载`document-asset-retrieval`和`github-markdown-authoring`。
- 对“天气预报”检索无候选，不产生`loaded`；直接读取禁用来源`my-skills-github:academic-writing`受到阻止（source is disabled）。
- 原先拆分的`find → load`仍有效。既有3轮ChatGPT→PLA对照：单次调用均值约2.19秒，双调用均值约4.38秒，减少约50%。样本数有限，不能视为长期P95或模型整体任务耗时。
- 运行时事件日志存在`skill-library.find`和`skill-library.load`成功事件（例如sequence 8033–8034、8035–8036）。事件以哈希方式记录调用参数，不含聊天会话标识及所选ref的可验证明文，不能据此认定两次必定属于同一任务或必定重复加载。
- 用户反馈在新聊天窗口中“看上去调用成功了”，支持一次主动触发的初步观察；目前没有系统化的跨新窗口盲测结果，也尚未独立验证具体Word输出文件的质量。

## 代码与回归检查

- PLA主要变更：移除旧工具暴露、中文Capability搜索过滤、自然任务描述与单次检索最佳匹配策略、文档和回归测试。
- Skill Library主要变更：进程内ManagedSourceLibrary对象复用；`skill_search(include_best_content=True)`通过原`skill_read`权限检查加载最佳匹配；文档和测试。
- 2026-10-09已执行：PLA选择性回归85 passed；Skill Library完整测试76 passed、2 skipped；独立真实MCP进程协议测试通过。若修改后继续维护元数据提示语，需要重新运行有关回归。
- 对重复读取采取提示语约束，而非强制拦截：若已存在`loaded.content`且`loaded.ref`即所选ref，模型应直接使用；选择不同Skill、缺少内容或明确需要刷新时仍可正常调用`load`。该规则的模型遵守率需要新的盲测，不能由单元测试保证。

## 尚未完成的验收

在多个真正无Skill提示的新聊天窗口中，记录自然任务是否主动调用`find`、是否携带`include_best_content=true`、是否正确复用已返回的正文、是否仅在需要时再次调用`load`，并按情况检查最终文档输出。模型层自主决策行为不具确定性，不应在服务端把任意写作任务强制绑定个人Skill。
