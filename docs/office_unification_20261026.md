# Office统一组件：实现与验收记录（2026-10-26）

## 目标与当前状态

新增可选`office` Provider，把既有WPS（Node.js）和Office补强（Python）作为独立MCP子进程接入一个统一入口。两个底层组件的工具实现不复制到新组件里。原始`wps-office`和`office-enhancement`的源码、Manifest和启停状态均予以保留。

`providers/office_server.py`使用FastMCP 4.0.3的`ProxyProvider`复用上下游MCP协议及输入Schema；子进程启动失败时隔离该分支，使另一个分支继续发现。保留原Manifest中的权限覆盖规则，特别是删除与原始方法等高风险工具的确认要求。统一入口额外暴露`office_status`和`office_preview_image`。预览工具使用PLA原有root访问策略，只读取PNG/JPEG，传输上限为5 MB。

已知条件：新入口需要本地安装并可运行WPS与Office补强两个后端。当前安装器不会自动安装这两个依赖组件，因此**用户侧真正的一键安装、依赖协调及隐藏旧入口尚未完成**；应在产品收敛阶段实施。源码Manifest设置`autostart=false`，避免过渡期默认出现重复能力。

## 本轮实际修复

1. 原来的自定义`office`整合Provider在已安装环境报错：工具允许名单中缺少`artifact_build_overlay`和`wps_macro`。新设计不依赖这种严格的手工名单；如果单侧后端不可用，仍会暴露可用部分及诊断工具。
2. WPS上游TypeScript对`wps_ppt_set_slide_size`和`wps_ppt_set_font_color`注册了方法，但其Windows COM PowerShell脚本中没有`setSlideSize`与`setFontColor`的Action分支，调用因此返回`Unknown action`。新增`provider_patches/wps-office/windows-ppt-actions.patch`，通过`provider_specs/wps-office.source.json`按固定顺序在安装阶段应用。
3. WPS添加文本框时PowerShell返回`shapeName`，但调用端读取`name`与`slideIndex`。兼容补丁将这些字段完整返回，并保留`shapeName`兼容旧行为。
4. 统一入口会读取本地WPS Windows桥接脚本，若缺少上述两个Action或文本框返回字段修复，则暂不暴露对应3项工具，并通过状态查询报告，防止把已知故障能力误报为可用。
5. Desktop Office补强对`@oai/artifact-tool`采用专属依赖路径检测。依赖不存在时，统一入口不会继续宣称`artifact_build_overlay`可用，其他补强功能不受影响。其npm依赖的合法获取和用户安装部署仍需进一步核实，不能因为开发机Codex缓存可用就假设发布包可用。

## 已完成验证

- 统一入口的完整工具集合为**261项**（WPS 250 + Office补强9 + 状态1 + 图片预览1）。当前开发机的WPS Windows脚本尚未安装新补丁，因此主动隔离3项已知故障工具，**实际可发现258项，office_status=degraded**。待补丁应用后可恢复工具发现，仍须通过实际WPS COM操作验证。
- 统一入口真实调用`wps_common_ping`成功。
- 统一入口真实调用`office_enhancement_status`成功。
- 通过统一入口调用`powerpoint_render_slide`，输出`workspace/office-suite-qa/unified_office_preview_20261026.png`，再调用`office_preview_image`成功收到MCP `ImageContent`。
- 新WPS Windows Action补丁使用`git apply --check`检验可应用到当前已经过旧兼容补丁处理的源文件。
- 新增故障隔离、权限覆盖、预览图片安全限制和组件Manifest测试。
- **注意：以上为源码/本机开发运行验证，不代表已安装的PLA-TEST用户配置被替换或WPS修复已在实际用户Provider中生效。** 新Action仍需在独立测试文稿上运行WPS COM真实E2E；ChatGPT客户端是否显示ImageContent还须验证。

## 后续部署门槛

