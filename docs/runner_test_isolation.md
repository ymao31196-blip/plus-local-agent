# Execution Runner 测试隔离与生产并存

本说明对应源码重构后的Execution Runner测试隔离修复，验证日期为2026-10-08。适用于Windows环境中PLA已启动、生产Execution Runner仍在运行的情况。

## 隔离契约

生产Runner使用固定状态文件`state/execution_runner/runtime.json`及对应认证文件，启动器与状态检查均保持不变。服务侧Windows命名互斥锁由`state_file.resolve()`的规范化绝对路径派生稳定哈希，因此对**同一状态文件**仍严格禁止第二个Runner所有者；独立测试使用pytest临时目录的`runtime.json`、`runner.auth`与随机Named Pipe，可和生产Runner并存，互不覆盖状态与认证材料。服务继续在固定源码根下运行，测试环境可通过启动前传入`AGENT_WORKSPACE`为临时Runner指定合法的workspace。

这个设计允许受控的多个状态域，不解除单一状态域的互斥约束。测试不得修改生产状态文件或强行停止生产Runner。

## 测试选择执行后端

PLA默认one-shot请求路由至生产独立Runner，此行为保持不变。仅验证临时`local_tools.WORKSPACE`、进程内输出、流式观察和本地取消行为的测试，显式选择`backend="in_process"`。这避免测试进程的临时workspace和生产Runner所持有的真实workspace发生混淆。

需要检验独立Runner执行语义的测试，设置临时`STATE_DIR`、`STATE_PATH`、`AUTH_PATH`，必要时设置`AGENT_WORKSPACE`，然后通过`start_execution_runner()`启动测试专用Runner，并在`finally`中调用`stop_execution_runner()`。生产Runner健康、进程身份和所有权的前后对比由`tests/test_execution_runner_runtime.py`覆盖。Backend Seam的提交前fallback测试通过临时无Runner状态域验证真实回退，不依赖生产Runner恰好关闭。

## 验证记录

- Runner／Canary／Candidate专项17 passed，包含同状态域互斥与不同状态域并存测试。
- 完整测试库在生产Runner仍运行时单次连续执行：**803 passed in 125.75s**，0 failed、0 skipped。通过PLA持久任务系统提交`python -m pytest -q --tb=short`，执行结果返回0。
- 测试修复主要集中在Canary/Runner生命周期、验收验证、MCP changeset、Foundation Tools、Durable Runtime、Execution Runtime与Backend Seam测试。
- 没有更改公开MCP工具名称、认证格式、Named Pipe协议、生产Runner状态目录或默认后端选择。

该验收不意味着所有平台和并行pytest worker都得到验证。它证明在当前Windows单机、PLA生产Runner保持运行的条件下，完整pytest测试库能够稳定完成一次回归。
