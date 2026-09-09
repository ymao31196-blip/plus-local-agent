# MCP Client 测试

## 测试目的

验证 Python MCP Client 通过 stdio 连接 FastMCP Server，并完成本地文件写入和读取。

## 使用方式

先激活 `plus-local-agent` Conda 环境，再从项目根目录运行：

```powershell
python tests/test_client.py
```

## 当前验证结果

已在 FastMCP 4.0.3、MCP 2.2.0、Python 3.11.16 下通过。客户端可发现低层工具、批量执行工具、后台任务工具和保留的实验性 Sampling 工具；`write_text` 和 `read_text` 调用及内容验证均成功。
