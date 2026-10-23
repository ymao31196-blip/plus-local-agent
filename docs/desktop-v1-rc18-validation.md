# PLA Desktop RC18 发布构建核验（2026-10-22）

本记录对应已发布的[PLA Desktop v1.0.0-rc.18 Windows x64预发布](https://github.com/ymao31196-blip/plus-local-agent/releases/tag/desktop-v1.0.0-rc.18)。

- 发布源码提交：`e68aa9dd003059cfec2ab651f6174df71c5ac6d7`
- [GitHub Actions构建记录](https://github.com/ymao31196-blip/plus-local-agent/actions/runs/39755241063)：`success`
- Windows CI：972项Python测试通过；Desktop组件UI测试、Runtime冻结、NSIS构建和产物上传均成功
- 安装程序：`PLA Desktop_1.0.0-rc.18_x64-setup.exe`
- SHA-256：`3c0e00ce34c564ef861de4be49c4b9ce15458c7019d44d412a744896f606e1c4`
- Release附件：安装包、`SHA256SUMS.txt`、`build-manifest.json`
- 分发限制：未进行代码签名；没有自动更新；属于实验性预发布版本

这一记录只证明对应提交的CI及构建产物通过。全新Windows主机上的安装、Runtime/Tunnel连接、重启与升级路径等实际验收，应以安装环境中的测试为准。

原RC17未发布候选及其失败诊断保留在[`desktop-v1-rc17-validation.md`](desktop-v1-rc17-validation.md)，作为历史记录，不代表RC18的当前发布状态。
