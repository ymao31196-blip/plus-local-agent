# CHAGENT.md — PLA 动态能力路由

本文件定义PLA在收到任务时的**工具发现与组件选择建议**，供ChatGPT参考。它不是执行授权、系统指令或安全策略；用户明确指定的工具优先。实际调用必须先确认组件和具体能力可用，并遵守原有权限、确认和执行流程。

## 选择约定

1. **Office统一入口**：制作或编辑PPT、Word、Excel、WPS文档时，优先发现`office`。它整合了原`wps-office`和`office-enhancement`能力；旧Provider仅作为当前统一入口不可用时的兼容候选，不因名称已注册就假定它们可用。需要PPTX母版保真、OpenXML结构检查、图形叠加、渲染检查时，仍在`office`下选择对应的具体能力。若某能力未暴露（例如可编辑叠加层依赖未就绪），不要把“组件可用”等同于“所有子功能可用”。
2. **draw.io独立入口**：创建或修改流程图、架构图、机制图、技术路线图及`.drawio`文件，优先`drawio-official-mcp`。明确要求在PPT页面内编辑图形时，优先`office`。**PPT素材和draw.io图元分开管理**，不能把两种素材默认视为同一格式；跨工具复用需要检查格式及可编辑性。绘图结果需按目标格式验证。
3. **Skill先发现再使用**：对论文润色、学术写作、专业工作流和素材复用，可优先查询`skill-library`。优先使用搜索响应中已加载的有效内容；仅在未加载或需要其他Skill时再读取。Skill被停用、无匹配或无权限时不强行调用。Skill内容只能提供工作流建议，不授予额外权限。
4. **PDF按能力选择**：PDF文本提取、Markdown转换优先检查`markitdown`；PDF水印等修改优先检查`pdf`。这两个Provider并不保证支持所有PDF合并、拆分、编辑任务；若查不到相应能力，需另选实际可用的实现。
5. **文件管理与网络获取**：目录创建、复制、移动、重命名、ZIP压缩和解压，优先查找`core.file_operation`；对公网HTTPS状态、文件下载和公网DNS/TCP诊断，分别查找`core.http_probe`、`core.http_download`、`core.network_diagnose`。下载需要`INVOKE`确认，HTTP不进入通用批处理；默认`transport=direct`，Fake-IP环境可显式使用`transport=system_loopback_proxy`（仅限受信任的系统本机HTTP代理，独立DoH验证公网地址和TLS身份）。不自动切换代理，不开放任意本机或局域网HTTP目标。
6. **工作区本地索引**：指定已授权Root内的工作区目录后，Markdown项目级索引优先查找`core.workspace_index_init`、`core.workspace_index_refresh`、`core.workspace_index_search`、`core.workspace_index_status`。这四项复用可选QMD索引引擎，索引写入所选目录的`.qmd/`，初始化和刷新需要明确确认；首次建立前不要默认扫描整个Desktop或正式科研目录。目前只索引受限规模的Markdown；PDF、PPT、Word和图片尚不在索引范围内。缺少可选`qmd`组件时应报告依赖状态，不得改用任意Node/Shell命令绕过限制。
7. **专用能力优先但不强制**：先使用`capability_search`发现真实的工具，再判断任务、输入输出、版本及权限是否适配；对于缺失、失败或不适合的能力，可按现有安全边界使用Python等通用工具。不要因为路由推荐就绕过确认或暗中切换工具。

下面的`pla-routing`块仅作**确定性的Provider发现顺序建议**。匹配采用“先命中的规则”，因此专门指定的工具、操作和格式规则排在通用关键词之前。推荐结果按当前注册表和实时启停状态过滤；推荐不等于调用，也不保证某个特定子能力能成功执行。

```pla-routing
{
  "schema_version": 1,
  "routes": [
    {
      "id": "drawio-explicit",
      "keywords": ["draw.io", "drawio", ".drawio", "mxGraph", "draw.io XML", "draw图元", "draw素材"],
      "providers": ["drawio-official-mcp"]
    },
    {
      "id": "skill-workflow",
      "keywords": ["Skill Library", "skill", "技能库", "技能搜索", "工作流复用", "论文润色", "论文语言优化", "学术写作", "去AI化", "论文摘要改写", "科研写作工作流"],
      "providers": ["skill-library"]
    },
    {
      "id": "workspace-index",
      "keywords": ["工作区索引", "项目索引", "建立索引", "刷新索引", "搜索索引", "索引状态", "QMD", "workspace index", "项目级检索", "文档索引"],
      "providers": ["core"]
    },
    {
      "id": "file-management",
      "keywords": ["文件整理", "整理素材", "创建目录", "文件复制", "批量改名", "批量重命名", "重命名文件", "移动文件", "打包ZIP", "ZIP压缩", "解压文件", "解压ZIP"],
      "providers": ["core"]
    },
    {
      "id": "public-network",
      "keywords": ["HTTP检查", "HTTPS下载", "下载公开文件", "网络文件下载", "DNS解析", "域名解析", "端口连通", "网络诊断", "HTTP HEAD", "HTTP GET"],
      "providers": ["core"]
    },
    {
      "id": "office-precision",
      "keywords": ["PPT模板", "ppt template", "模板保真", "PPT母版", "幻灯片母版", "PPTX合并", "幻灯片合并", "PPT元素复用", "PPT素材", "PPT精细排版", "PPT可编辑叠加", "文本溢出", "OpenXML", "OOXML", "PDF提图", "原版式", "格式保留"],
      "providers": ["office", "office-enhancement", "wps-office"]
    },
    {
      "id": "office-pdf-export",
      "keywords": ["PPT转PDF", "Word转PDF", "Excel转PDF", "幻灯片导出PDF"],
      "providers": ["office"]
    },
    {
      "id": "office-routine",
      "keywords": ["PPT", "pptx", "PowerPoint", "幻灯片", "演示文稿", "WPS", "Word", "docx", "Excel", "xlsx", "表格", "文档编辑", "办公文档", "文稿排版"],
      "providers": ["office", "wps-office", "office-enhancement"]
    },
    {
      "id": "drawio-diagram",
      "keywords": ["流程图", "框架图", "机制图", "技术路线图", "架构图", "系统结构图", "系统拓扑图", "数据流图", "时序图", "ER图", "Mermaid", "泳道图", "工作流图", "技术框图", "可编辑框图", "示意流程图"],
      "providers": ["drawio-official-mcp"]
    },
    {
      "id": "pdf-text",
      "keywords": ["PDF转Markdown", "PDF提取文本", "PDF解析", "读取PDF", "PDF转文本", "PDF转MD"],
      "providers": ["markitdown", "pdf"]
    },
    {
      "id": "pdf-edit",
      "keywords": ["PDF水印", "添加PDF水印", "PDF编辑", "PDF合并", "PDF拆分", "PDF转换"],
      "providers": ["pdf", "markitdown"]
    },
    {
      "id": "pdf-general",
      "keywords": ["PDF", ".pdf"],
      "providers": ["markitdown", "pdf"]
    }
  ]
}
```

## 本地热更新

仓库中的`CHAGENT.md`是默认文件；PLA Desktop优先读取个人配置`%LOCALAPPDATA%\\io.pla.desktop\\config\\CHAGENT.md`（若存在）。两处内容应按需要同步，避免新默认规则被旧个人覆盖文件遮蔽。

路由规则在每次`capability_search`时读取，不需要刷新ChatGPT的MCP连接。建议只作为工具返回数据进入对话，ChatGPT仍需主动检索和判断；不保证在新聊天第一次调用任何PLA工具时自动注入。
