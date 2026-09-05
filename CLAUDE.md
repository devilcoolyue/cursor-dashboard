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
node --check cursor_dashboard/web/js/app.js                               # 页面脚本
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
└── web/
    ├── index.html          结构 + 皮肤引导脚本，挂 /static 下的三个样式表
    ├── css/tokens.css      主题令牌表（皮肤 × 明暗，四套取值）
    ├── css/base.css        骨架样式，只准写 var()
    ├── css/ui.css          公共下拉菜单、弹窗样式，沿用主题令牌
    ├── css/skins/glass.css 液态玻璃皮肤
    ├── js/ui.js            PanelUI 公共下拉菜单、弹窗插件
    └── js/app.js           页面业务逻辑
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
`GET /api/accounts/{id}/usage-detail`（本周期按模型明细，**唯一一个页面点了才回源的读接口**）、
`POST /api/accounts`（新增/续期，同一入口，成功后直接写快照）、
`PATCH /api/accounts/{id}/department`（只改部门，不碰 cookie）、
`POST /api/accounts/{id}/refresh`（单卡刷新，走冷却 + 令牌桶）、
`DELETE /api/accounts/{id}`。
`store.AccountsError` 由 `server.handle_accounts_error` 转成 500 + `detail`，
前端读的就是 `detail` 字段。

页面是**无构建、无 CDN 依赖**的静态文件，整个 `web/` 目录由 `server.py` 挂在
`/static` 下（`app.mount`，注意不能挂在 `/` 上，会把 `/api/*` 一起吃掉）。
改完刷新浏览器就生效，没有任何打包步骤。`load()` 一个请求拿回整组卡片，
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
不足 48 小时时显示到整小时（`remainingParts`）。

**页面风格由 `SKINS` 声明表 + 三层 CSS 决定**，地位跟下面的 `CARD_OPTIONS` 平级。
风格和明暗是 `<html>` 上**两个正交的属性**：`data-skin`（classic / glass）×
`data-theme`（light / dark，"跟随系统"由 JS 解析成其中之一）。
**不要合并成"经典深色/玻璃浅色"这样的四选一**——每加一套皮肤选项就翻倍，
而且"跟随系统"没法落在任何一个上。

三层 CSS 的分工是死规矩：`tokens.css` 是所有颜色、圆角、阴影的唯一出处，四套组合
各写一份**完整**取值（只补差集的话，没补到的令牌会落回 `:root` 兜底的经典深色，
玻璃浅色下就冒出几个深色残留）；`base.css` 只准写 `var()`，一个颜色字面量都不许
有，换皮肤才只是换那张表；`skins/*.css` 只写令牌表达不了的形态差异，且
**每条选择器都必须挂在 `html[data-skin="…"]` 下**，漏了前缀就会渗进别的皮肤。
加一套皮肤 = `SKINS` 里多一条 + 一个 `css/skins/*.css` + `index.html` 里多一行
`<link>`，别处都不用碰。皮肤 CSS 一次全部加载，不按需插 `<link>`——省下那点流量
不够抵消切换时重下样式表的白闪。

`index.html` 头部那段内联引导脚本负责防闪烁：它必须内联、必须排在样式表之前，
把 localStorage 里的皮肤和明暗尽早打到 `<html>` 上，否则浏览器会先按兜底画一帧。
它刻意不做白名单校验（校验表在 `SKINS`，抄一份过去就成了两处事实来源），脏值会
落回兜底、由 `app.js` 纠正；里面的 `'classic'` 是全页唯一一处必须和
`DEFAULT_SKIN` 保持一致的重复，改默认皮肤要两处一起改。

玻璃皮肤有三个已经踩过的坑：**只有固定不动的层（侧栏、顶栏、弹窗、气泡）用
`backdrop-filter`，卡片一律不用**——卡片背后只有 `--app-canvas` 那层平滑渐变，
模糊低频信号的产出和原图几乎一样，白烧 GPU；而 42 张卡各挂一个，滚动时每帧都要
重采样，这是玻璃风掉帧的头号原因。卡片的玻璃感改用半透明底 + 上缘高光 + 柔外阴影
堆出来。**卡片 hover 不要写 `transform`**：它会让 `.card` 变成层叠上下文，额度行
那个 `z-index: 100` 的 tooltip 就只在卡片内部有效，被邻卡压住——跟"不要给 `.card`
加 `overflow: hidden`"是同一个坑，而且只在鼠标底下那张卡上发作，更难发现。
**`--app-canvas` 的色斑 alpha 别再往下调**：玻璃是"透出背后的东西"才成立的，
背景压太暗就没东西可透，整页会退回一块灰底加白框。

