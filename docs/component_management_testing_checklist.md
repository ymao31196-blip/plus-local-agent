> 历史开发移交材料：下文原有的完成率和部署声明不代表当前发行验收。2026-10-11 的实际修复与测试记录见 [组件管理修复进度](component_management_repair_status.md)。新版安装包验收尚在进行。

# Component Management Testing Checklist

## 测试环境信息

- **测试仓库位置**: `D:\AI_Tools\plus-local-agent\test-provider-repository`
- **仓库 URL**: `file:///D:/AI_Tools/plus-local-agent/test-provider-repository`
- **测试 Provider**: `test-echo-provider` v1.0.0

## Phase 1-3 功能验证清单

### 阶段 1: 仓库管理

- [ ] **添加本地仓库**
  1. 打开 PLA Desktop
  2. 进入"组件管理"页面
  3. 点击"仓库管理"或使用 `view-repositories` 按钮
  4. 点击"Add Repository"
  5. 输入仓库 URL: `file:///D:/AI_Tools/plus-local-agent/test-provider-repository`
  6. 验证: 仓库成功添加并显示在列表中

- [ ] **查看仓库列表**
  - 验证显示仓库名称: "PLA Test Repository"
  - 验证显示仓库 URL
  - 验证显示"Enabled"状态

- [ ] **浏览仓库内容**
  1. 点击仓库的"Browse"按钮
  2. 验证: 切换到 Provider Marketplace 视图
  3. 验证: 显示"Test Echo Provider"

### 阶段 2: 组件发现和浏览

- [ ] **发现可用组件**
  1. 在 Marketplace 中点击"Refresh"
  2. 验证: 加载并显示 Provider 列表
  3. 验证: "Test Echo Provider"显示为可安装状态

- [ ] **查看组件详情**
  1. 点击"Test Echo Provider"的"Details"按钮
  2. 验证详情对话框显示:
     - Provider ID: test-echo-provider
     - 作者: PLA Development Team
     - 许可证: MIT
     - 类别: general
     - 可用版本: 1.0.0
     - 变更日志

- [ ] **搜索功能**
  1. 在搜索框输入"echo"
  2. 验证: 只显示匹配的 Provider
  3. 清空搜索框验证恢复所有结果

- [ ] **分类过滤**
  1. 选择"General"分类
  2. 验证: 显示正确分类的 Provider

### 阶段 3: 组件安装

- [ ] **安装组件（完整流程）**
  1. 点击"Test Echo Provider"的"Install"按钮
  2. 验证: 显示确认对话框
  3. 确认安装
  4. 验证安装过程:
     - 显示"Installing..."通知
     - 创建隔离环境
     - 安装依赖 (mcp==1.1.3)
     - 部署资产文件
     - 注册 manifest
  5. 等待安装完成（约1-3分钟）
  6. 验证: 显示"Successfully installed"通知
  7. 验证: 组件卡片显示"Installed"标签

- [ ] **检查安装状态**
  1. 刷新 Marketplace
  2. 验证: "Test Echo Provider"显示为已安装
  3. 点击"查看已安装组件"
  4. 验证: 列表中显示 test-echo-provider

### 阶段 4: 健康检查

- [ ] **执行健康检查**
  1. 在已安装组件中找到 test-echo-provider
  2. 点击"Health"或"健康检查"按钮
  3. 验证健康检查结果:
     - environment_exists: ✓
     - python_exists: ✓
     - manifest_exists: ✓
     - assets_present: ✓
     - status: healthy

### 阶段 5: 启用和使用

- [ ] **启用组件**
  1. 进入"MCP 插件与 Provider"页面
  2. 找到 test-echo-provider
  3. 点击"Enable"按钮
  4. 等待启用完成
  5. 验证: 状态显示为"Connected"或"已连接"

- [ ] **通过 ChatGPT 测试**
  1. 确保 Runtime 和 Tunnel 运行中
  2. 在 ChatGPT 输入:
     ```
     使用 test-echo-provider 的 echo_message 工具，发送消息 "Hello PLA Component Management!"
     ```
  3. 验证: ChatGPT 返回 "Echo: Hello PLA Component Management!"
  4. 测试结构化数据:
     ```
     使用 test-echo-provider 的 echo_structured 工具，发送 {"test": "data"}
     ```
  5. 验证: 返回包含原始数据的结构

### 阶段 6: 升级流程（模拟）

**注意**: 测试仓库只有 v1.0.0，此测试需要手动创建 v1.1.0

- [ ] **检查更新**
  - 在 Marketplace 刷新
  - 如果有新版本，验证显示"Update Available"标签

- [ ] **执行升级**（如果创建了 v1.1.0）
  1. 点击"Upgrade"按钮
  2. 确认升级
  3. 验证升级过程
  4. 验证: 版本更新成功

### 阶段 7: 禁用和卸载

- [ ] **禁用组件**
  1. 进入"MCP 插件与 Provider"页面
  2. 找到 test-echo-provider
  3. 点击"Disable"按钮
  4. 验证: 组件停止运行

