# 仓库更名与旧版兼容

产品与仓库统一为 [Cursor Panel](https://github.com/devilcoolyue/cursor-panel)。发行记录、版本查询和发布工具使用新地址；现有仓库直接重命名，历史 Release 与附件保留，旧链接由 GitHub 重定向。不要重新创建 `devilcoolyue/cursor-dashboard` 仓库，否则会中断重定向。

`v0.0.2`–`v0.0.4` 更新器会检查清单中的下载 URL 是否属于旧仓库。因此，`latest.json` 与签名 `server-update.json` 中的安装包 URL 继续使用 `cursor-dashboard` 路径；当前源码中的桌面与服务器更新器接受新旧两个固定仓库的同版本附件，仍验证原签名。旧客户端直接查询最新正式版本，不会逐版安装；只要继续支持旧客户端，后续每次发版都必须保留这一下载路径兼容。

仓库更名不改变 `dev.cursor-panel.desktop` 应用标识、数据目录、系统凭证库标识及更新签名密钥。Python 包名 `cursor-dashboard` / 导入名 `cursor_dashboard` 和已有部署路径保留兼容用途；新版本的源码与验证附件使用 `cursor-panel-v版本号-*`，历史附件不改名、不覆盖。

本地克隆可更新远端地址，工作目录名称不影响运行：

```bash
git remote set-url origin https://github.com/devilcoolyue/cursor-panel.git
```

发布流程同时检查新旧地址下的公开更新清单与构建产物一致。跨版本安装仍须使用最终签名包在 macOS arm64/x64 和 Windows x64 上验收；不能以链接跳转或合成测试代替实际升级验证。常规发版步骤见[固定发布流程](release-automation.md)。