**卡片上哪些东西显示，由 `CARD_OPTIONS` 这一张声明表说了算**（角标、燃尽水印、周期环、
套餐徽章、部门、邮箱、额度上限，以及 meta 里的五行）。表是唯一事实来源：`card()` /
`bar()` / `identityMeta()` 按它渲染，侧边栏「卡片显示项」面板也按它生成勾选框，
加开关只要在表里多写一条。勾选结果存 `localStorage.panelCardPrefs`，读回时用
`{ ...OPTION_DEFAULTS, ...readPrefs() }` 合并——**不要直接拿存量当配置**，那样以后
新增的开关在老用户那儿会因为缺字段而消失；`readPrefs` 只认表里还有的键和布尔值，
删掉的开关和手改坏的值都进不了渲染。三个别踩的点：模板串里必须写
`cond ? html : ''`，**写 `cond && html` 条件为假时进页面的是字符串 "false"**；
meta 五行全关时连 `.meta` 容器一起省掉，否则会剩一条 `border-top` 的横线；
关掉「Grok Bot 周额度」只是不渲染那一行，**后台照常请求 `get-sand-usage-status`**，
想减出站量得动 `REFRESH_INTERVAL`，不是动这个开关。燃尽水印的开关不管卡片底色
（`.card.exhausted` 的暗红底是状态识别，去掉燃尽卡就跟正常卡长得一样了）。

卡片左上角的**角标**（`ribbon` / `quotaTier`）按综合剩余额度分四档：>30% 充足绿、
10~30% 偏紧黄、>0~10% 告急红、0% 燃尽暗红。**阈值必须跟 `color()` 同源**，两处对不
上的话同一张卡会自相矛盾（角标说充足、额度条却是黄的）。裁剪由 `.ribbon` 自己的
`overflow: hidden` 完成——**不要图省事给 `.card` 加 `overflow: hidden`**，那会把额度行
向上弹出的 tooltip 一起裁掉。

形状是 `::before` 的**实心直角三角**（`clip-path`），不是斜带——斜带两侧留出来的那点
卡片背景显脏，还不如把角填满。它是背景装饰：`z-index: 0`，`.card-head` 用 `z-index: 1`
压在上面。**不要靠给 `.card-head` 加 `padding-top` 来给它腾地方**——试过，整排卡片会
一起变高，很难看。两个尺寸不能随便动：斜边落在 `x+y = 38`，必须停在名字第一个字的
字形左上角（`x+y ≈ 42`）之前，越过去名字就糊在彩色三角上；文字中心的
`cx − cy` 必须是 0，否则文字偏向三角形的一头而不是正中。

卡片右上角的**周期环**（`cycleRing`）：环走过的比例 = `cycle.start` → `reset_at` 之间
已经过去的比例，中心写还剩多久。`cycle.start` 缺失（升级前的旧快照没这个字段）时只报
剩余时间、不画弧——猜一个 30 天周期画出来的弧是假的。那块位置平时空着（操作按钮只在
hover 时出现），所以 `.card:hover` 时环淡出、按钮淡入，两者共用同一条 `:hover` 规则。
**环必须保持 `pointer-events: none`**：它压在删除/刷新按钮上面，能接鼠标就会挡住点击。
窄屏（≤620px）按钮是常驻的，环因此挪到按钮左边并排，也不再淡出。
三条额度都是按钮：hover 出提示，点开弹窗看本周期按模型的 token 和花费（`openDetail`
→ `/api/accounts/{id}/usage-detail`）。点「综合」看两组 + 合计，点分类只看那一组；
`detailRequest` 计数防止旧响应盖掉新弹窗。
弹窗的三个坑都踩过一遍，别改回去：`dialog` 必须 `position: fixed`（写 `relative` 会盖
掉 UA 给 `dialog:modal` 的 fixed，弹窗掉回文档流末尾，`showModal()` 移焦点时整页跟着
跳）；`display: flex` 只能写在 `dialog.wide[open]` 上（写在 `dialog.wide` 上会盖掉 UA
的 `dialog:not([open]) { display: none }`，关掉之后弹窗还赖在页面上）；背景锁滚动靠
`body.modal-open { overflow: hidden }` + 补滚动条宽度，解锁盯的是 `dialog` 的 `open`
属性（`MutationObserver`）而不是 `close` 事件——实测 `close` 事件并不总会派发。
名字后面那个 `$450` 读的是 `quota.*.limit_usd`，
为 `null` 时整个不显示——**不要拿 `plan.included_usd`（$20）顶替**，那是订阅价不是池子。
升级前存的旧快照没有这个字段，`== null` 判断已经覆盖 `undefined`。
新增账号默认带入当前部门，「全部」使用前端 sentinel，不写入数据库。新增和调整分组
使用 `PanelUI` 增强的 `select`：原生元素保存值并派发 `change`，自定义菜单负责显示，
空值代表未分组，已有部门动态生成，选择「新建部门…」后才显示自由文本输入框。
直接修改 `.value` 后调用 `PanelUI.select.refresh()`，聚焦使用 `PanelUI.select.focus()`。
公共组件逻辑在 `web/js/ui.js`、样式在 `web/css/ui.css`，后者加载在 base 和 skins 之间。
弹窗统一走 `PanelUI.open/close`，确认和通知使用 `PanelUI.confirm/alert`，不使用浏览器
`confirm/alert`。确认接口可接收异步 `onConfirm`，提交时禁用关闭和重复提交，抛错显示在
弹窗内；用户文本使用 `textContent`。保留 native dialog 的顶层、焦点约束和 open 属性观察。

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
量就凭空 +20%，而按 IP 限流是本项目最大的风险。
**不要给页面加「批量拉明细」的入口**，理由和不给「刷新全部」是同一条。

