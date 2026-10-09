# Skill Library Provider 安装与来源配置

Skill Library是PLA的可选Provider，代码位于独立的`chatgpt-skill-library`项目中。PLA不会把开发者个人的GitHub仓库当作用户的默认Skill来源。

## 运行前提

PLA的`setup_providers.ps1 -Provider skill-library`负责安装该Provider的Python运行依赖；Skill Library自身的Python包需要独立安装。任选一种：

1. 将已取得访问权限的Skill Library源码放到`workspace/skill-library`，保留`src/skill_library/server.py`。PLA入口会在该位置加载源码。
2. 运行`setup_skill_library.ps1`，向`.provider_envs/skill-library/Scripts/python.exe`安装Skill Library 0.4.0包。PLA入口会在没有本地源码目录时使用已安装的包。

不要把仅有`SKILL.md`的个人Skills仓库当成Skill Library服务端源码。它们属于用户随后登记的**Source**。

已取得Skill Library代码访问权限的用户，可从独立仓库克隆对应`v0.4.0`标签到`workspace/skill-library`，然后执行`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\setup_skill_library.ps1`。也可以提供已获得的本地Wheel：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\setup_skill_library.ps1 -SourcePath C:\\path\\chatgpt_skill_library-0.4.0-py3-none-any.whl`。只检查本地来源可使用`-ValidateOnly`。PLA主安装器支持`-SkillLibraryPackage <本地源码目录或Wheel路径>`，省去独立执行安装脚本的步骤。两种方式都不会自动开放任何本地Skill读写权限。独立仓库如处于私有状态，用户需要自行获得合法代码访问权限；不能假设所有GitHub访问者都能下载。

## 配置

公共`config/skill-library.json`只定义数据目录和默认空白的本地文件访问范围（读取、写入分别授权）：

```json
{
  "data_dir": "state/skill-library",
  "local_roots": [],
  "writable_roots": []
}
```

如需允许Skill Library在PLA本机读取特定文件夹，创建不入库的`config/skill-library.local.json`：

```json
{
  "local_roots": ["workspace/skill-library"],
  "writable_roots": [],
  "repo": "your-account/your-legacy-skill-repository",
  "branch": "main",
  "transport": "git"
}
```

以上`repo`字段仅为旧版`refresh_library`保留；**新增多来源工作流无需设置该字段**。即使只连接GitHub Source，也无需配置`local_roots`。

`local_roots`必须是PLA目录以内的相对路径，默认拒绝访问任意本地目录。不要将整个磁盘、用户主目录或PLA根目录加入允许范围。需要更广泛的目录权限时应先明确审查。

## ChatGPT调用示例

通过PLA连接后，使用Skill Library的以下Capability：

```text
skill-library.manage  → action=add, source_id=my-github, kind=git, location=owner/skills-repo
skill-library.sync    → source_id=my-github
skill-library.find    → query=Markdown authoring
skill-library.load    → name=my-github:github-markdown-authoring
skill-library.sources → 查看所有来源和缓存状态
```

Source Registry配置和缓存保存在服务端`state/skill-library`，按来源隔离；不允许Skill自行执行脚本或扩展PLA的权限。

如需使用v0.4单Skill启用/停用，可通过`skill-library.states`查看状态、`skill-library.toggle`设置开关。若需要删除或重命名，可先调用`skill-library.plan-change`预览完整文件清单，经确认后使用`skill-library.apply-change`执行，删除的Skill可用`skill-library.restore-local`恢复。`skill-library.publish-plan`为拥有个人GitHub账户的用户提供首次发布方案，真正创建GitHub仓库与推送依旧经由需要独立授权的GitHub CLI和PLA受控Git能力执行。

如果用户需要直接维护自己的SKILL.md，可另行在本机私有配置中将**受控的Skill仓库目录**加入`writable_roots`（必须位于`local_roots`允许范围内），重载Provider后使用`skill-library.prepare`预览草稿、`skill-library.apply-local`在明确确认后写入。写入仅限已登记本地Source的`skills/<name>/SKILL.md`，并以文件SHA避免覆盖并发修改；Git提交/推送仍由PLA受控Git能力处理。参见Skill Library的`docs/authoring_v03.md`。

## 升级与验证

安装或更换Skill Library服务端后，通过PLA的`runtime.provider_reload`重新发现19项工具，然后运行`runtime.provider_status`或`provider_doctor`确认健康状态；更改独立Provider时无需重启整套PLA。若报缺少`skill_library`模块，先检查独立包是否实际安装在Provider运行环境中。

重要限制：配置与身份隔离是**按服务部署实例**实现的。不能将同一个无多租户隔离的Provider实例公开给彼此不信任的不同用户。
