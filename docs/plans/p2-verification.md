# P2 验证报告

日期：2026-09-10。状态：P2 已完成。分支：`feat/v2-p2`。

实现与测试提交 `815b8c46120e0944119689d0c31a9a9890d58d43` 通过 [三平台 CI](https://github.com/devilcoolyue/cursor-dashboard/actions/runs/34434865306)。后续收尾只更新文档。

## 交付

- `0002_identity` 增加实例管理员、会话、邀请、切换票据及审计；真实 P1 schema 升级保留原身份与空间。已有 P1 无密码 Owner 可用离线 `server-init` 设置首次管理员，补建个人空间。
- scrypt 密码、随机会话票据哈希、CSRF、精确 Host/Origin、登录限速、会话列表/撤销、密码变更与离线恢复。禁用用户和密码变更立即撤销会话；旧共享口令与管理 Cookie 不能访问 V2。
- 个人/团队空间、Owner/Admin/Member/Viewer、邀请加入、退出/移除、事务内 Owner 转移、view/use 授权和实时 capabilities。实例管理员不自动获得他人空间权限。
- `policy.authorize` 统一资源授权；列表、搜索、统计、标签计数、明细、刷新、账号修改/重新授权均验证空间和用户权限。慢请求在返回或提交前复查，快照提交和 use 校验在同一事务。
- 邀请、角色、授权、账号维护、会话与密码、票据签发/领取审计；成功审计与业务事务原子提交。记录固定动作和允许的变更信息，不保存明文凭证、密码或原票据。
- `SwitchService` 提供会话/空间/账号/代次/版本绑定、最多 5 分钟、原子单次领取的共用核心；切换执行结果另记审计。
- `cursor-api` 独立 `/api/v1`，要求 server 模式、认证初始化和固定 origin，持有 P1 数据目录锁；本地 `cursor-core` 保留维护/导入职责。

P2 不提供新版 Web 页面、容器、周期后台调度、Web 手工脚本、设备 PKCE 登录或远程桌面切换。票据测试使用 Web 会话并在可信核心层领取，不开放裸凭证浏览器 API；P3/P5 适配及上游设备会话验证继续按计划实施。

## 本地验证

macOS arm64，Python 3.12.13，依赖固定于 `uv.lock`。显式声明 Pydantic 2，避免独立安装时被旧版 Pydantic 满足 FastAPI 的宽版本依赖。

| 验证 | 结果 |
| --- | --- |
| P1 核心回归 | 27 项通过 |
| 新身份、权限、票据与竞争 | 19 项通过 |
| 新 HTTP 端到端 | 8 项通过 |
| P1 真实 schema 升级、CLI 和导入空间删除 | 3 项通过 |
| 完整回归 | 238 项：229 通过，9 项缺少 PowerShell 跳过，无失败，60.683 秒 |
| wheel 独立安装 | CLI 初始化/预检/重复导入/校验/升级；真实 HTTP 进程登录、列表、退出均通过 |
| Python 静态检查 | 相关模块通过 Ruff E4/E7/E9/F；git diff 空白检查通过 |

覆盖两用户和两空间、所有角色及授权级别、隐形账号的搜索/分页/标签/总数隔离、越权 Cookie 提交在回源前拒绝、实例管理员与空间权限分离、错误 CSRF/Host/Origin、请求体限制及错误脱敏。

并发测试覆盖：8 路领取仅成功一次；领取审计失败回滚；旧会话、过期票据、正常轮换、重新授权和删除使旧票据失效；Viewer 降级收回 use；恢复原授权不复活旧票据；慢续期签发与撤权竞争、慢明细/刷新与会话撤销竞争、排队刷新在回源前重验会话。

全部使用临时数据库、合成 Cookie/AT/RT 与模拟上游；完整旧版回归通过 `legacy-baseline.py` 注入临时存储，不读取真实账号库、密钥或本机 Cursor。日志在被忽略的 `output/p2/`。

## 三平台验证

`.github/workflows/p2-identity.yml` 的最终运行 [34434865306](https://github.com/devilcoolyue/cursor-dashboard/actions/runs/34434865306) 全部成功：

| 环境 | P1 核心 | 身份/权限/票据 | HTTP 与升级 | wheel + HTTP 进程 | 完整回归 |
| --- | --- | --- | --- | --- | --- |
| Linux / Python 3.10 | 27 项，4.995 秒 | 19 项，25.672 秒 | 11 项，13.225 秒 | 通过 | 本 job 不运行全量 |
| macOS / Python 3.12 | 27 项，4.715 秒 | 19 项，30.409 秒 | 11 项，15.871 秒 | 通过 | 238 项全部通过，无跳过，77.697 秒 |
| Windows / Python 3.12 | 27 项，9.030 秒 | 19 项，46.113 秒 | 11 项，23.423 秒 | 通过 | 本 job 不运行全量 |

各平台 wheel 流程另建 Python 3.12 环境；Linux 的 Python 3.10 覆盖是核心/身份/API/升级测试，不将 wheel 安装称为 Python 3.10 验证。macOS CI 覆盖本地缺少 PowerShell 而跳过的 9 项；无测试失败或平台跳过。完整日志保存于被忽略的 `output/p2/ci-final.log`。

## 实施依据

[ADR 0004](../adr/0004-p2-identity-and-authorization.md)、[API 初始化与运行](../v2-api-operations.md)、[核心迁移与恢复](../core-operations.md)、[总体计划](v2-architecture.md)。
