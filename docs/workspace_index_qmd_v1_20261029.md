# PLA 工作区本地索引首版（2026-10-29）

## 已实现
正式动态能力：core.workspace_index_init、core.workspace_index_refresh、core.workspace_index_status、core.workspace_index_search。源码实现位于 src/tooling/workspace_index.py。选择PLA已授权Root和其中的目录，索引位于该目录 .qmd/index.yml 和 .qmd/index.sqlite。init/refresh为需要INVOKE确认的写操作，search/status为读操作。

底层直接复用QMD，不独立重写FTS数据库或模型。首版只索引Markdown(.md)，没有自动embedding、向量模型下载、AI知识图谱或其他文档提取，不能对PDF/PPT/DOCX宣称已支持。项目级索引之间互相隔离。

## 安全边界
- PLA RootPolicy控制目录权限，禁止在受保护的pla源码Root下建立索引；对搜索返回的相对文件路径重新做Root验证。
- init拒绝覆盖现有.qmd目录，允许用户删除测试索引后再重新建立。若QMD引擎缺失，创建配置之前失败。
- 预扫描最多4000条文件系统记录、1200份Markdown、单文件512KiB、合计24MiB，拒绝符号链接和reparse point。
- QMD配置须为PLA所创建的固定Markdown集合及忽略规则；允许QMD自动写入的三项官方默认模型标识，禁止注入更新Hook、额外目录与自定义模型来源。
- 执行仅允许QMD的update/status/search固定命令，shell=False，受限环境，90秒超时；最多20条检索结果、每条搜索文本120字符以内。
- 索引结果有限长度，实际文件缺失或越权时不会作为有效搜索结果返回。不会自动监控和处理授权Root外的目录。

## 可选依赖
新增 provider_manifests/qmd.json 和 provider_specs/qmd.npm.txt，固定 @tobilu/qmd@2.8.3。Desktop构建会将其复制到组件清单，仍需用户在组件管理中安装该可选依赖（私有目录 .provider_envs/qmd），且默认不启用原生MCP。新Core接口直接调用引擎。为旧试验环境保留 .provider_envs/qmd-pilot 兼容发现，正式新安装使用 qmd。此npm依赖不是OS级沙箱，发布前仍需审核许可证及供应链。

## 已执行验收
源码端真实QMD E2E在桌面独立临时目录完成：首次索引2份中文Markdown，搜索“复杂地形”返回cfd.md，状态查询成功，二次刷新显示2份均未变化；试验目录已经清理。
现有Manifest加载器确认qmd配置有效且Node命令存在。专项单元测试涉及Root越界、配置注入、符号链接、体积限制、搜索结果穿越、输入边界和Broker的INVOKE确认。
最终联合回归：266 passed、3 skipped（其中一项为本机不能创建测试符号链接）。

## 尚未完成
新版Desktop尚未重建安装，新Core能力也尚未通过冻结安装版的PLA_TEST真实MCP E2E。不能将本轮源码验收等同已发布。建议下一步构建安装后验证新增动态Capability与qmd可选组件安装，并继续在独立临时工作区演示init→search→refresh→status，之后再讨论导入真实科研目录。
