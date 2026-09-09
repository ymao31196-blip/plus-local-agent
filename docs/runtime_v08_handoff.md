# Agent Runtime v0.8 — 事实交接

## 1. Goal

在 v0.7 ChatGPT-native Agent Loop 正式架构上实现 Durable Task Runtime + Transactional Workspace。Python 使用 `C:\Users\26286\miniconda3\envs\plus-local-agent\python.exe`，版本 3.11.16。没有调用模型 API、增加本地 Reasoner 或使用 Codex CLI worker。

## 2. Inspected scope / baseline

已读取 server.py、local_tools.py、internal_tool_executor.py、task_store.py、pytest.ini、两份架构/E2E 文档，以及 tests/ 下全部现有测试和 README。确认 MCP wrappers 与后台 worker 复用本地工具，execute_actions 使用单独的六项白名单，run_process/run_powershell 原先使用 subprocess.run，apply_patch 使用解析后单文件替换。

修改前完整运行：`python -m pytest -q` → **101 passed in 24.84s**，与给定 baseline 一致。

开始时已存在未提交修改：server.py、local_tools.py、internal_tool_executor.py、两份文档、tests/test_http_transport.py、tests/test_tool_schema.py，以及未跟踪的 tests/test_foundation_tools.py。本轮保留并承接这些内容，没有还原。不能把相对 HEAD 的全部差异都归因于 v0.8。

## 3. 本轮修改的已有文件

- `.gitignore`：忽略 state/。
- `server.py`：0.8 版本、两个 MCP wrappers、cursor 参数、启动时初始化/恢复任务。
- `local_tools.py`：共享写锁、任务所属进程控制、ChangeSet 委托、patch 新旧位置/零行 hunk/换行语义、显式输出长度。
- `internal_tool_executor.py`：阶段事件、ChangeSet 错误映射、批处理取消边界及 100 action 上限。
- `task_store.py`：SQLite 任务、恢复、取消、事件分页及内存边界。
- `docs/architecture.md`、`docs/chatgpt_e2e.md`：0.8 架构、契约、限制、人工验收。
- `tests/test_generic_agent_e2e.py`、`tests/test_mcp_sampling.py`、`tests/test_tool_schema.py`：给 stdio 子服务器显式注入临时数据库路径；保留原断言。

`tests/test_http_transport.py` 和 `tests/test_foundation_tools.py` 的工作区差异来自开始前，本轮没有修改它们。pytest.ini 没有修改。

## 4. 新文件

- runtime_context.py：worker 私有取消上下文、注册进程句柄、事件入口。
- process_controller.py：受控 Popen 生命周期与有界输出。
- changeset_manager.py：事务 preflight/stage/commit/rollback；MCP ChangeRequest 类型。
- tests/conftest.py：隔离真实 state；测试后关闭任务存储。
- tests/test_durable_runtime.py、tests/test_changeset.py：新增可靠性测试。
- docs/fixtures/long_task.py：无副作用的长任务。
- docs/fixtures/changeset_demo/math_a.py、math_b.py、test_demo.py：两个故意错误的计算模块和测试。
- 本交接文档 docs/runtime_v08_handoff.md。

## 5. Durable Task Store

生产默认 state/tasks.sqlite3，使用标准库 sqlite3、WAL、FULL synchronous、参数化 SQL、RLock。持久保存任务 ID、状态、创建/开始/结束时间、request/result/error；增加 cancel_requested。没有新增第三方依赖。

运行实例持有数据库旁的 OS 文件锁，第二实例不能抢占活跃任务。操作员可通过 AGENT_TASK_DB 指定本地数据库；生产配置拒绝放入业务 workspace。TaskStore(db_path=临时路径) 供测试，未指定路径的嵌入式 TaskStore 保留 :memory: 默认，服务器始终显式使用持久路径。

最多 256 个活跃任务、单 request 1,000,000 序列化字符、批次 100 个 action。超过 2,000,000 字符的结果用显式 JSON-tail 元数据表示。历史记录不在内存无限堆积；历史任务行尚无自动磁盘清理策略。

## 6. Restart recovery

服务器初始化获得所有权后，将旧 queued/running 转为 failed，记录 finished_at、TaskInterruptedByRestart 及事件。绝不重放旧请求。完成/失败/取消记录可由重建存储读取。

