# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

查询多个 Cursor 账号的订阅套餐和额度余量。两个入口共用同一套取数逻辑：命令行
(`cursor-quota`) 和 Web 面板 (`cursor-panel`)。数据来自 cursor.com 的**非公开内部
接口**，字段随时可能变。

## 命令

```bash
uv run cursor-panel                    # 本机 :8787，自动开浏览器
uv run cursor-panel --port 9000 --no-open
PANEL_TOKEN=xxx uv run cursor-panel --host 0.0.0.0 --no-open   # 部署

uv run cursor-quota                    # 命令行
uv run cursor-quota --json
uv run cursor-quota -c other.json
```

两个入口是 `pyproject.toml` 里的 `[project.scripts]`，`uv run` 会自动建 venv 并把本
项目 editable 装进去。

存储层测试使用标准库 `unittest`。改完用这几条自查：

```bash
uv run python -c "import cursor_dashboard.server, cursor_dashboard.cli"   # 导入即语法+循环依赖检查
uv run python -m unittest discover -s tests -v
# 页面内联 JS：抽出来交给 node
python3 -c "import re,pathlib;print(re.search(r'<script>(.*)</script>',pathlib.Path('cursor_dashboard/web/index.html').read_text(encoding='utf-8'),re.S).group(1))" > /tmp/page.js && node --check /tmp/page.js
```

**本机 curl 必须加 `--noproxy '*'`**——环境里设了 `http_proxy=127.0.0.1:7890`，
否则打本地服务会得到 502。

环境变量集中在 `config.py`：`PANEL_TOKEN`（非空则 `/api/*` 要 `X-Panel-Token`）、
`DATABASE_PATH`（SQLite 账号库）、`ACCOUNTS_PATH`（仅用于首次导入旧 JSON）、
`MAX_WORKERS`（通用线程池，默认 48）、`REQUEST_CONCURRENCY`（Cursor 出站并发，默认
3）、`REQUEST_MIN_INTERVAL`（出站最小间隔，默认 0.5 秒）、`REQUEST_RETRIES`/
`RETRY_BASE_DELAY`、`RATE_LIMIT_RETRIES`/`RATE_LIMIT_BASE_DELAY`、`REFRESH_ENABLED`/
`REFRESH_INTERVAL`（默认 900 秒）/`REFRESH_MIN_GAP`/`REFRESH_IDLE_AFTER`/
`REFRESH_IDLE_FACTOR`/`REFRESH_MAX_BACKOFF`、`MANUAL_COOLDOWN`/`MANUAL_BURST`。
相对路径都基于启动时的工作目录。

## 架构

```
cursor_dashboard/
├── client.py     5 个接口的封装、AuthExpired、RateLimited、ENDPOINTS、fetch_one
├── usage.py      assemble/collect —— 把 5 份原始返回拼成前后端共用结构，纯计算
├── snapshot.py   每个账号最后已知状态 + single-flight 锁 + 给前端的 view()
├── scheduler.py  后台错峰刷新循环，自适应退避
├── store.py      SQLite 事务读写、snapshots 表、旧 JSON 自动迁移、account_id
├── config.py     环境变量
├── cli.py        命令行入口（render/bar/main）
├── server.py     FastAPI，只做编排
└── web/index.html
```

**`client` + `usage` 是取数层，`server` 只做编排**——CLI 和面板共用同一份接口定义和
组装逻辑，改字段只需要改一处。`collect()` 是串行版，CLI 专用；服务端不走它。

**页面不回源，只读快照。** 所有对 cursor.com 的常规访问都由 `scheduler.Scheduler`
在后台发出，一次一个账号。**不要给页面加回「刷新全部」这类批量强制回源的入口**——
那正是触发限流的洪峰源头（42 账号 × 5 接口 = 210 个请求几秒内打完）。

出站有两道闸，缺一不可：
- `fetch_cursor()` 里的 `_pace()` 按 `REQUEST_MIN_INTERVAL` 给每个请求分配时槽。
  **限并发不等于限速率**，3 个并发在接口够快时照样能打出几十 QPS，而边缘防护看的
  就是速率。锁内只算时槽、锁外再 sleep。
