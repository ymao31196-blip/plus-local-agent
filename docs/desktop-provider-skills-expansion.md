# 桌面 Provider / Skills / 可选自开发扩展 — 2026-10-10

用户要求桌面界面覆盖源码功能，安装新的 MCP 插件后能够显示，并提供默认关闭的可选自开发模式。此文档是实现与验收清单，不是完成报告。当前用户安装仍为 RC.3，尚无这些新增页面。

## 已确认的真实基线

- 原 GUI 只有 browser_enabled 开关，需要停止服务后保存再启动；不等于通用 Provider 热插拔。
- 原 Runtime 已有 `runtime.provider_status/setup/rescan/reload/enable/disable`，三个 transport kind：isolated_python_stdio / executable_stdio / streamable_http，统一工具 allowlist 与 capability broker 风险/确认语义。
- 已新增只读 `runtime.provider_catalog`：读取全部 validated manifest，包含未配置项和新装项；配置存在、命令/Python存在、实际 lifecycle 分开呈现，读取目录不会启动组件。当前源码相关 12 项测试通过，未重新打包安装。
- Desktop Runtime 当前只给浏览器生成 manifest。其他源码 manifests/specs/wrappers 未完整进入发行，因此必须先补发行及可写组件层，不能只做按钮。
- 包含在安装包的 Python execution component 实测没有 venv、ensurepip 或 pip；不能假称现有 Python 足以安装所有 Provider，不能借用开发者的环境。
- Skill Library 是独立服务包 v0.5.0，已有本地来源或 wheel 的安装入口。PLA manifest 明确暴露17项新版工具；独立服务旧版工具不是桌面需要重复暴露的旧接口。可访问源码/包和再分发许可须核查，不能自动采用开发者私有 Skills 配置。

## 功能与验证对应

| 页面/功能 | 原接口 | 需要的实际验收 |
| --- | --- | --- |
| Provider目录、状态、依赖、工具详情 | provider_catalog / provider_status / provider_doctor / capability_describe | 新manifest可发现；未装/停用/故障不显示ready；真实tools/list与schema显示 |
| 安装与更新 | provider_setup + reviewed provider_specs | 固定规格/版本与安装日志；Python/npm/source组件装入独立用户组件区；失败可诊断 |
| 新MCP接入 | 原manifest三种transport与校验器 | 导入/预览/验证/独立授权；不暴露通用Shell；重扫后出现在目录；新工具经原broker调用 |
| 真正热插拔 | provider_rescan / reload / enable / disable | HTTP Runtime PID不变；真实工具增加/停用/恢复；状态与持久偏好分开 |
| Skill Source管理 | manage / sources / sync | 多来源添加、编辑、启停、移除、同步，真实缓存/失败状态，无默认私人来源 |
| Skill目录与阅读 | find / load / resource / validate | 搜索、完整正文、资源、来源及版本、验证错误，不把Skill文字当权限 |
| 单Skill启停 | states / toggle | 停用后搜索/读取行为确实受限制 |
| Skill创作 | prepare / apply-local | 编辑、完整草稿预览、hash并发检查、受控可写Source、确认后真实文件变化 |
| 改名/删除/恢复 | plan-change / apply-change / restore-local | 每文件hash和变更预览；可恢复删除；真实恢复；保持原确认语义 |
| 发布准备 | publish-plan / publication-prepare / publication-apply | 仓库方案、逐文件预览、受授权Git克隆复制；不自动创建远端或push |
| 可选自开发 | 多root + 原文件、事务、受控执行能力 | 默认关闭；选择明确源码root；启用后ChatGPT可修改该root；正常模式安装资源仍只读；构建候选和切换流程明确 |

## 实现边界

优先采用用户数据内的独立组件/manifest层与固定管理命令映射，复用原Runtime/broker；新安装的Provider重扫时列出。安装资源保持只读，组件依赖与用户配置分离。Skills本地读写权限从明确授权root映射，默认不开放全盘。新的本地执行插件须有真实MCP发现、工具权限和受控安装/信任预览，不通过通用Shell文本框启动任意程序。

自开发先按独立明确授权的源码工作区实现，默认关闭。用户已被询问是否还希望更新当前安装程序；未把偏好问题当作批准公开发布。切换运行候选必须保留配置、可恢复并有明确版本/校验信息。

## 当前剩余

组件层、固定管理接口、三种清单导入、完整参数目录、全部Provider管理页面、17项Skills工作流、独立依赖安装、Skill root权限、自开发开关和源码快照准备已写入源码。当前开始 RC.4 构建；用户安装仍为 RC.3，以上新增功能尚未通过冻结包和真实窗口验收。

源码环境完整回归836项通过（快照新测试加入前）；快照/新增管理专项10项通过。实际独立 uv 管理 Python + Skill Library v0.5.0安装和17项 MCP接口验收7组通过，报告 `.desktop-build/components-mcp-report.json`：来源同步、读全文/资源、验证、停用后的读取阻止、草稿应用、改名、归档恢复、发布方案与本地仓库逐字节转移、Runtime PID不变。没有push或公开发布。此证据不是冻结包或ChatGPT验收。

Skill Library未找到明确的再分发许可证，因此没有自动复制独立仓库进入安装包；当前提供用户自己的v0.5.0源码/wheel受控安装入口。Office增强独立环境已改为复用原RootPolicy的最小公开模块，不借用Codex的artifact-tool；该可选依赖缺失会真实报告。Git和Rust仍为可观察的自开发/来源组件系统前提。

仍需：RC.4构建、冻结安装/热插拔/17项Skills验收、实际GUI验收、默认用户配置保留与更新验证、ChatGPT新增链路复测、第三方组件许可证最终整理。原任务干净Windows与系统重启条件也仍未提供。
