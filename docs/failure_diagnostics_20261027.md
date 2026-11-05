# PLA失败事件分类（2026-10-27）

## 范围

此次仅增强现有只追加事件数据库（`state/events.sqlite3`）和已有只读活动投影；不迁移SQLite表结构，不开放任意日志查询或读取额外文件，不改变工具执行、权限边界与返回值。

涉及原生MCP工具的`native_tool.failed`、内部后台Action的`local_action.failed`、组件执行的`capability.failed`及Gate Hook拒绝的`capability.gate_denied`。为捕获确认令牌/事务上下文不足造成的预执行策略拒绝，新增`capability.policy_denied`，只记录注册表中已存在的能力名称。非法参数等预执行校验仍维持原有的“不创建事件”行为。

## 字段

- `failure_class`：固定枚举（`policy_denied`、`validation_error`、`missing_resource`、`decode_error`、`process_exit`、`timeout`、`cancelled`、`dependency_error`、`provider_error`、`execution_error`、`unknown_failure`）。
- `policy_reason`：仅在`policy_denied`时出现，固定枚举：`program_not_allowed`、`root_access_denied`、`protected_path`、`outside_root`、`confirmation_required`、`transaction_required`、`powershell_parameter_denied`、`specialized_capability_required`、`gate_hook_denied`、`component_disabled`、`policy_denied_other`。
- `exit_code`：仅记录带符号32位范围内的整数进程退出码（不包含布尔值、字符串或命令行）。

不新增原始异常信息、stdout/stderr、路径、文件内容、调用参数或任意Provider提供的字符串。分类逻辑只读取异常类型及有限的错误前缀，最终落库只允许预定义的类别和枚举。既有事件中的摘要Hash等字段保持不变，旧事件没有新字段时读出空分类，而不会反推原因。

## 使用边界

`runtime.activity_runtime.recent_activity`投影在失败记录上附带新字段，Desktop的`activity_recent`可复用；桌宠将`capability.policy_denied`识别为已结束的失败。原始事件也可以按类型与时间只读聚合，不需要重新构建数据库。

同一个底层操作可能在不同层级产生多条日志，聚合失败次数时不要简单相加。类别是自动诊断标签，不是权限决策依据；Provider自报错误可能不具备可信根因，失败分类必须和事件源一起分析。

测试使用当前本地源码及隔离Python；**已运行的PLA Desktop二进制尚未更新，需要重新构建和安装才能在真实PLA-TEST进程中看到新增字段**。本次不提交、不推送、不发布。