- 全局 semaphore（`REQUEST_CONCURRENCY`）限连接数，防止连接洪峰。

`refresh_account()` 用 `asyncio.gather(..., return_exceptions=True)` 把 **N 账号 ×
5 接口打平成一个任务集**，共用 lifespan 里装的单个 `ThreadPoolExecutor`。
**不要改成嵌套线程池**（每账号一个池、池内再并发）——线程数会乘起来。默认 executor
只有 `min(32, cpu+4)`，所以显式设了 `MAX_WORKERS`。用 `return_exceptions` 是为了让
5 个接口都跑完再由 `_classify()` 判定，否则先抛出的那个说了算，混合错误容易误判。

`scheduler` 每轮挑 `attempted_at` 最旧的账号（`Scheduler._pick`），间隔是
`REFRESH_INTERVAL / 账号数`。撞限流 → 间隔翻倍（上限 `REFRESH_MAX_BACKOFF`）；
连续 10 次成功 → 收紧一档。`REFRESH_IDLE_AFTER` 没人访问 `/api/*` 就降速，
中间件里的 `touch()` 会立刻叫醒它。**别把 `REFRESH_INTERVAL` 调到几分钟以下**：
错开只降瞬时密度，周期越短长期总量越大，同一个 IP 上 24 小时不停打，反而比手动
刷新更容易招来按 IP 的封禁。额度是月度数据，不需要秒级新鲜度。

快照（`snapshot.py`）按账号 ident（`store.account_id`：email，回退 label）存，
带 single-flight，同时落 SQLite `snapshots` 表（只存 cookie 哈希，不存明文）：
- **失败绝不覆盖成功**：`record_failure` 只写 error，`data` 原样保留。这是误报的
  根治办法——过去 403 一来就把整卡覆盖成失效，好数据被擦掉，只能靠重启清缓存恢复。
  改动时不要退回「用失败结果覆盖整条缓存」的写法。
- `failures` 按「同一种失败连续出现几次」计数，kind 变了就归零重数；认证失效要连续
  确认 `EXPIRE_CONFIRMATIONS` 次才标红，从没成功过的账号除外（第一次就标红）。
- cookie 变更让快照自动作废（fingerprint 不匹配）；删号会 `snapshot.drop`。

接口：`GET /api/config`（不鉴权，页面据此决定要不要问口令，并拿到后台刷新周期）、
`GET /api/status`（调度器状态，排查限流用）、
`GET /api/account-index?department=...`（只要索引和部门人数，不访问 Cursor）、
`GET /api/accounts?department=...`（整组卡片，读快照，一个请求拿完）、
`GET /api/accounts/{id}`（单卡快照）、
`POST /api/accounts`（新增/续期，同一入口，成功后直接写快照）、
`PATCH /api/accounts/{id}/department`（只改部门，不碰 cookie）、
`POST /api/accounts/{id}/refresh`（单卡刷新，走冷却 + 令牌桶）、
`DELETE /api/accounts/{id}`。
`store.AccountsError` 由 `server.handle_accounts_error` 转成 500 + `detail`，
前端读的就是 `detail` 字段。