数据库损坏、错误 schema/JSON、I/O 故障映射为 TaskStoreError，不删除损坏数据库，不让导入失败导致整个 MCP server 无结构化崩溃。worker 终态写入失败后查询不会伪称 worker 仍在执行。已经发生的文件修改、硬崩溃后可能存活的子/孙进程不由恢复逻辑撤销或任意 PID 清理。

## 7. Cancellation

cancel_task 只接受 task_id。queued 直接 cancelled，worker 不会执行；running 持久化 cancel_requested 并停止已注册直接子进程，worker 实际结束后才写 cancelled。重复取消 completed/failed/cancelled 幂等；不存在返回 TaskNotFound。

普通文件操作协作取消，不能强杀 Python 线程；已发生的写入不自动撤销。批处理保留前序动作结果并停止后续动作。ChangeSet 提交/回滚阶段延迟取消，完成报告后外层任务才能成为 cancelled。

## 8. Process lifecycle

任务内 run_process 和 run_powershell 使用受控 Popen；任务外同步调用保留原 subprocess.run 路径。shell=False、allowlist、safe cwd/workdir、环境限制、stdin、1–300 秒 timeout 都经过原验证。

stdout/stderr 通过有界 reader 保存末尾 20,000 字符，同时累计 original_length；stdin 使用私有临时文件，避免子进程不读 stdin 导致 writer 卡住。超时/取消只杀已登记的进程句柄并回收直接子进程。Windows reader 仅轮询自己的 pipe handle，没有模型可调用的 Win32/PID 参数。

不保证终止孙进程。孙进程持有输出管道时，有限等待后停止 reader，返回 capture incomplete 错误，不无限等待或遗留阻塞 reader。search_text 的既有 rg 子进程也登记到取消上下文。

## 9. Incremental Observation

不带 cursor 的 task_result 保留原完整记录。带 cursor 的模式返回状态、事件、next_cursor、has_more、oldest_cursor、events_truncated、dropped_through_cursor、result_available，省略大 request/result；最终结果另用无 cursor 调用读取。

事件 cursor 为数据库全局递增整数，按 task_id 过滤，可能有间隙。每页最多 64 条，每任务保留最新 256 条；被淘汰的范围显式报告。大事件带截断元数据。事件包含 task/action 开始与完成、stdout/stderr、取消请求、事务阶段和终态。

明确采用阶段级 Observation：进程输出在该 action 结束后返回，未实现逐行实时 streaming。

## 10. ChangeSet schema

```json
{"changes":[{"path":"src/a.py","expected_sha256":"<64 hex>","patch":"<single-file unified diff>"}]}
```

每项三个字段必填；1–32 个已存在 UTF-8 文件，总 patch ≤1,000,000 字符，总源内容 ≤16,000,000 bytes。不支持创建/删除路径、rename、binary、跨 workspace、多文件 diff；允许将已有文件内容改为空。重复 resolved path、大小写/相对路径别名及 hardlink 文件身份被拒绝。

## 11. Transaction phases / result

Preflight 验证所有路径、hash、UTF-8、patch header/context/hunk 位置和数量、规模限制；任一失败零正式写入。Stage 同目录候选文件和原字节备份，flush/fsync；全部完成后再次检查全体 hash/path。Commit 共享写锁下逐项复查并 os.replace。失败则倒序 rollback。

返回 files_requested/files_applied、before/after hash；失败带 error、failed_phase、path、files_replaced_before_rollback、rollback_attempted、rolled_back、restored_paths、rollback_errors、unrestored_paths。回滚失败保留 recovery_files 原内容副本；清理失败也显式列出。

files_applied 在失败时表示已替换但未成功恢复的文件数；外部编辑介入时不能据此断言当前内容仍是候选内容。

## 12. expected_sha256

每项必需；preflight 与实际读取字节 hash 比较；stage 后全体复查；每次正式替换前再次核对。与 write_text/replace_text/apply_patch 共享 RLock，避免本 runtime 内并发写交错。回滚也核对目标是否仍为本次安装的 hash，避免覆盖外部新内容。

外部编辑器/进程不遵守该 Python 锁，检查与替换之间仍有系统级竞态。不能声称强隔离或真正 OS 多文件原子事务。

## 13–14. MCP 与 allowlist

新增且仅新增 cancel_task、apply_changeset 两个 MCP 工具。task_result 仅增加可选 cursor。apply_changeset 可作为单个 submit_task 请求；cancel_task 不进入本地 executor。

