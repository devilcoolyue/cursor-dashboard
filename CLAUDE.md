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
`MAX_WORKERS`（默认 48）、`CACHE_TTL`（默认 60 秒）。相对路径都基于启动时的工作目录。

## 架构

```
cursor_dashboard/
├── client.py    5 个接口的封装、AuthExpired、ENDPOINTS、fetch_one
├── usage.py     assemble/collect —— 把 5 份原始返回拼成前后端共用结构，纯计算
├── cache.py     按账号缓存 + single-flight 锁
├── store.py     SQLite 事务读写、旧 JSON 自动迁移、account_id
├── config.py    环境变量
├── cli.py       命令行入口（render/bar/main）
├── server.py    FastAPI，只做编排
└── web/index.html
```

**`client` + `usage` 是取数层，`server` 只做编排**——CLI 和面板共用同一份接口定义和
组装逻辑，改字段只需要改一处。`collect()` 是串行版，CLI 专用；服务端不走它。

服务端 `probe()` 用 `asyncio.gather` 把 **N 账号 × 5 接口打平成一个任务集**，共用
lifespan 里装的单个 `ThreadPoolExecutor`。**不要改成嵌套线程池**（每账号一个池、
池内再并发）——线程数会乘起来。默认 executor 只有 `min(32, cpu+4)`，所以显式设了
`MAX_WORKERS`。

缓存按账号 ident（`store.account_id`：email，回退 label）存，带 single-flight：
- `cache.get` 看 TTL；`cache.since(ident, cookie, t0)` 不看 TTL，只看"是不是 t0
  之后写入的"。后者是为了让**并发强制刷新**复用先到者的结果——若只用前者，
  `force=1` 会绕过缓存检查，那把锁就把并发请求串成了排队，比不加锁更慢。
- cookie 变更（`api_save`）和删号都会 `cache.drop`。

接口：`GET /api/config`（不鉴权，页面据此决定要不要问口令）、
`GET /api/accounts?force=1`、`POST /api/accounts`（新增/续期，同一入口）、
`POST /api/accounts/{id}/refresh`（单卡刷新）、`DELETE /api/accounts/{id}`。
`store.AccountsError` 由 `server.handle_accounts_error` 转成 500 + `detail`，
前端读的就是 `detail` 字段。

`web/index.html` 是**单文件、无 CDN 依赖**的页面。前端持有 `accounts` 数组，单卡刷新
只替换其中一项再整体 `render()`；`_loading` 标记让该卡渲染成骨架。渲染一律走
`esc()` 转义。

## 必须知道的坑

**cookie 失效不返回 401**，而是 307 跳 WorkOS 登录页；跟着跳转只会拿到一个无关的
404。`CursorClient._call` 因此用 `allow_redirects=False`，并把 401/403 或跳向
auth/login/workos 的 3xx 判为 `AuthExpired`。**不要把它改回默认跟跳转。**

**`fetch_one` 每次新建 `Session` 是刻意的**——`requests.Session` 跨线程共享不安全
（响应会改写 cookie jar），而这 5 个请求本来就要并发发出。

**额度百分比是官方给的**：`autoPercentUsed` / `apiPercentUsed` / `totalPercentUsed`
直接来自 `period_usage`，代码只做 `100 - x`。**不要拿美元金额去算百分比**——
`totalSpend` 里含 `bonusSpend`（Cursor 赠送额度），和百分比不是一个尺度，
$193.03 / $20.00 与"剩 61.0%"并存是正常的。

**`accounts.db` 存的是等同登录态的会话 token**，权限 0600，已 gitignore。首次启动会
从旧 `accounts.json` 导入一次。调试时不要复制真实数据库或输出 cookie；存储测试只用
临时目录中的假数据。

## 两条已废弃的路线，不要重新提议

- **Playwright 拉起浏览器让用户登录**：Cursor 登录页的人机验证（Cloudflare
  Turnstile）会识别自动化浏览器并拒绝，实测报 `Can't verify the user is human`；
  且服务器上没有桌面环境。绕过检测不做。
- **Chrome 扩展读 cookie 回填**：技术上可行且验证通过，但要求每个使用者装扩展，
  不适合服务端部署，已按用户要求删除。

现在只保留手工粘贴 cookie 这一条路。这不是偷懒：`WorkosCursorSessionToken` 是
httpOnly cookie，网页 JS 读不到（跨域读不到，httpOnly 连 cursor.com 自己的页面 JS
也读不到，iframe 嵌 cursor.com 会被 `frame-ancestors` 挡掉）。