- [ ] **卸载组件**
  1. 进入"组件管理" → "已安装组件"
  2. 找到 test-echo-provider
  3. 点击"Uninstall"或"卸载"按钮
  4. 确认卸载
  5. 验证卸载过程:
     - 移除 manifest
     - 删除资产文件
     - 清理隔离环境
  6. 验证: 组件从已安装列表中消失
  7. 验证: Marketplace 中恢复为"可安装"状态

### 阶段 8: 仓库移除

- [ ] **移除仓库**
  1. 进入"仓库管理"
  2. 点击测试仓库的"Remove"按钮
  3. 确认移除
  4. 验证: 仓库从列表中消失

## 错误处理测试

### 安装错误

- [ ] **重复安装**
  1. 安装 test-echo-provider
  2. 尝试再次安装
  3. 验证: 显示"已安装"错误

- [ ] **卸载已启用的组件**
  1. 启用 test-echo-provider
  2. 尝试卸载
  3. 验证: 显示"先禁用"错误

### 仓库错误

- [ ] **无效 URL**
  1. 尝试添加不存在的仓库 URL
  2. 验证: 显示"无法访问"错误

- [ ] **损坏的仓库**
  1. 创建包含无效 JSON 的仓库
  2. 尝试添加
  3. 验证: 显示解析错误

## 性能测试

- [ ] **安装时间**
  - 记录完整安装时间
  - 预期: 1-3 分钟（取决于网络）

- [ ] **健康检查时间**
  - 记录健康检查时间
  - 预期: < 1 秒

- [ ] **发现时间**
  - 记录从添加仓库到显示 Provider 的时间
  - 预期: < 5 秒

## UI/UX 测试

- [ ] **响应式布局**
  - 调整窗口大小
  - 验证: UI 正确适配

- [ ] **通知显示**
  - 验证成功、错误、信息通知正确显示
  - 验证通知自动消失或可关闭

- [ ] **加载状态**
  - 验证长操作显示加载指示器
  - 验证按钮在操作期间禁用

- [ ] **错误提示**
  - 验证错误消息清晰易懂
  - 验证提供解决建议

## 数据持久化测试

- [ ] **重启后保持状态**
  1. 安装组件
  2. 关闭 PLA Desktop
  3. 重新打开
  4. 验证: 已安装组件仍在列表中
  5. 验证: 仓库配置保持

- [ ] **配置文件检查**
  - 检查 `%LOCALAPPDATA%\io.pla.desktop\config\provider-repositories.json`
  - 检查 `%LOCALAPPDATA%\io.pla.desktop\components\provider_states.json`
  - 验证: 文件格式正确

## 集成测试

- [ ] **与现有 Provider 共存**
  1. 安装测试组件
  2. 验证: 不影响现有 bundled providers
  3. 验证: 两者可同时启用

- [ ] **Workspace 权限**
  1. 测试组件文件访问遵守 workspace 授权
  2. 验证: 隔离环境在正确位置

## 日志和诊断

- [ ] **安装日志**
  1. 安装组件期间
  2. 进入"日志与诊断"页面
  3. 验证: 显示 `[provider_install:test-echo-provider]` 日志

- [ ] **错误诊断**
  1. 触发安装失败
  2. 查看日志
  3. 验证: 错误信息足够详细

## 已知限制验证

- [ ] **并发安装**
  1. 尝试同时安装两个组件
  2. 验证: 第二个被阻止或排队

- [ ] **Git 仓库**
  - 确认 Git 仓库支持未实现

- [ ] **数字签名**
  - 确认只有 checksum 验证，无签名验证

## 测试通过标准

所有核心功能测试项必须通过：
- ✅ 仓库管理（添加、列表、移除）
- ✅ 组件发现和浏览
- ✅ 组件安装
- ✅ 健康检查
- ✅ 组件启用和使用
- ✅ 组件卸载
- ✅ 基本错误处理

可选测试项可以部分通过。

## 测试报告模板

```
测试日期: ____________________
测试人员: ____________________
PLA 版本: ____________________

通过测试项: _____ / _____
失败测试项: _____

关键问题:
1. 
2. 
3. 

性能指标:
- 安装时间: _____ 秒
- 健康检查: _____ 毫秒
- 发现时间: _____ 秒

建议改进:
1. 
2. 
3. 

总体评价: ☐ 通过  ☐ 需要修复  ☐ 不通过
```

## 下一步测试（可选）

如果基础测试通过，可以继续：

1. **创建多版本测试**
   - 添加 v1.1.0 到仓库
   - 测试升级流程

2. **创建多 Provider 测试**
   - 添加第二个 Provider 到仓库
   - 测试批量操作

3. **HTTPS 仓库测试**
   - 部署到 Web 服务器
   - 测试远程访问

4. **压力测试**
   - 大量 Provider 列表
   - 大依赖包安装

## 测试完成后

- [ ] 清理测试数据
- [ ] 卸载测试组件
- [ ] 移除测试仓库
- [ ] 删除测试仓库目录（可选）
- [ ] 提交测试报告
