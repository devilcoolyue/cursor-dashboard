# Cursor Panel

管理个人与团队的 Cursor 账号、额度与授权。一套 Python 业务核心提供已认证 API、Vue Web 界面和远程 CLI；个人空间默认隔离，团队账号按 view/use 权限共享。

当前包版本仍为 `1.4.0`，V2 按 [实施计划](docs/plans/v2-architecture.md) 分阶段推进。P0–P2 已完成，P3 Web 与服务端交付的验证记录见 [P3 报告](docs/plans/p3-verification.md)。独立桌面产品和连接远端桌面属于 P4/P5，尚未交付。

## 启动 V2 Web

推荐使用 Docker Compose，完整步骤见 [Web 部署与使用](docs/v2-web-operations.md)。在仓库根目录执行：

```bash
cp deploy/v2/.env.example deploy/v2/.env
# 将 CURSOR_PANEL_DOMAIN 改成指向本服务器的域名。
docker compose --env-file deploy/v2/.env -f deploy/v2/compose.yaml build panel
docker compose --env-file deploy/v2/.env -f deploy/v2/compose.yaml run --rm maintenance \
  cursor-core --key-file /run/cursor-secrets/master.json keygen
docker compose --env-file deploy/v2/.env -f deploy/v2/compose.yaml run --rm maintenance \
  cursor-core server-init --login owner@example.com
docker compose --env-file deploy/v2/.env -f deploy/v2/compose.yaml up -d panel proxy
```

在终端隐藏输入初始化密码，随后访问自己的 HTTPS 域名登录。Caddy 提供 HTTPS，数据库和独立主密钥使用不同持久卷。服务端首版为单实例、单业务进程；维护时需先停服务，更新前备份数据库和匹配密钥。

源码运行需要 Python 3.10+、uv、Node 22.12+：`uv sync --locked`、`npm --prefix frontend ci`、`uv run --frozen python dev/build-web.py`，再按 [API 运行文档](docs/v2-api-operations.md) 初始化并启动 `cursor-api`。

## V2 当前能力

| 功能 | 行为 |
| --- | --- |
| 用户与空间 | 用户登录、退出、改密码与会话撤销；个人空间、团队邀请、固定角色及 Owner 转移 |
| 权限 | Member/Viewer 默认看不到团队账号；view 查看额度与明细，use 还可刷新和手工切换 |
| 账号维护 | Owner/Admin 添加、修改名称与标签、重新授权、删除；空间内去重，跨空间独立 |
| 额度与明细 | 最后成功快照、套餐与周期、综合/Cursor/Other Models 额度、Grok 周额度、按 tier 分组的模型用量 |
| 刷新 | 手工刷新与按需明细，统一节流与凭证续期；失败保留成功快照，显示更新时间与状态；尚无 V2 周期调度 |
| Web 手工切换 | 经过 use 授权的短期一次性票据，领取时复查当前权限，生成固定 macOS/Windows 脚本由用户执行 |
| 设置与审计 | 空间成员/授权管理、空间审计、实例用户启停及实例审计；实例管理员不自动获得他人空间权限 |
| 显示与交互 | 六种皮肤与独立明暗、移动布局、焦点恢复、减少动态效果；切换空间/用户取消旧请求 |
| 运行维护 | 加密凭证、进程锁、显式 schema 升级、旧版迁移、离线一致备份及新环境恢复 |

额度百分比沿用 Cursor 返回口径，美元上限仅作推算；数据来自非公开接口，不能视为官方计费或兼容性承诺。列表显示快照时间，不能视为实时数据。拥有 use 权限的人能领取账号凭证，撤权不能收回已复制的凭证。

## 命令行

```bash
cursor-remote --server https://panel.example.com --login owner@example.com workspaces
cursor-remote --server https://panel.example.com --login owner@example.com list
cursor-remote --server https://panel.example.com --login owner@example.com \
  detail --workspace 空间UUID --account 账号UUID
```

每次命令隐藏输入密码，会话只在内存保存，结束时撤销。`cursor-core` 是持有数据目录锁的离线运维入口，不能用其 Actor 参数替代远程用户登录。

## 模拟预览与检查

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --frozen python dev/preview-v2.py --port 18763
```

访问 `http://127.0.0.1:18763`。合成用户 `owner@example.test`、`member@example.test`、`viewer@example.test`，密码均为 `Preview password 42!`。预览创建临时数据库与密钥，不请求 Cursor；预览切换脚本在访问本机 Cursor 前停止。

```bash
uv run --frozen python -m unittest discover -s tests -v
npx --prefix frontend playwright install chromium
npm --prefix frontend run test:e2e
```

验证使用模拟网关和临时数据。前端 API 类型由 OpenAPI 生成并通过 CI 检查漂移；容器、wheel 和三平台检查见 [P3 报告](docs/plans/p3-verification.md)。

## Legacy 兼容入口

`cursor-panel` / `cursor-quota` 继续提供旧版共享面板与查询，不使用 V2 的登录、空间或数据库。旧 `PANEL_TOKEN`、独立管理员口令与访客开放规则不能访问 V2。旧版源码基线保存在远端 `legacy` 分支，详见 [旧版使用说明](docs/legacy-usage.md)、[旧版部署](docs/operations.md)和[维护文档](docs/maintenance.md)。

不要让旧版直接打开新版数据库。迁移、重复导入检查与回退说明见 [核心运维](docs/core-operations.md)。真实 Cookie、AT/RT、密钥、运行数据库和生成切换命令不应提交到仓库。

## 文档

- [Web 部署、备份恢复、远程 CLI](docs/v2-web-operations.md)
- [V2 认证与 API 约定](docs/v2-api-operations.md)
- [核心运行与旧版迁移](docs/core-operations.md)
- [架构与分阶段计划](docs/plans/v2-architecture.md)
- [P3 交付决策](docs/adr/0005-p3-web-delivery.md)
