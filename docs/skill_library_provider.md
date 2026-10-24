# Skill Library Provider 安装与来源配置

Skill Library是PLA的可选Provider，代码位于独立的`chatgpt-skill-library`项目中。PLA不会把开发者个人的GitHub仓库当作用户的默认Skill来源。

## Desktop一键安装（新版构建）

新版PLA Desktop的安装包在构建时包含审核过的`chatgpt_skill_library-0.5.0-py3-none-any.whl`。在**组件与MCP插件**选择`skill-library`，直接点击**安装 / 修复**并审阅SHA-256计划即可；安装后仍须单独**启用并连接**，再登记自己的Skill Source。普通用户无需下载Skill Library服务端源码，也不会获得开发者的私人Skill或访问权限。

安装包只包含v0.5.0服务端模块。其SHA-256固定为`869bd376af7c729172283017b36c694ad0fc128c5d54ffa7a897ce4b4d3f1eb0`，校验失败会拒绝安装。升级时先建立新的独立Python环境并完成前后校验，失败会尝试恢复旧环境；Source配置、缓存、访问授权保留在环境目录之外。高级安装依然支持指定已审阅的本地源码或Wheel。

构建安装包的开发者需要一份干净的独立仓库检出（已验证的commit `13cc22435152d475e72e4cfe1aa0dc4e0657d16f`）。默认位置为`workspace/skill-library`，也可在`desktop/packaging/build.ps1`指定`-SkillLibrarySource`；构建脚本生成并校验Wheel后嵌入Desktop资源。该开发期输入**不应出现在终端用户安装流程中**。Skill Library原创部分已在独立仓库标注Apache-2.0，仍须核查所引入第三方内容的许可证。本说明描述当前待发布源码；已安装旧版Desktop不会自动获得新能力。

RC16候选版的Desktop把「Skill列表」与「Skills管理」合并为唯一「Skill Library」主导航入口。列表直接提供查看正文、单项启停、筛选、同步来源以及「仅刷新缓存」；「管理来源与权限」二级页面提供本地/GitHub来源登记、自动生成来源ID、重复来源识别、来源同步、整体启停、移除登记与独立读写授权。移除来源会删除其服务端登记、缓存与单项启停记录，但不会删除原始文件或远端仓库；保存权限后需重新连接Provider生效。来源草稿在当前WebView本地存储中保留，上次未填写的字段保持空白并显示提示占位。

GUI不再展示17项MCP接口的参数表单。ChatGPT仍可调用原有的`prepare`、`apply-local`、`plan-change`、`apply-change`、`restore-local`及发布/转移能力，按原有预览、哈希检查和确认机制处理创建、编辑、删除和发布。用户用自然语言向智能体提出这些任务即可，不必直接填写内部计划ID或SHA-256。同步失败时提示保留上次有效缓存；当前GUI只呈现本次会话的同步结果，不声称记录跨重启的历史同步时间。

## PLA源码版安装（进阶）

原有PLA源码部署与Desktop安装方式不同。`setup_providers.ps1 -Provider skill-library`负责安装Provider的Python依赖；独立Skill Library Python包可以按以下方式安装：

1. 将已取得访问权限的Skill Library源码放到`workspace/skill-library`，保留`src/skill_library/server.py`。PLA入口会在该位置加载源码。
2. 运行`setup_skill_library.ps1`，向`.provider_envs/skill-library/Scripts/python.exe`安装Skill Library 0.5.0包。PLA入口会在没有本地源码目录时使用已安装的包。

不要把仅有`SKILL.md`的个人Skills仓库当成Skill Library服务端源码。它们属于用户随后登记的**Source**。

