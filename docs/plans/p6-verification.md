# P6 验证报告

实施日期：2026-09-10。分支：`feat/v2-p6`。发布准备、贡献资料与更新/回退演练已实现，本机实际候选构建与安装/恢复验证已通过，三平台候选 CI 待补录。**许可证等待维护者定稿，LICENSE 尚未建立，因此 P6 尚未全部验收。** macOS/Windows 正式签名、公证与干净设备安装不在已完成验证中；本阶段准备签名方案，当前产物为 unsigned `v2-preview`。

P5 的真实 Cursor S01–S06 实验仍未完成，`remote_switch=false`。本阶段不读取真实账号、运行库或密钥，不执行真实 Cursor 切换，也不创建正式 Release/标签或推送镜像。

## 交付内容

- 三种入口的上手文档与 [手动更新、版本校验、签名方案](../v2-release-operations.md)，包括服务端与桌面各自备份/恢复边界及连接协议不兼容处理。
- [支持平台表](../supported-platforms.md)、[贡献指南](../../CONTRIBUTING.md)、缺陷/功能问题模板，明确历史 P5 平台证据与本次新候选的区别。
- `dev/release.py` 的版本/锁一致性、跟踪源文件与打包内容检查、按目标复制明确产物、manifest/SHA-256 生成及完整性验证。拒绝脏源码候选；本地 `--allow-dirty` 会明确标记仅供本地验证。
- [P6 CI](../../.github/workflows/p6-release.yml) 构建 Web wheel、Linux amd64 镜像、macOS arm64/x64 DMG、Windows x64 NSIS，先验证再上传；权限仅 `contents: read`。
- 镜像 OCI 版本、提交、时间标签；Compose 通过同一个 `CURSOR_PANEL_IMAGE` 指定服务与维护镜像。前端包从内部 `0.0.0` 对齐产品现有 `1.4.0`，Python/桌面产品版本保持 `1.4.0`。
- README、maintenance、operations、核心/桌面/连接/Web 使用说明及 CLAUDE 维护指引同步当前行为，不更改 legacy 基线或 P5 能力开关。

## 本机验证

macOS arm64；测试使用临时文件与合成材料。

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 292 项，283 通过，9 项因缺少 PowerShell 跳过；106.641 秒 |
| P6 产物检查与恢复专项 | 8 项通过 |
| Vue 类型与生产构建、Web wheel 构建 | 通过 |
| 安装后的 Web wheel | 独立临时虚拟环境安装，迁移/恢复 CLI、登录/列表/退出及远程 CLI 全部通过 |
| Linux arm64 容器（本机 Docker Desktop） | 镜像启动、持久化、账号备份/恢复及恢复后会话撤销通过；检查 93 个包文件 |
| macOS arm64 桌面 | `.app` / DMG 构建、中文空格目录重定位、两次启动及正常/异常退出清理通过；冻结后台检查 95 个文件 |
| 三类本机候选 manifest / SHA-256 | wheel、镜像归档和 DMG 均生成后完整验证通过；本地脏工作区明确标记仅供验证 |
| 静态检查 | 247 个 Git 跟踪文件检查、Ruff E4/E7/E9/F、actionlint 1.7.12、Compose 配置与 Markdown 相对链接通过 |

本机日志和合成安装报告位于忽略目录 `output/p6-*.log`、`output/p6/installed-smoke.json`，候选文件位于 `output/p6/{web-python,linux-arm64,macos-arm64}`。本机未安装 PowerShell 的 9 项跳过不记为通过；P5 的完整三平台结果另见历史报告。

## 迁移与回退演练

`tests/test_release_recovery.py` 提供三个可重复的离线场景，命令为 `uv run --frozen python -m unittest discover -s tests -p 'test_release*.py' -v`。

1. Legacy v4 合成库导入 V2 team 空间，修改 V2 名称，验证其他空间无账号、重复导入不覆盖。停止 V2 后由原备份恢复 legacy 库，原名称/旧 schema/完整性均保留，源文件 SHA-256 未变。此项验证原库回退，不向旧版反向同步 V2 编辑，不调用旧上游接口。
2. 从实际 `0002_identity` revision 建库，保存加密合成凭证，将标签更新提交到尚未 checkpoint 的 WAL。新桌面模式在锁内创建升级前备份，在真实 `0003_devices` 的创建表步骤之后注入失败。旧 schema、会话列和事务均完整回滚；备份保留已提交 WAL 标签及原密钥可解密凭证。复制到新目录后移除注入故障，可成功重新升级；原失败目录也可重新升级，不泄漏目录锁。
3. 当前服务实例在更新前离线备份，然后模拟新版本的账号编辑。停止服务，原备份恢复到空目录，旧账号值和凭证可解密性恢复，备份中的登录会话被拒绝。

现有 `test_v2_upgrade.py` 验证 P1→当前、P4→当前升级与原会话/票据保留；`test_v2_web.py` 验证错误密钥、非空目录、进程占用时拒绝恢复。`test_v2_connected.py` 覆盖 API 主版本不兼容时拒绝继续请求。schema 反向 downgrade 不受支持；旧 schema 备份应由对应旧程序恢复。

产物专项覆盖篡改、丢失/额外文件、重复校验条目、路径越界、manifest 与文件集合不一致，以及 wheel 缺 Web、混入数据库/私钥材料。检查不输出匹配到的敏感内容。SHA-256 不代替发行签名，内容扫描也不声称识别任意编码的秘密。

## 剩余验收

1. 维护者选定许可证后增加 LICENSE 和一致的包/镜像许可元数据，更新贡献约定并重新构建验证。
2. 新 P6 候选 CI 结果待补录；历史 P5 平台通过记录不当作本次新工作流执行结果。
3. 正式发行需要维护者证书、实际 macOS 签名/公证、Windows 签名，以及干净用户/最低系统/真实 WebView 的验证。签名方案已准备，尚未执行或宣称通过。
4. P5 真实多设备会话实验独立保留，不以 P6 文档和构建工作完成为由开启远程切换或宣布完整 V2 Connected Desktop。
