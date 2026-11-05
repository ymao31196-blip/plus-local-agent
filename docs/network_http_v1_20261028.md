# PLA 公网HTTP与网络诊断：第二轮（2026-10-28）

## 入口与行为

当前仅注册动态Core Capability，**没有**新增原生顶层MCP工具，也未开放任意PowerShell Invoke-WebRequest。重启本地PLA运行时后通过capability_search发现：

| ID | 操作 | 许可 |
|---|---|---|
| core.http_probe | 公网HTTPS 443 HEAD/GET状态与受限元数据，不读取正文 | read，无额外确认 |
| core.http_download | 公网HTTPS 443下载指定文件至已授权目录，默认不覆盖，返回SHA-256 | write_local，必须INVOKE确认 |
| core.network_diagnose | 公共DNS解析、公共IP TCP连通性 | read，无额外确认 |

HTTP获取和下载**不能**加入execute_actions批处理；下载的Broker确认不能被批处理绕过。诊断仅接受公有FQDN，不接受IP字面量或本地/私网主机。允许的公网TCP端口由调用者显式指定。没有实现局域网HTTP探测，也没有HTTP明文。

## 操作限制

1. HTTPS证书始终校验原始请求域名。默认direct模式通过系统DNS选择公网IP；可显式选system_loopback_proxy，在系统授权的本机HTTP代理中建立CONNECT，并通过固定Cloudflare DoH的加密解析取得公网IP。每一跳重新校验地址、固定CONNECT目标公网IP；最多3次跳转，拒绝HTTP降级、URL凭据及非443端口。
2. 下载上限50MiB，超时预算1至30秒，逐块读取校验Content-Length，限制临时文件大小。响应不是2xx时不会落盘。SHA-256作为完整性检查标识，不代表内容可信或无恶意代码。代理的DNS请求也计入HTTP超时预算；Cloudflare DoH接收待访问域名，会带来相应的域名查询隐私权衡。
3. 下载目标复用命名root写入授权和PLA私有路径策略；父目录必须已经存在。默认禁止覆盖，通过临时同目录文件与硬链接原子创建；显式overwrite才使用os.replace替换普通文件。失败或取消清理临时文件。
4. HTTP状态检查只返回状态码、响应原因、Content-Type、Content-Length及跳转次数，不返回页面内容、Cookie、授权头或URL查询参数。
5. DNS解析与TCP建立连接是尽力而为的诊断。DNS-only会返回解析地址、public_egress_allowed与classification，遇到特殊用途IP会标记non_public_dns但不会建立连接；HTTP下载及TCP仍严格拒绝连接。DNS由操作系统提供，系统解析阻塞时间不能完全受Python超时参数控制。
6. 不解析/执行下载的资源，不自动安装软件。下载需按普通文件进行后续安全检查。

## 调用示例

通过capability_invoke选择core.http_probe，参数为：

```json
{"url":"https://example.org/","method":"HEAD","timeout":8}
```

下载时选择core.http_download并传入INVOKE确认（不是给参数体添加confirmation字段）：

```json
{"url":"https://example.org/archive.zip","destination":"downloads/archive.zip","root":"workspace","overwrite":false}
```

Fake-IP环境使用代理模式需主动传入 `"transport":"system_loopback_proxy"`；默认仍为 `direct`，不会自动切换，也不会把本机代理或Fake-IP地址放入公网目标白名单。仅支持系统已配置的无认证127.0.0.1/::1 HTTP代理。用户应信任自己的本机代理程序；若代理本身恶意改写CONNECT目标，PLA无法保证其忠实转发，TLS只能阻止目标身份不匹配。Cloudflare DoH不可用时拒绝请求，不自动放宽DNS规则。

DNS诊断示例：

```json
{"host":"example.org","kind":"dns"}
```

## 测试与部署

- python -m pytest -q tests/test_network_operations.py tests/test_network_proxy_fakeip.py tests/test_network_reliability.py
- python -m pytest -q tests/test_file_operations.py tests/test_core_capabilities.py tests/test_capability_broker.py tests/test_multi_root.py tests/test_runtime_parity.py tests/test_server_run_process_backend.py
- 更新的是仓库源码，当前已运行的PLA不会自动加载新增Python模块与能力，需按已有发布流程重建/重启后实际MCP端到端测试。
- CHAGENT.md的仓库默认规则增加文件管理和公网HTTP路由；若Desktop个人配置存在独立CHAGENT.md覆盖版本，需要同步对应规则。

## 本机网络兼容性观察

2026-10-28真实探测发现当前Windows机器将example.org、example.com等公网域名解析到198.18.0.x及2001:2::地址，属于特殊用途地址段，疑似VPN/TUN的Fake-IP映射。DNS-only诊断现在可报告non_public_dns，但HTTP/TCP继续拒绝通过这些地址发起请求。已增加明确选择的system_loopback_proxy模式：发现本机系统代理127.0.0.1:7897，成功通过TLS保护的Cloudflare DoH获取真实公网IP；example.org的HEAD返回HTTP 200，实际下载577字节并校验SHA-256。独立新进程FastMCP Client经capability_search与capability_invoke确认后再次完成真实下载；实际PLA_TEST已通过能力发现、受控下载、文件整理和权限拦截验收。Fake-IP地址仍被direct模式拒绝。

## VPN链路可靠性优化（2026-10-28）

1. 为代理DoH结果增加进程内LRU缓存（最大64个域名代理组合）。缓存键包含域名及系统回环代理的IP和端口；TTL遵守DoH回答并封顶30秒，TTL=0、不完整解析、超时、证书错误、私网/Fake-IP结果都不缓存。每次缓存命中仍重新确认目标为全球可路由地址；不把Fake-IP地址作为下载目标。
2. DoH A和AAAA仍按顺序查询；单次查询最长4秒，每一类最多重试一次，可重试超时、连接中断、临时SSLEOFError，证书验证失败、权限拒绝和恶意/格式错误的DNS回答不重试。总体受原HTTP请求30秒上限约束。
3. 已验证公网IP的代理TLS握手若遭遇SSLEOFError、连接复位或超时，仅在HEAD/GET连接尚未收到响应头时补一次重试；每次仍校验证书、保持公网IP固定，不允许对证书错误重试，也不自动切换代理。
4. HTTP探测及下载返回`timings_ms`：`dns`、`proxy_connect`、`tls_handshake`、`response_headers`、`total`；下载额外返回`body`，另含`dns_cache_hits`、`dns_retries`、`connect_retries`。均为整数毫秒或有限计数，无URL/域名/路径。HTTP失败的事件日志在脱敏失败分类后增加固定枚举`network_stage`，仅可能为dns、proxy_connect、tls_handshake、response_headers、body。
5. 独立Python源码实网重复探测：首次返回HTTP 200约10秒，随后缓存命中请求约1.34秒和2.69秒，DNS计时均为0毫秒。真实下载577字节SHA-256校验成功；128字节大小限制拦截成功，未遗留临时文件。速度与VPN链路实时状态有关，不保证固定延迟。
6. 源码回归需执行`python -m pytest -q tests/test_network_reliability.py tests/test_network_proxy_fakeip.py tests/test_network_operations.py tests/test_failure_diagnostics.py`并完成联合回归。优化后的代码仍需重建、安装、重启PLA_TEST才能完成新版本真实MCP联机验收。

## 暂缓范围

本机/局域网HTTP检查、专属网络授权界面、HTTP非443端口、远端身份凭据、上传/POST与请求体都未开放。未来如果需要，独立设计授权与安全策略。