每行只有 `modelIntent` / `inputTokens` / `outputTokens` / `cacheWriteTokens` /
`cacheReadTokens` / `totalCents` / `tier` 七个字段，**没有任何单价**，金额是 Cursor
算好的。缓存写和缓存读是分开的两个字段（为 0 时整个字段省略），页面也分两列显示——
两者单价差一个量级，加起来会把花费的大头藏掉（实测某账号 206M tokens 里 185M 是缓存
读，单这一项就占了某个模型 $133.60 里的 $99.23）。Cursor 自家模型（tier 2）缓存写恒
为 0，第三方模型才有，跟官方价目表里 Cursor Models 的 Cache write 一栏是 `-` 对得上。
**不要把价目表内嵌进代码去算成本**：自己乘一遍只会引入偏差（实测按官方单价反推，非
fast 档分毫不差，fast 档差 0.8~2.3%），还得跟着供应商调价维护。弹窗底部挂官方价目表
的链接就够了。

分类**用返回里的 `tier` 字段**（2 = Cursor Models，1 = Other Models），
**不要改成按模型名匹配 `period_usage.autoBucketModels`**：那个列表实测是滞后的
（只到 `cursor-grok-4.5`，没有正在跑的 4.6），按它归类会把新模型全丢进 Other Models。

**`period_usage.displayMessage` 也是金额口径，页面不显示它。** `includedSpend`
撞到 `limit`（$20）后它就固定返回 "You've hit your usage limit"，哪怕
`totalPercentUsed` 只有 45%——同一份返回里的 `autoModelSelectedDisplayMessage`
说的还是"用了 45%"。挂在剩 92% 的卡片下面纯属吓人，已从 `card()` 里去掉；
`usage.assemble` 仍然透传 `notice` 字段（CLI 还在用），**不要因为字段还在就把它
渲染回卡片**。

**Grok Bot 的周额度在 `ENDPOINTS` 里**（`get-sand-usage-status`），每账号 5 个接口。
它一度被摘掉过（当时是为了给按 IP 限流减 20% 的出站量），2026-09-03 按用户要求加了
回来——**这是一个明确的取舍，不要再自作主张摘掉它**；真要减出站量，先动
`REFRESH_INTERVAL`，那条杠杆比少打一个接口大得多。
Grok Bot 是 x.ai 的独立桌面/iOS App（用 Cursor 账号登录的常驻云端 agent），跟在编辑器
里写代码是两回事，没装这个 App 的账号会一直显示 100%，卡片上的悬停提示已经写明。
`client.grok_status()` 吞普通异常返回 `{}`（这条额度可有可无，不该把整个账号拖成失败），
但 **`AuthExpired` / `RateLimited` 必须冒泡**——调度器靠这两类异常判断该退避还是该报
失效，吞掉就等于对限流视而不见。接口只给 `currentPeriodStart`，重置时间是 `+7 天`
算出来的；返回空时 `grok_weekly` 整条为 `None`，前端不渲染这一行。

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
