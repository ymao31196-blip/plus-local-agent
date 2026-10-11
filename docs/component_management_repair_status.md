# 组件管理修复进度

2026-10-11。此记录描述当前源码；没有把源码测试等同于安装包验收。

## 合并入口

已移除单独的「MCP 插件与 Provider」导航，统一为「组件与 MCP 插件」。四个标签为运行管理、仓库管理、浏览组件和已安装组件。原有运行管理表单保持同一 DOM，切换标签保留清单草稿和事件处理器。原 `view('providers')` 路由转到统一页面的运行管理标签，Skill 安装引导继续可用。

从仓库点击 Browse 现在会真正切换到浏览组件标签。已安装列表加载不再因外层操作锁而被跳过。定时安装状态刷新仅在运行管理实际可见时进行。

已执行：

- PASS：`node desktop/verification/component-navigation.cjs`。使用实际 Edge 浏览器 DOM 和模拟管理 RPC，检查统一入口、四个标签、仓库浏览联动、草稿及 DOM 保留、原重扫 RPC、兼容路由和字符串错误提示。
- PASS：`node desktop/verification/components-ux-smoke.cjs`。现有组件控制界面回归。
- PASS：app.js、components.js 和 native-ui.cjs 的 Node 语法检查。

安装版导航验收脚本已更新到统一入口；新版二进制尚未构建和验证。这些测试不证明实际 MCP 连接或仓库组件安装成功。

## 安装链路修复与实际验证

已补充固定 `repository_job` 子进程角色，复用组件安装全局锁。预览保存完整包字节和原安装状态，SHA-256 绑定安装、升级及卸载确认；确认后执行已审阅快照。新组件按 ID 和版本隔离，升级保留旧版本的所有权记录，卸载仅处理已登记文件，失败可回滚。运行中的插件必须先停用。

真实测试发现并修正了 uv 参数错误、测试 manifest 的非法 version 字段、测试组件 SDK 接口版本不匹配，以及 stdio 默认 keep_alive 导致停用后解释器仍被占用的问题。测试组件使用精确锁定的 mcp==2.2.0 和该版本的 MCPServer API。

已执行：

- PASS：14 项仓库任务和路径/摘要/所有权/异常回滚回归。
- PASS：桌面相关选定回归共 46 项；MCP 客户端、Provider 热插拔、外部运行和仓库任务选定回归共 53 项。两组有重叠，不能相加作为独立用例总数。
- PASS：实际全新 Temp 目录中，源代码 Manager → 固定子进程 → 验证 uv → 私有 Python → 仓库组件安装；确认请求约 0.1 秒返回任务。
- PASS：真实 stdio MCP 发现两个 echo 工具并调用；Runtime 热启用、Broker echo 调用、停用、升级到 1.0.1、重连与再次调用。
- PASS：拒绝卸载仍启用的插件；停用后卸载两份已登记版本；Runtime PID 保持不变；实际检查安装环境和事务清理目录均无残留。

实际证据：`C:\Users\26286\AppData\Local\Temp\pla-repository-e2e-yrbcbqm3\repository-evidence.json`。复现脚本为 `desktop/verification/repository_install.py`。证据限定为本地真实 MCP，不包含 Tunnel、ChatGPT 或新安装包验收。

## 仍在进行

Node 仓库组件已实际验证：私有 npm 安装 is-number@7.0.0，使用安装包的 Node、独立模块加载器和实际 Runtime Broker 调用；停用和卸载成功，私有依赖及事务目录无残留。证据为 C:\Users\26286\AppData\Local\Temp\pla-node-repository-e2e-c7i19yl8\node-repository-evidence.json。

首轮全量源码回归为 928 PASS、1 FAIL；失败为源码布局清单未登记新 provider 模块，已修正。原移交用例返回 False 仍被 pytest 算通过的问题已修正；RPC 测试现在调用实际 Manager。修正后相关 46 项通过。RC.10 全量源码回归已通过：929 PASS，无警告，用时 144.36 秒；日志为 .desktop-build/rc10-source-tests.log。

版本已统一更新为 1.0.0-rc.10，协议和用户指南已更新。仍需重新构建和安装验收。现有运行应用不会因修改源码而自动更新。
