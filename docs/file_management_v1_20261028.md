# PLA 文件管理与ZIP第一轮补强（2026-10-28）

## 范围与入口

本轮不扩大 `run_powershell` cmdlet 白名单，也不增加任意脚本执行权限。新增统一结构化工具 `file_operation`，可通过原生MCP入口、`core.file_operation`能力和既有`execute_actions`顺序批处理调用。

| operation | 必填参数 | 行为 |
| --- | --- | --- |
| mkdir | destination | 创建目录及缺失的父目录；现有目录可复用 |
| copy | source, destination | 复制单个文件或目录 |
| move | source, destination | 移动单个文件或目录 |
| rename | source, destination | 限同一目录重命名 |
| archive_create | sources[], destination | 将多个文件/目录打包成ZIP |
| archive_extract | source, destination | 从ZIP提取到目标目录 |

可选参数 `root`（源/默认root）、`destination_root`（目标root）和 `overwrite`（布尔值，默认false）。目标路径必须预先拥有授权的写入权限。源路径要求读取权限；移动、重命名还要求源路径写入权限。覆盖只适用于已存在的普通文件，绝不覆盖整个目录。

示例：整理两项素材并创建ZIP（这里仅展示操作请求结构，不直接执行）

```json
{
  "actions": [
    {"tool":"file_operation","arguments":{"operation":"mkdir","destination":"assets"}},
    {"tool":"file_operation","arguments":{"operation":"copy","source":"incoming/figure.png","destination":"assets/figure.png"}},
    {"tool":"file_operation","arguments":{"operation":"archive_create","sources":["assets"],"destination":"assets.zip"}}
  ],
  "stop_on_error":true
}
```

`execute_actions`仍遵循最多100条操作、逐步返回、遇错停止（可选择继续）的规则。它是顺序批处理，不承诺全部步骤自动回滚；如需跨步骤的审核和恢复，应沿用现有Transaction能力。

## 安全边界

- 使用既有命名root的读取/写入授权、规范化路径、PLA私有目录防护。拒绝根目录替换、跨越root边界和把目录复制进自身。
- 对目录操作进行条目预检查，拒绝符号链接和Windows重解析点。目录复制通过临时目录暂存、复查后发布。
- 目标存在时默认拒绝；显式 `overwrite=true` 才允许普通文件替换；移动和解压覆盖并非通用事务，失败时可能部分完成。
- ZIP拒绝路径穿越、绝对路径、设备名、大小写重复、符号链接以及不安全的Windows路径名。
- ZIP最多4000条目，总未压缩体积最多512 MiB，单项最多256 MiB，单项压缩比上限500；打包源最多128个。恶意压缩包检查发生在目标写入之前。
- ZIP解压先完整暂存再发布。目标目录原先不存在时以同卷重命名发布；目标目录已存在时逐项合并，途中失败可留下部分已发布文件。
- 源文件可能在预检查与复制/移动之间被其他进程修改；操作不构成OS级别的文件系统快照或不可分割事务。生产级并发原子性仍是独立问题。
- 本轮不提供HTTP GET/HEAD、网络文件下载，也不打开PowerShell自由脚本。

## 测试

- `python -m pytest -q tests/test_file_operations.py`
- `python -m pytest -q tests/test_foundation_tools.py tests/test_multi_root.py tests/test_core_capabilities.py tests/test_capability_broker.py tests/test_durable_runtime.py`
- 另需检查 `tests/test_runtime_parity.py` 与 `tests/test_server_run_process_backend.py` 的注册和导入兼容性。

预发布前必须重新构建/重启PLA进程，当前已经连接的MCP客户端无法自动获得新顶层工具元数据。Core Capability注册同样需要服务器重新载入新代码才能发现。

## 后续建议

保留HTTP获取为独立阶段，先设计公网HTTPS、定向重定向/DNS检查、文件落盘限额和独立授权，禁止任意内网访问；不要简单开放 `Invoke-WebRequest`。
