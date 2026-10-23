# PLA Desktop 1.0.0-rc.2 — 用户安装后的复测

2026-10-09–10。用户提供独立测试 Tunnel 信息并授权操作软件。本次通过 Windows Computer Use 实际操作已安装的 Tauri 界面；未使用原开发连接凭据。

## 已定位并修复

1. 连接配置必须在服务停止时保存，但配置页没有直接停止入口，报错是英文。现在配置页提供停止、启动与连接按钮，并翻译常见操作错误。
2. 填写 ID 后返回概览停止服务，刷新会清空未保存的 ID。现在刷新保留已编辑的连接草稿，成功保存后才重新同步配置。
3. 完成配置向导会停止 Runtime 和 Tunnel。现在只保存完成标记，保持服务与本地验证状态。

## 本次真实执行的验收

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 用户提供的独立 Tunnel 凭据 | PASS | GUI 保存成功；DPAPI 密文存在；配置及日志未检出测试 key 明文 |
| 真实远端连接 | PASS | 正确取得远端 test 元数据；控制面成功轮询时间戳更新；界面显示「已连接远端服务」 |
| RC.2 更新现有安装 | PASS | 更新到 `D:\PLA Desktop`，NSIS exit 0；三份配置文件更新前后 SHA-256 完全一致 |
| 未保存的连接 ID | PASS | Computer Use 输入带 draft 后缀，切换概览并刷新，再返回配置页，草稿仍保留；随后恢复并保存真实 ID |
| 完成向导后保持连接 | PASS | Computer Use 启动、真实诊断 PASS、连接、完成向导；Runtime 18766 / Tunnel 18082 PID 均不变，仍 ready / connected / 本地调用通过 |
| 桌面管理后端回归 | PASS | 当前源码 10 tests passed |
| 新打包 Runtime MCP / 浏览器验收 | PASS | 完整 build.ps1 执行的 packaged_mcp.py 共 10 项通过 |
| 用户实际安装后的 MCP 工具 | PASS | 23 项目录；在私有独立测试目录真实读、创建、修改文件；独立 runner 执行 bundled Python 3.11.9；SHA 校验事务修改，共 4 项 |
| 原开发服务保持 | PASS | 8766 / 8931 / 18081 原 PID 30744 / 19464 / 20620 保持不变 |
| 新增原生 UI 自动脚本断言 | NOT TESTED | 已加入 native-ui.cjs；本次同等关键行为用 Computer Use 实际验证，未重跑整个 CDP 自动套件 |
| 真实 ChatGPT MCP 调用 | PASS | 2026-10-10 用户完成连接；通过 ChatGPT 网页 PLA-TEST 实际创建、读取、替换文件，独立 runner 执行 Python --version exit 0，再执行 SHA 校验事务修改及最终读取 |
| 经 Tunnel 的正向工具调用 | PASS | 真实 ChatGPT → 用户独立 Secure MCP Tunnel → 已安装 Runtime → 文件、独立 runner、事务修改 → ChatGPT 返回；本地最终文件内容及哈希独立核对一致 |
| 干净 Windows / 系统重启 | BLOCKED | 本机仍是开发机，未提供独立环境或安全重启条件 |

## 产物

- 安装包：`dist/desktop-v1/PLA Desktop_1.0.0-rc.2_x64-setup.exe`
- 大小：89,749,305 bytes
- SHA-256：`5ed26e86cf57e3d0a8905abaed24a1a4fc80582a3eacbbf6385f411141c4fb2f`
- 源码：`3a6e9b7855683b4c299297ae80d1f40d3f1b878c`，分支 `codex/pla-desktop-v1`
- 最新构建 manifest 的 `source_dirty=false`。构建逻辑已改为比较实际 Git 内容及非忽略的新文件，避免 Tauri 写回换行格式造成仅 stat 状态的误判。最新构建复用已冻结 RC.2 Runtime；冻结后 Runtime 源码未改动，打包 MCP / 浏览器 10 项重新通过。
- 用户当前安装及真实 ChatGPT E2E 对应首次 RC.2 产物：89,755,283 bytes，SHA-256 `9db98b5a3ab4559e80dc5fceaf0313aa56dd4dbb19c969f4f376f8fa3f7db148`，源码 `685f38ef2071369af770c73548f959d1b4a56b17`。首次安装包/manifest/hash 原样保存在 `archive/rc2-initial/`。最新重构建仅修改构建记录逻辑，应用源码无功能变化；未将此前实际安装证据误记为最新二进制的安装重测。
- Authenticode：未签名测试候选；自动更新禁用。

证据位于 `.desktop-build/user-evidence/` 及交付目录 `evidence/rc2/`。RC.1 的 825 源码回归、完整安装卸载和生命周期测试属于此前基线，详见原验收报告，不作为本次重新执行的结果。

## 2026-10-10 真实 ChatGPT 验收证据

用户自行完成 PLA-TEST 连接并先实际诊断和列目录。随后在同一测试聊天发送限定新建私有测试目录的验收请求；未操作已有 work 文件或密钥。ChatGPT 返回创建/读取、FIRST → SECOND 替换、named_pipe_candidate 的 Python 3.11.9、带 expected_sha256 的 apply_changeset 和最终读取全部通过。

最终文件：`C:\Users\26286\AppData\Local\io.pla.desktop\workspace\desktop-chatgpt-e2e-20261010-001\result.txt`。内容：`PLA_CHATGPT_E2E_VERIFIED`。SHA-256：`55c2da4072cda0c73e1e29b8732992ebd9219f9bfdd806aa2df127ba0d83a8ed`；以只读文件工具独立计算，与 ChatGPT 返回完全一致。独立 runner 状态记录的镜像是 `D:\PLA Desktop\resources\runtime\pla-runtime.exe`。

聊天：https://chatgpt.com/g/g-p-6911c6442f2481918141137a81e3c2da-dui-chatgptde-tan-suo/c/6ac911d1-aaf4-83ea-b152-e94f4e32501c 。截图、限定本次结果的 DOM 文本和报告已交付到 `evidence/rc2/chatgpt-e2e-*`。没有将此通过等同于干净 Windows、系统重启、Office/Skills 或完整正式发行验收。

## 2026-10-10 后续构建及回归收尾

最新 clean-source 重构建退出 0，manifest SHA-256 与实际安装包一致。当前源码全量测试 `825 passed in 163.17s`。用户已安装 RC.2 的七项 headless 异常测试通过；专用网络中断仍为 NOT TESTED。完整 CDP 套件及最新重构建二进制的安装生命周期未在本轮重跑，用户实装原 RC.2 的 Computer Use 及 ChatGPT E2E 证据保留。用户新反馈工作区单路径体验与多 root 能力不符，该问题进入下一项复现与修复，不宣称整体发行已完成。