1. 清理并重新安装或用户确认修复已安装的错误`office` Manifest（当前配置为用户所有，打包流程不会覆盖用户修改）；不得绕过权限及显式确认。
2. 对WPS按固定版本重新安装/修复，以应用新增Windows COM补丁，使用临时测试文稿真实测试幻灯片尺寸、字体颜色和文本框返回字段。
3. 在干净Desktop实例上安装`office`及其两个后端，并测试：一端缺失、启动失败、正常调用、渲染与图片返回。
4. 核实`artifact-tool`的可再发行性、安装方式及许可证；缺失时保持能力不可用状态。
5. 真实ChatGPT/PLA-TEST检查图片内容是否被客户端直接显示，并完善预览后的视觉修正策略。
6. 通过以上验收后，再设计自动依赖安装与单入口GUI呈现，逐步停用冗余入口。

## 本轮文件

- `providers/office_server.py`
- `provider_manifests/office.json`
- `provider_specs/office.txt`
- `provider_specs/wps-office.source.json`
- `provider_patches/wps-office/windows-ppt-actions.patch`
- `tests/test_office_unified_provider.py`
- `tests/test_office_preview.py`
- `tests/test_production_provider_catalog.py`

以上是首轮源码阶段的状态，当时没有修改用户安装。本次恢复执行的更新如下。

## 中断恢复、安装迁移与真实验收（2026-10-26）

- 先隔离疑似阻塞的桌面组件测试。其他5个模块**36 passed（2.99s）**，桌面组件管理3个普通测试**3 passed（2.20s）**，热插拔真实运行测试**1 passed（15.41s）**，结果保存至`workspace/office-suite-qa/office_hotplug_probe_20261026.log`。此前大批量同步测试的两次120秒失败，未复现为单个热插拔测试失败。
- 新增`tests/test_office_desktop_stage.py`验证隔离Desktop暂存与用户修改保留；新增`scripts/office_component_migrate.py`与`tests/test_office_migration.py`，实现基于SHA-256预检、三个文件完整备份及失败恢复的显式迁移。相关12项测试通过。
- 通过只读预检与旧Manifest SHA-256核对后，迁移了已安装PLA Desktop中的三个旧`office`文件。备份位于`%LOCALAPPDATA%\\io.pla.desktop\\office-migration-backups\\office-20261026T022451Z-27bc7731`，并复验备份文件存在。没有改变旧WPS或Office补强组件，也没有自动改变用户启用偏好。
- 通过PLA-TEST的`runtime.provider_reload`热重载成功：原来`office`因工具白名单缺失而`error / 0 tools`，现在`ready / 257 tools`，Provider Doctor健康；`office_status`如实报告`degraded`，其中3个WPS动作尚未应用兼容补丁，另有`artifact_build_overlay`因缺少Desktop依赖暂时隔离。
- 真实PLA-TEST调用`office.wps_common_ping`成功、`office.office_enhancement_status`成功、`office.pptx_inspect`成功（桌面1页文稿、5个shape、1张图片），`office.office_preview_image`成功返回一块MCP ImageContent。
- 余项：将新WPS Windows COM补丁应用到Desktop管理的WPS源码，并用独立测试PPT进行真实字体颜色/页面尺寸/文本框E2E；高级可编辑排版仍需查明artifact-tool依赖安装与许可；用户侧一键安装及单入口GUI尚未完成。
- 继续修复：安装版WPS桥接脚本的SHA-256与开发基线一致，新补丁通过`git apply --check`；已先备份旧脚本，再原子替换为新补丁版本，备份位于`%LOCALAPPDATA%\\io.pla.desktop\\office-migration-backups\\wps-bridge-20261026T022945Z-8b002dc9.ps1`。热重载后统一Office由257项升至**260项工具**；`office_status`确认WPS `unavailable_tools=[]`，Office补强仅剩`artifact_build_overlay`因依赖缺失不可用。WPS格式修改尚未在独立测试文稿完成真实E2E，仅证明桥接脚本已部署并恢复发现。
- **重要的持久化边界**：当前已安装PLA Desktop启动时执行`ComponentProject.stage()`，其旧打包资源`D:\\PLA Desktop\\resources\\provider-assets\\providers\\office_server.py`仍是旧版本；重新启动桌面运行时会覆盖用户组件目录中的新`office_server.py`。因此本次修复是当前会话中的受控热修复，需要按源码重建并安装新版Desktop后，才能宣称重启持久生效。用户自定义Manifest经SHA保护通常会保留，但旧代码仍可能覆盖新实现。不得为了绕过此问题随意修改旧安装包二进制资源。
- **本次未提交、推送、发布或重建PLA Desktop安装包。**

