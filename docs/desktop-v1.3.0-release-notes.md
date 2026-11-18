# PLA Desktop v1.3.0 — Windows x64

本版扩展了多对话活动归因，并为桌面Runtime增加可选的本地网页调用接口。继续使用Tauri应用内签名更新机制；Windows安装程序本身仍未获得Authenticode发布者签名。PLA不构成操作系统级安全沙箱。

## 新增与改进

1. **持久化对话编号**：从宿主提供的MCP请求元数据`openai/session`识别对话，对原始标识进行SHA-256处理后建立本地编号映射。同一对话在多次工具调用及Runtime重启后保持编号不变；新对话获得递增编号。编号采用`C01`、`C02`格式，至少两位，不设固定最大位数，也不按小时重置。注册表只保存哈希指纹和本地编号，不保存原始会话标识。

2. **桌面端与桌宠的工具归因**：最近本地活动列表和Agent Pet为能够识别来源的工具调用增加对话编号前缀。最近60秒内有PLA工具活动的会话列为“活跃”；随后60秒显示为“刚刚停止”，超过120秒后从摘要中隐藏。缺少可识别元数据的调用仍显示为`?`。该状态表示最近观察到的PLA工具活动，不代表ChatGPT标签页是否关闭，也不代表云端任务是否仍在运行。

3. **本地网页调用接口（Web Invocation API）**：可信的本地网页可以通过HTTP调用PLA现有工具，提交任务、查询状态、取消任务，并读取受限的图片产物。接口与MCP共用现有Runtime进程、HTTP监听端口和TaskStore，不另起一套任务执行服务。网页可以自行设计参数输入、流程顺序和结果展示。

4. **显式授权与短期凭据**：接口默认关闭，只监听`127.0.0.1`。用户需要明确配置允许的localhost Origin、工作区Root和工具；`run_process`需单独启用。每次启动Runtime都会生成新的随机Bearer Token，停止或重启Runtime后旧Token失效。网页请求仍经过PLA现有工作区、程序授权、执行和批处理策略。接口不会增加MCP工具定义，也不能通过网页扩大PLA授权范围。

5. **应用图标与构建保护**：更新桌面应用图标，并在构建前检查必需图标文件和Tauri配置。正式打包前清理Tauri原生bundle资源，避免增量构建沿用旧图标。冻结版验收脚本增加Web API认证、Origin限制、真实任务提交与轮询、PNG产物读取及Token轮换检查。

## 安装与更新

1. 在Release的Assets中下载`PLA.Desktop_1.3.0_x64-setup.exe`和`SHA256SUMS.txt`，核对SHA-256后安装。
2. 已安装v1.1.0及后续版本、且签名更新配置有效的用户，可以在桌面端检查Tauri签名更新；也可以下载完整安装包手动升级。升级前保存文档，并停止不希望中断的任务。
3. Web API默认关闭。需要使用时，先停止Runtime和Tunnel，再进入设置页，启用接口并填写网页的精确Origin、允许访问的工作区Root和工具。保存后启动Runtime，点击“检测本地连接”，再通过“显示本次访问Token”复制本次Runtime的凭据。
4. 本地接口地址通常为`http://127.0.0.1:18766/web/v1`，实际端口以桌面端显示为准。网页请求需在`Authorization`请求头中携带`Bearer <token>`。不要把Token写进HTML/JavaScript源码、URL、Git仓库或截图；Runtime停止或重启后需要重新获取。
5. Web API不是公网服务，不应通过反向代理或Tunnel暴露。只允许自己信任的本地网页Origin。启用`run_process`后，网页发起的本地程序仍以当前Windows用户权限执行，受PLA既有程序授权和工作区策略约束。

## 验证与边界

- 源码回归测试：**1,269 passed、4 skipped，另有5项subtests passed**；共121个测试文件，分四组执行。
- Desktop Runtime模式的release-freeze检查：**3 passed**。
- Web API单元测试覆盖认证、Host与Origin校验、CORS预检、工具和Root白名单、固定批处理、参数传递、任务轮询、图片产物和Token校验。
- 本地签名候选包的冻结Runtime验收通过；正式发布包由干净源码提交构建，构建清单核对版本、SHA-256、Tauri Updater签名和源码提交。
- 本次未执行全新Windows环境安装和真实ChatGPT→Tunnel→PLA端到端验证。Windows安装程序没有Authenticode发布者签名；Tauri更新签名不等于Windows发布者身份签名。
- 对话编号是本机可观测工具活动的标识，不是浏览器窗口编号。没有`openai/session`元数据的调用无法归因；“刚刚停止”也不能证明对话已结束。
- PLA继续使用工作区Root、程序允许清单、组件来源和确认策略约束本地操作，但这些机制不是操作系统沙箱。