`web/index.html` 是**单文件、无 CDN 依赖**的页面。`load()` 一个请求拿回整组卡片，
每 60 秒静默重取一次（`POLL_INTERVAL`），标签页切回来也立刻重取；`silent` 模式不
显示骨架、失败也不清空页面。`loadGeneration` + `AbortController` 防止切组后旧响应
污染新视图。单卡刷新只替换 `accounts` 里的一项再整体 `render()`，`_loading` 让该卡
渲染成带姓名/部门/邮箱的骨架。渲染一律走 `esc()` 转义。每张卡的 `_order` 保留添加
顺序；排序只对渲染副本操作。卡片有四种状态：正常、`stale`（有数据但最近一次刷新
失败，照常显示数据 + 黄字说明）、`expired`（连续确认后才变红）、`pending`（后台还
没轮到，骨架 + 说明）。`stale` 文案刻意不说「会话失效」——被限流时说失效会把人骗去
重粘 cookie，而那次粘贴同样会被挡住。每张卡显示 `ok_at`（最后统计时间，后台是错开
刷的所以每张都不同），页头显示后台轮一遍要多久。部门和排序选择分别写入
`localStorage.selectedDepartment`、`localStorage.accountSort`，服务端按部门过滤账号；
额度刷新时间由前端把 `reset_at` 转成浏览器本地时区，不使用后端向下取整的 `days_left`；
不足 48 小时时显示到整小时。
新增账号默认带入当前部门，「全部」使用前端 sentinel，不写入数据库。新增和调整分组
使用原生 `select`：空值代表未分组，已有部门动态生成，选择「新建部门…」后才显示
自由文本输入框；不要改回浏览器表现不一致的 `datalist`。

## 必须知道的坑

**cookie 失效不返回 401**，而是 307 跳 WorkOS 登录页；跟着跳转只会拿到一个无关的
404。`CursorClient._call` 因此用 `allow_redirects=False`，并把 401 或跳向
auth/login/workos 的 3xx 判为 `AuthExpired`。**不要把它改回默认跟跳转。**

**403 不一定是失效。** Cursor / Vercel 被打急了会回 **403 + HTML 安全拦截页**，
cookie 本身好好的。旧代码把所有 403 都当 `AuthExpired`，结果批量刷新触发限流时整屏
卡片变红，用户重新粘贴 cookie 也照样红。现在按 Content-Type 分：403 + JSON 才是
失效，403 + HTML 以及 429/503 判为 `RateLimited`，可退避重试（`RATE_LIMIT_*`，
尊重 `Retry-After`）。`_classify()` 里**限流优先于失效**，混合错误按限流处理。
`grok_status` 吞掉了普通异常，但 `AuthExpired` / `RateLimited` 必须冒泡，否则调度器
不知道该退避。**不要把这两类错误合并，也不要把 403 一律当失效。**

**`fetch_one` 每次新建 `Session` 是刻意的**——`requests.Session` 跨线程共享不安全
（响应会改写 cookie jar），而这 5 个请求本来就要并发发出。Session 用完必须关闭。
连接错误、超时、5xx 可短退避重试，`RateLimited` 走更长的退避；`AuthExpired` 和普通 4xx 不能重试。

**额度百分比是官方给的**：`autoPercentUsed` / `apiPercentUsed` / `totalPercentUsed`
直接来自 `period_usage`，代码只做 `100 - x`。**不要拿美元金额去算百分比**——
`totalSpend` 里含 `bonusSpend`（Cursor 赠送额度），和百分比不是一个尺度，
$193.03 / $20.00 与"剩 61.0%"并存是正常的。

**`accounts.db` 存的是等同登录态的会话 token**，权限 0600，已 gitignore。首次启动会
从旧 `accounts.json` 导入一次；`snapshots` 表只存 cookie 的 sha256 前 16 位，不存
明文，也不含任何凭据。调试时不要复制真实数据库或输出 cookie；存储和快照测试只用
临时目录中的假数据，端到端验证另起一个 `DATABASE_PATH` 指向临时库的实例。

## 两条已废弃的路线，不要重新提议

- **Playwright 拉起浏览器让用户登录**：Cursor 登录页的人机验证（Cloudflare
  Turnstile）会识别自动化浏览器并拒绝，实测报 `Can't verify the user is human`；
  且服务器上没有桌面环境。绕过检测不做。
- **Chrome 扩展读 cookie 回填**：技术上可行且验证通过，但要求每个使用者装扩展，
  不适合服务端部署，已按用户要求删除。

现在只保留手工粘贴 cookie 这一条路。这不是偷懒：`WorkosCursorSessionToken` 是
httpOnly cookie，网页 JS 读不到（跨域读不到，httpOnly 连 cursor.com 自己的页面 JS
也读不到，iframe 嵌 cursor.com 会被 `frame-ancestors` 挡掉）。
