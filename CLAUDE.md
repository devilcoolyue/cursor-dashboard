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
`REFRESH_IDLE_FACTOR`/`REFRESH_MAX_BACKOFF`、`MANUAL_COOLDOWN`/`MANUAL_BURST`、
`DETAIL_TTL`（按模型明细的缓存秒数，默认 60）。
相对路径都基于启动时的工作目录。

## 架构

```
cursor_dashboard/
├── client.py     接口封装、AuthExpired、RateLimited、ENDPOINTS、fetch_one
├── usage.py      assemble/collect/pool_limits/assemble_detail，纯计算
├── pools.py      同套餐额度池登记表：用满的账号自己解不出上限，从同套餐抄
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
那正是触发限流的洪峰源头（42 账号 × 4 接口 = 168 个请求几秒内打完）。

出站有两道闸，缺一不可：
- `fetch_cursor()` 里的 `_pace()` 按 `REQUEST_MIN_INTERVAL` 给每个请求分配时槽。
  **限并发不等于限速率**，3 个并发在接口够快时照样能打出几十 QPS，而边缘防护看的
  就是速率。锁内只算时槽、锁外再 sleep。
- 全局 semaphore（`REQUEST_CONCURRENCY`）限连接数，防止连接洪峰。

`refresh_account()` 用 `asyncio.gather(..., return_exceptions=True)` 把 **N 账号 ×
4 接口打平成一个任务集**，共用 lifespan 里装的单个 `ThreadPoolExecutor`。
**不要改成嵌套线程池**（每账号一个池、池内再并发）——线程数会乘起来。默认 executor
只有 `min(32, cpu+4)`，所以显式设了 `MAX_WORKERS`。用 `return_exceptions` 是为了让
4 个接口都跑完再由 `_classify()` 判定，否则先抛出的那个说了算，混合错误容易误判。

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
`GET /api/accounts/{id}/usage-detail`（本周期按模型明细，**唯一一个页面点了才回源的读接口**）、
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
「本周期消费」的分母是 `quota.overall.limit_usd`，消费超过上限时金额标红
（`overspent`，容差 0.005）——用满的账号实测停在 $495.3 上下，"剩 0.0%" 和
"$495.32 / $495" 说的是同一件事。
额度刷新时间由前端把 `reset_at` 转成浏览器本地时区，不使用后端向下取整的 `days_left`；
不足 48 小时时显示到整小时。
三条额度都是按钮：hover 出提示，点开弹窗看本周期按模型的 token 和花费（`openDetail`
→ `/api/accounts/{id}/usage-detail`）。点「综合」看两组 + 合计，点分类只看那一组；
`detailRequest` 计数防止旧响应盖掉新弹窗。名字后面那个 `$450` 读的是 `quota.*.limit_usd`，
为 `null` 时整个不显示——**不要拿 `plan.included_usd`（$20）顶替**，那是订阅价不是池子。
升级前存的旧快照没有这个字段，`== null` 判断已经覆盖 `undefined`。
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
**不要把这两类错误合并，也不要把 403 一律当失效**——调度器靠 `RateLimited` 判断
该退避还是该报失效，混进 `AuthExpired` 就会把限流当成 cookie 过期。

**`fetch_one` 每次新建 `Session` 是刻意的**——`requests.Session` 跨线程共享不安全
（响应会改写 cookie jar），而这 4 个请求本来就要并发发出。Session 用完必须关闭。
连接错误、超时、5xx 可短退避重试，`RateLimited` 走更长的退避；`AuthExpired` 和普通 4xx 不能重试。

**额度百分比是官方给的**：`autoPercentUsed` / `apiPercentUsed` / `totalPercentUsed`
直接来自 `period_usage`，代码只做 `100 - x`。**不要拿美元金额去算百分比**——
`totalSpend` 里含 `bonusSpend`（Cursor 赠送额度），和 `includedAmountCents`（$20）
不是一个尺度。卡片上「本周期消费」的分母因此用的是反解出来的综合池（见下条），
$231.24 / $495 正好对上「综合 剩 53.3%」；$20 挪进了悬停提示。
**别把分母改回 `plan.included_usd`**——那会显示成 $231.24 / $20.00，看着像超支十倍。

**每条额度的美元上限是反解出来的，不是接口字段，也不许写死。** `usage.pool_limits`
利用「`totalPercentUsed` 是两个池按容量加权的平均数」这一点解方程：
总池 `T = totalSpend / totalPct`，池之比 `A/B = (apiPct − totalPct) / (totalPct − autoPct)`。
某 Pro 账号在两个不同时刻都解出 A=$450、B=$45、T=$495，且和按 tier 分组的明细金额
逐分吻合。三个百分比贴太近时方程退化（`POOL_MIN_GAP`），这时只给总池、分池留 `None`，
前端不显示——**宁可不显示，也不要显示一个猜的数**。
**百分比会被服务端截顶在 100**，一个真用到 110% 的账号照样报 `100.00`，代进方程
就是假数据：42 个账号的实测里，张琛 `auto=98.03 / api=100.00` 解出 $402 / $93
（真值 450 / 45），全用满的账号解出的"总池"其实是消费额、还会随消费一直变大。
所以 `PCT_CEILING` 一票否决：某一档触顶只丢分池（总池仍解得对），综合触顶就全丢。
**别为了"让每张卡都显示金额"把这道闸去掉**——要让用满的卡片也显示，正确做法是
`pools.py`：池子是套餐的常量，从**同套餐里解得出的账号**那儿抄一份（`snapshot.view`
里 `pools.fill`），抄来的标 `limit_inferred`。这跟猜不一样，表里每个数都是某个真实
账号当场解出来的，套餐涨价或换 Pro+ 会自动跟着变；一个账号都没解出来过就老实留空。
**不要改成在代码里写死 450/45/495。** 按 (套餐, 账号) 存最近一次观测，所以表不会
随刷新次数增长，取值时逐档取中位数，个别脏数据盖不过大多数。**这跟「不要拿美元金额去算百分比」
不冲突**：那条禁的是拿 `totalSpend` 除 `includedAmountCents`（$20），两者不是一个尺度；
这里是反过来用官方百分比标定池子大小，百分比仍是唯一事实来源。

**按模型明细走 `get-aggregated-usage-events`，且只在点开卡片时才拉。**
`{"teamId":0,"userId":0,"startDate":<ms>,"endDate":<ms>}`，窗口传账单周期起点就是「本
周期」（网页 Usage 页面默认按天，所以那里看不到周期口径）。返回的 `totalCostCents`
精确等于 `planUsage.totalSpend`。**它刻意不在 `ENDPOINTS` 里**——加进去后台轮询的出站
量就凭空 +25%，而按 IP 限流是本项目最大的风险；当初摘掉 Grok Bot 接口就是为了 -20%。
**不要给页面加「批量拉明细」的入口**，理由和不给「刷新全部」是同一条。

分类**用返回里的 `tier` 字段**（2 = Cursor Models，1 = Other Models），
**不要改成按模型名匹配 `period_usage.autoBucketModels`**：那个列表实测是滞后的
（只到 `cursor-grok-4.5`，没有正在跑的 4.6），按它归类会把新模型全丢进 Other Models。

**`period_usage.displayMessage` 也是金额口径，页面不显示它。** `includedSpend`
撞到 `limit`（$20）后它就固定返回 "You've hit your usage limit"，哪怕
`totalPercentUsed` 只有 45%——同一份返回里的 `autoModelSelectedDisplayMessage`
说的还是"用了 45%"。挂在剩 92% 的卡片下面纯属吓人，已从 `card()` 里去掉；
`usage.assemble` 仍然透传 `notice` 字段（CLI 还在用），**不要因为字段还在就把它
渲染回卡片**。

**Grok Bot 的额度整条都不要了**，`get-sand-usage-status` 已从 `ENDPOINTS` 摘掉，
每账号从 5 个接口降到 4 个（出站量 -20%，这是本项目最大风险——按 IP 限流——的直接
缓解）。Grok Bot 是 x.ai 的独立桌面/iOS App（用 Cursor 账号登录的常驻云端 agent），
跟在编辑器里写代码是两回事，没人装这个 App 就永远是 100%。**别为了"顺手多显示一点"
把这个接口加回来。**

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