已取得Skill Library代码访问权限的用户，可从独立仓库克隆对应`v0.5.0`标签到`workspace/skill-library`，然后执行`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\setup_skill_library.ps1`。也可以提供已获得的本地Wheel：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\setup_skill_library.ps1 -SourcePath C:\\path\\chatgpt_skill_library-0.5.0-py3-none-any.whl`。只检查本地来源可使用`-ValidateOnly`。PLA主安装器支持`-SkillLibraryPackage <本地源码目录或Wheel路径>`，省去独立执行安装脚本的步骤。两种方式都不会自动开放任何本地Skill读写权限。独立仓库如处于私有状态，用户需要自行获得合法代码访问权限；不能假设所有GitHub访问者都能下载。

## 配置

公共`config/skill-library.json`只定义数据目录和默认空白的本地文件访问范围（读取、写入分别授权）：

```json
{
  "data_dir": "state/skill-library",
  "local_roots": [],
  "writable_roots": []
}
```

如需允许Skill Library在PLA本机读取特定文件夹，创建不入库的`config/skill-library.local.json`：

```json
{
  "local_roots": ["workspace/skill-library"],
  "writable_roots": [],
  "repo": "your-account/your-legacy-skill-repository",
  "branch": "main",
  "transport": "git"
}
```

以上`repo`字段仅为独立Skill Library服务端的旧版接口保留；PLA不再对ChatGPT暴露旧版单来源工具。**多来源工作流无需设置该字段**。即使只连接GitHub Source，也无需配置`local_roots`。

`local_roots`必须是PLA目录以内的相对路径，默认拒绝访问任意本地目录。不要将整个磁盘、用户主目录或PLA根目录加入允许范围。需要更广泛的目录权限时应先明确审查。

## ChatGPT调用示例

通过PLA连接后，使用Skill Library的以下Capability：

```text
skill-library.manage  → action=add, source_id=my-github, kind=git, location=owner/skills-repo
skill-library.sync    → source_id=my-github
skill-library.find    → query=Markdown authoring
skill-library.load    → name=my-github:github-markdown-authoring
skill-library.sources → 查看所有来源和缓存状态
```

Source Registry配置和缓存保存在服务端`state/skill-library`，按来源隔离；不允许Skill自行执行脚本或扩展PLA的权限。

如需使用v0.4单Skill启用/停用，可通过`skill-library.states`查看状态、`skill-library.toggle`设置开关。若需要删除或重命名，可先调用`skill-library.plan-change`预览完整文件清单，经确认后使用`skill-library.apply-change`执行，删除的Skill可用`skill-library.restore-local`恢复。`skill-library.publish-plan`为拥有个人GitHub账户的用户提供首次发布方案，真正创建GitHub仓库与推送依旧经由需要独立授权的GitHub CLI和PLA受控Git能力执行。

v0.5首次GitHub上传：先通过`skill-library.publish-plan`确定来源和仓库，再创建并登记新的本地Git克隆目录；授权该克隆的独立`writable_roots`后，用`skill-library.publication-prepare`审阅全部文件与SHA-256，再经确认调用`skill-library.publication-apply`执行复制。之后继续采用PLA受控Git暂存、提交、推送，并核对GitHub新来源的同步读取。文件复制不会自动推送，默认也不会包含单独停用的Skill。

如果用户需要直接维护自己的SKILL.md，可另行在本机私有配置中将**受控的Skill仓库目录**加入`writable_roots`（必须位于`local_roots`允许范围内），重载Provider后使用`skill-library.prepare`预览草稿、`skill-library.apply-local`在明确确认后写入。写入仅限已登记本地Source的`skills/<name>/SKILL.md`，并以文件SHA避免覆盖并发修改；Git提交/推送仍由PLA受控Git能力处理。参见Skill Library的`docs/authoring_v03.md`。

## Skill主动发现与执行路径

对于论文润色、学术报告、复杂文档编辑或其他可能有对应Skill的任务，ChatGPT应优先考虑调用`skill-library.find(query=..., include_best_content=true)`。一次调用返回已启用来源的匹配元数据，以及排名第一Skill的`loaded`正文与完整来源标识。ChatGPT需要自行检查候选相关性：当`loaded.content`已经存在，并且`loaded.ref`等于所选Skill的`ref`时，直接应用已返回的内容，**不要再次调用`skill-library.load`读取相同的ref**。仅在`loaded`不存在、选择了另一个ref或明确需要重新读取时才调用`load`。旧调用省略`include_best_content`时保持原返回结构。简单问题无需机械搜索。Skill属于建议性工作流程，不会获得额外权限。

PLA只暴露新版多来源的17个工具；旧单来源工具仍保留在Skill Library独立服务端以兼容直接访问服务端的旧客户端，但不经PLA路由。旧缓存不会被删除。ChatGPT客户端是否主动触发仍取决于模型决策，应在新会话中验证。

## Skill发现性能与独立验收

Skill Library采用受控的独立Python stdio Provider。基准测试表明：Provider内部检索与读取本身已很快，主要可操作的优化是减少ChatGPT经过Tunnel的MCP调用次数。新增的`include_best_content=true`将原来的`find → load`两次调用合并为一次，并且继续检查Source/Skill启停状态、来源标识和缓存完整性。该选项不会自动执行Skill脚本，也不会改变权限；当无匹配时只返回空候选。测试过持久stdio会话，但隔离对照显示收益主要在毫秒量级，因此本轮不改变Provider常驻进程策略。

无需请求Skill的全新ChatGPT对话，是验证自动触发行为的必要环境。建议分别输入“帮我润色这篇论文”“改写论文摘要”“整理Word里的图片作为论文配图”“按GitHub格式修复公式”，观察ChatGPT是否在编辑前主动执行`skill-library.find`，以及后续是否通过带来源的`skill-library.load`成功读取；“介绍什么是PCA”“创建一个空文件”等不应机械触发。Capability Registry正反例单元测试只能验证工具可发现性，**不能证明模型在新聊天中必然自动调用**。统计时应同时记录从用户消息到首次`find`的决策时间、`find`耗时、`load`耗时和整体任务用时，不能只看Provider内部耗时。

## 升级与验证

安装或更换Skill Library服务端后，通过PLA的`runtime.provider_reload`重新发现17项工具，然后运行`runtime.provider_status`或`provider_doctor`确认健康状态；更改独立Provider时无需重启整套PLA。若报缺少`skill_library`模块，先检查独立包是否实际安装在Provider运行环境中。

重要限制：配置与身份隔离是**按服务部署实例**实现的。不能将同一个无多租户隔离的Provider实例公开给彼此不信任的不同用户。
