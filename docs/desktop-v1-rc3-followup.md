# PLA Desktop 1.0.0-rc.3 — 多 root 修复验收

2026-10-10。用户确认「添加第二个路径后，第一个被替换」。原因是桌面表单使用原有按名称 upsert 接口，保存后保留旧名称，把再次填写路径静默当作更新同一 root。

## 修复

- 桌面新增使用 `workspace_save(mode=create)`；重名拒绝，不覆盖已有路径。成功后清空名称/路径，恢复默认只读权限。
- 已有 root 提供「编辑」；进入后锁定名称、载入当前路径和权限，保存使用 `mode=update`。取消回到新增模式。
- 界面显示 root 数量与各自的路径/权限。后台仍复用原 registry、原子写入、SHA 并发校验、停止 Runtime 要求及私有目录边界。
- 原管理调用缺省 mode 的 upsert 兼容行为保留；既有 Runtime/MCP 的多 root 协议没有改变。

## 实测结果

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 当前源码全量 | PASS | 826 passed in 151.86s |
| 新冻结多 root | PASS | 4 项：第二 root 保留第一路径；重名新增拒绝且配置字节不变；实际 MCP 对两个 root 独立文件读写；显式编辑仅改变选中 root 的权限 |
| 完整构建的冻结 MCP/浏览器 | PASS | packaged_mcp.py 共 10 项通过 |
| RC.3 冻结异常 | PASS | 基础 7 项通过；独立真实认证网络中断/恢复 suite 4 项也已通过，详见后续记录 |
| 更新用户实际安装 | PASS | NSIS exit 0，安装路径 D:\PLA Desktop；连接、DPAPI 密文、工作区三份配置 hash 更新前后相同 |
| 已安装 RC.3 Computer Use | PASS | 独立数据目录内连续新增 root_a/root_b；界面和持久配置均保留两个目录；新增后表单清空；重名新增显示中文错误且配置 hash 不变；显式编辑锁定名称、载入原路径，仅改变 root_a，root_b 不变；旧测试文件保留，共 5 项 |
| 用户配置不被 UI 测试污染 | PASS | 独立测试结束后，三份正式配置仍与更新前 hash 一致；测试 root 未添加到正式配置 |
| 正常连接恢复 | PASS | 重新以正式数据目录启动用户安装，Runtime ready，健康端口成功控制面轮询，GUI 远端连接 ready；原开发三个 PID 保持 |
| RC.3 真实 ChatGPT 更新后复测 | PASS | 真实 ChatGPT 经 PLA-TEST 检查新测试目录、创建 proof.txt、回读及独立 Runner 执行 Python --version；退出码 0，Python 3.11.9；本地内容与 SHA 独立核对一致 |
| 干净 Windows / 系统重启 | BLOCKED | 无可用 Sandbox/VM/独立测试机或安全重启条件；用户机器仍为开发机 |

## 产物

- `dist/desktop-v1/PLA Desktop_1.0.0-rc.3_x64-setup.exe`
- 89,752,445 bytes
- SHA-256：`8736fec36dd957237dd3fc3b9b540ec7ff8f6785d3c1000f2d9c061f480a41a6`
- 源码：`c3e14f3ad8d5572c50c52c95e8864859eeabba7e`，manifest `source_dirty=false`
- 未签名测试候选，自动更新禁用。RC.2 原始包及 clean 重构建分别归档，不混用不同二进制的证据。

已安装多 root 界面截图和报告位于 `evidence/rc3/`。新增多目录应分别使用唯一 root 名称，例如 project_a 与 project_b；要改变某个已授权目录，点击该行「编辑」。此前被旧版本替换掉的 root 路径不会自动推断恢复，可以重新添加。

## RC.3 更新后真实 ChatGPT 证据

测试文件为 `workspace/desktop-chatgpt-rc3-20261010/proof.txt`，内容 `PLA_RC3_E2E_OK`，SHA-256 `8306da9dd7fcc205a05a69703249c5cfa958e18074a621c25746b3a3f27c6539`，本地文件独立计算与 ChatGPT 返回相同。Candidate Runner PID 32780，镜像为 `D:\PLA Desktop\resources\runtime\pla-runtime.exe`。当前用户正式配置已保留，Runtime 和 Tunnel 正常运行。

此轮 ChatGPT 复测覆盖新文件创建/回读和独立进程；RC.2 的修改与事务链路完整 ChatGPT 验收属于先前版本证据，RC.3 事务/浏览器能力另由当前冻结自动 MCP suite 验证。

## 2026-10-10 真实认证网络中断与恢复

使用用户明确提供的独立测试凭据，通过已安装冻结 manager 启动隔离 Runtime/Tunnel。测试进程专用 HTTPS CONNECT 代理只允许 api.openai.com:443，TLS端到端保持，不修改系统代理、防火墙或证书。真实认证成功轮询后，主动断开代理套接字并拒绝新连接，已安装 manager 观测 disconnected，而 Runtime 仍 ready。解除拒绝后同一 Runtime/Tunnel PID 自动恢复远端成功轮询，时间戳由1791574010增长到1791574047。四项通过，包括真实 key 的DPAPI保护和断线日志无明文。

测试 manager EOF正常退出，独立监听端口消失；测试生成的加密凭据副本已移除，用户提供的test.txt及正式凭据没有修改。正式三份配置hash保持，原开发服务PID保持。测试报告在 `evidence/rc3/authenticated-network-report.json`。
