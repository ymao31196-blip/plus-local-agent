# PLA Desktop Agent Pet — optional local companion

## Lifecycle

The Q-style character is a Desktop companion displayed on the same page as MCP
providers. It is explicitly marked as not being an MCP provider, and it exposes
no extra ChatGPT tools. It reads PLA's local SQLite event store read-only.

- Initially disabled, with no background GUI process.
- Enabling with Runtime stopped saves the preference; the character waits.
- When Runtime becomes ready, Desktop Manager spawns its exact owned pet role.
- Enabling while Runtime is already running launches the character immediately.
- Disabling terminates only the pet child; Runtime, Tunnel and other Python programs remain untouched.
- Runtime stop and Desktop quit terminate the pet.
- Manual exit closes the pet for that Runtime session. Re-enable or restart Runtime to launch again.
- An unexpected Runtime exit is detected during status polling and terminates the pet.
- Optional pet GUI failure must never prevent Runtime startup.

Implementation includes new Desktop Manager pet actions and component-list card,
plus a PyInstaller pet role in the packaged pla-runtime executable.

## Asset ownership

Public PLA installers contain no character artwork. Use your own or explicitly
licensed PNG frames in the following private data directory:
%LOCALAPPDATA%/io.pla.desktop/components/agent-pet/frames3d

For a custom --data-dir, use that directory instead of the default above.
Keep any required artwork notices alongside your locally supplied frames.

Frame groups: idle 6, work 6, waiting 6, review 6, failed 8.


On other machines the component will show that assets are missing, with
enable disabled. No network download, global Python installation or arbitrary
third-party script execution is performed.

## Packaging and limits

The new desktop role uses Tkinter from the frozen runtime, not system Python.
The PyInstaller spec now requests tkinter and _tkinter. Rebuild/reinstall the
Desktop package to make the new Rust allowlisted commands and Python role
available. Packaged GUI validation results are recorded in desktop-v1-rc17-validation.md.

Only PLA tool activity is observable. Absence of events cannot prove that
ChatGPT's cloud inference has stopped or that a task has completed.
The enabled preference is saved in private Desktop configuration.

## QA

Tests in tests/test_desktop_agent_pet.py cover preference persistence, missing
assets, explicit confirmation, process ownership, Runtime start and stop,
no duplicate GUI launches and manual closure behavior.

After rebuilding, manually verify enabling while Runtime is off, automatic
appearance after Runtime start, real tool event timestamps and status, live
disable, re-enable, Runtime stop and full Desktop exit.

## 用ChatGPT + PLA更换桌宠形象（无需素材导入界面）

用户可在对话中让ChatGPT通过PLA已有的本地文件、Python、图像处理工具完成美术素材的准备和替换。**桌宠运行逻辑无需改动，也不添加素材管理GUI或新的MCP工具。** 执行前必须遵守PLA原有的工作区、组件私有目录和工具授权限制；若当前工具没有该组件目录的合法写入权限，应明确告知用户并通过正常授权流程解决，不绕过安全边界。

### 本地素材目录和固定契约

本机目标目录（不属于原始源码仓库）：

```text
%LOCALAPPDATA%\io.pla.desktop\components\agent-pet\frames3d\
```

当前的`PetCompanion.installed()`按以下文件名检查**全部32张PNG是否存在**：

| 桌宠场景 | 文件命名范围 | 帧数 |
| --- | --- | ---: |
| 空闲/发呆（idle） | `idle_0.png`～`idle_5.png` | 6 |
| 工具活跃（work） | `work_0.png`～`work_5.png` | 6 |
| 等待工具返回（waiting） | `waiting_0.png`～`waiting_5.png` | 6 |
| 短时安静/观察（review） | `review_0.png`～`review_5.png` | 6 |
| 工具失败（failed） | `failed_0.png`～`failed_7.png` | 8 |

连接失败状态也复用`failed`动画。大小写、从0开始的连续索引、扩展名必须保持一致；不要将五种状态合并为一张精灵图。当前程序用Tkinter`PhotoImage`逐帧直接加载PNG，**不会自动缩放、裁切或拆分GIF/SVG/视频**。AI如拿到其他格式，需要先在允许的工作区处理成上表对应的PNG序列。

素材推荐尺寸为**200×200像素**。更换时建议统一输出200×200的透明背景PNG，逐帧保持角色的位置、比例和脚底基线一致，以减少抖动和超出悬浮窗的问题。200×200是当前布局的推荐规格，并非`installed()`的硬编码尺寸条件；透明背景也属于显示建议。动画每约180毫秒推进一帧，宜检查相邻帧是否连贯。

### 建议的AI操作顺序

1. **确认素材来源和使用范围**：使用用户提供、用户有权使用或明确允许相关用途的素材。代码开源不代表角色立绘必然可公开再分发；本地个人使用与打包发布要分开判断。
2. **检查现状并准备新素材**：读取本规范与当前组件状态；仅在已授权的本地工作目录内预处理图片、调整为一致规格、拆帧并按5组共32张文件命名。校验PNG文件头、完整解码、尺寸一致和帧数。不要将任意外部脚本当作可直接执行的安装程序。
3. **保存可恢复备份**：用户确认替换后，先对当前32张素材及来源说明做有标识的备份；使用临时目录完成所有准备和检查。禁止直接覆盖桌宠代码、Runtime数据库、其他组件目录或用户无关文件。
4. **受控替换**：通过PLA已授权的本地能力操作目标目录。切换前先停用桌宠（或从右键菜单退出），再只替换`frames3d`内对应的32张图片；失败时恢复原素材。已经运行的桌宠将图片缓存到内存，换图后**必须重新启动桌宠**才能显示新角色。
5. **完成验证**：核对32张PNG都能打开、组件仍显示素材已安装、启用后角色正确显示，再触发实际PLA工具调用检查工作/等待/空闲/异常状态对应的动画。发现角色抖动、遮挡、越界、透明底异常或启动错误时恢复备份。

`installed()`目前只核查文件存在和安全路径，**不保证PNG可解码或画面美观**；所以真正的图像解码检查和GUI验证不可省略。若ChatGPT通过当前PLA节点无法访问目标目录或执行受控替换，应如实说明阻断，不暗示任务已完成。

### 用户可直接发送的指令示例

> 用PLA帮我更换桌宠形象。我已提供一套拥有使用权限的Q版素材。请先阅读`docs/desktop-agent-pet.md`中的素材规范，按idle/work/waiting/review/failed五组生成共32张200×200透明PNG，检查命名、解码和动画对齐。确认后备份旧素材，只替换`%LOCALAPPDATA%\io.pla.desktop\components\agent-pet\frames3d`中的图片；按现有授权要求操作，重启桌宠并检查状态显示。不要修改其他PLA组件或自动发布素材。

完整的组件管理/生命周期仍以本文件上文为准；不需要建立素材导入页面。自动更换形象属于AI操控**本地素材文件**，并不意味着桌宠自己会从网络检索或下载素材。