execute_actions 白名单仍为 list_directory、read_text、write_text、replace_text、search_text、run_process 六项；没有加入 changeset/cancel/PowerShell/patch。额外增加显式 100 action 上限。

## 15. 安全审计

保留 safe_path/workspace/reparse-point 限制、原程序 allowlist、shell=False、环境敏感覆盖限制、PowerShell cmdlet/参数白名单、无 script/pipeline 输入、显式截断。没有新增任意 kill、shell、下载、删除、Git 写入、模型 API 或本地 Reasoner 接口。

没有修改 Engineering Bridge、Tunnel 配置/credential；没有执行 commit/push/reset/clean，没有安装依赖、网络下载、调用模型 API、启动 subagent。fixture 放在 docs 中，自动验证只复制到临时目录；原 workspace/agent_test 无 Git 内容差异。

边界需如实理解：既有 run_process 仍是程序名 allowlist，Python/Git 的通用参数能力并非 OS sandbox，也未新增 Git 参数级策略；本轮未扩展或声称解决这项原有架构限制。

## 16–19. 测试、编译、兼容性

新增 **66** 项，完整 suite 从 **101 → 167**。

最终完整命令（均在 plus-local-agent 环境）：

```text
python -m pytest -q --tb=short
167 passed in 27.51s

python -m py_compile server.py local_tools.py internal_tool_executor.py task_store.py runtime_context.py process_controller.py changeset_manager.py
exit code 0; no compilation errors

git diff --check
exit code 0; only Git LF/CRLF notices
```

按要求另外运行分组回归：

```text
python -m pytest -q --ignore=tests/test_foundation_tools.py --ignore=tests/test_changeset.py --ignore=tests/test_durable_runtime.py
52 passed in 16.57s

python -m pytest tests/test_foundation_tools.py tests/test_durable_runtime.py tests/test_changeset.py -q
115 passed in 13.16s
```

后一组由原 Foundation Tools 49 项 + 新增 66 项组成。项目真实 state/ 目录未被测试创建或写入。

已真实验证：持久化重建、真实 runtime 进程突然退出后的恢复、并发 worker、真实长进程取消且其他进程存活、批处理取消、cursor 分页与淘汰/截断、PowerShell 任务输出、Unicode、两/三文件成功、preflight/stage 失败零写入、commit 失败及 rollback 成败、外部修改不被回滚覆盖、路径 escape、UTF-8、binary/create/delete/multi-diff、规模/别名/hardlink。

Windows 未启用普通 symlink 创建权限；路径测试实际创建目录 junction 验证相同 reparse-point 逃逸边界，最终没有 skipped 测试。不把 junction 测试称为已执行普通 symlink 创建。

MCP fixture 回归真实看到 2 failed → 一次带 hash 的 changeset → 2 passed。原有 101 项全通过，旧 MCP 参数仍可用；保留 experimental Sampling。新增上限是有意的有界执行兼容性收紧。

## 20. 已知限制

- 直接子进程取消，不保证完整 process tree；硬崩溃不重新绑定旧进程。
- 协作取消，不撤销之前的普通写入；事务中途取消延迟到安全报告点。
- 阶段日志，非实时 stdout；淘汰/截断的内容不能从 API 恢复。
- 多文件事务有 rollback，但不是 OS atomic；没有系统崩溃事务 journal/recovery。
- 外部编辑/进程与路径重解析仍有 check/replace 竞态；并非恶意本地进程隔离。
- 历史任务行无自动磁盘保留期限；极大结果显式截断。
- 任务内输出内存有界，任务外同步进程沿用旧 capture 行为。
- SQLite 单运行实例拥有；第二实例任务操作失败，不支持多 runtime 共享调度。

## 21. 未完成项

要求的本地开发/测试/文档已完成。没有替用户运行真实 ChatGPT Plus 人工验收，也没有重启正在使用的服务器/Tunnel；使用现有正常运维方式加载新代码后，可按 docs/chatgpt_e2e.md 验收。这是部署/人工验收边界，不冒充已经完成的端到端客户端运行。

## 22. 下一阶段真正值得做的事情

先执行新增的两项真实 ChatGPT 验收，观察是否确实需要实时 stdout 或更长日志保留。若有真实故障数据，再考虑 task 历史保留策略、ChangeSet crash journal、Windows Job Object 进程树生命周期。不要先扩张工具数量或引入新的 Reasoner/模型 API。
