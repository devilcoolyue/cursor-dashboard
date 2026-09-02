# Cursor 额度面板

按部门查看多个 Cursor 账号的订阅套餐、Auto/高级模型剩余额度、额度刷新时间。
Web 面板和命令行共用同一份账号库和同一套取数逻辑。
服务端不碰浏览器，可跑在无桌面的服务器上。

## 启动

```bash
uv run cursor-panel                 # 本机 :8787，自动开浏览器
uv run cursor-quota                 # 命令行输出
```

`uv` 会照着 `pyproject.toml` 自动建虚拟环境、装依赖（fastapi / uvicorn / requests）。

部署到服务器：

```bash
export PANEL_TOKEN=$(openssl rand -hex 16)   # 必设，见下方「安全」
export DATABASE_PATH=/var/lib/cursor-panel/accounts.db
uv run cursor-panel --host 0.0.0.0 --port 8787 --no-open
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PANEL_TOKEN` | 空 | 非空则 `/api/*` 需要 `X-Panel-Token`。对外监听必设 |
| `DATABASE_PATH` | `./accounts.db` | SQLite 账号库路径（相对启动时的工作目录） |
| `ACCOUNTS_PATH` | `./accounts.json` | 旧 JSON 账号库路径，仅用于首次自动迁移 |
| `MAX_WORKERS` | 48 | 通用网络/数据库线程池上限，通常无需修改 |
| `REQUEST_CONCURRENCY` | 3 | 同时访问 cursor.com 的请求上限 |
| `REQUEST_MIN_INTERVAL` | 0.5 | **出站请求的最小间隔秒数**，真正的限速闸门 |
| `REQUEST_RETRIES` | 2 | 连接错误、超时、5xx 的重试次数 |
| `RETRY_BASE_DELAY` | 0.25 | 指数退避基础秒数，每次重试另加随机抖动 |
| `RATE_LIMIT_RETRIES` | 3 | 被限流时的重试次数 |
| `RATE_LIMIT_BASE_DELAY` | 2.0 | 被限流时的退避基数，比普通重试狠得多 |
| `REFRESH_ENABLED` | 1 | 后台自动刷新总开关 |
| `REFRESH_INTERVAL` | 900 | 每个账号的目标刷新周期（秒），**别往下调**，理由见下 |
| `REFRESH_MIN_GAP` | 2.0 | 两次回源之间的绝对下限 |
| `REFRESH_IDLE_AFTER` | 1800 | 这么久没人访问面板就降速 |
| `REFRESH_IDLE_FACTOR` | 4 | 降速时周期放大的倍数 |
| `REFRESH_MAX_BACKOFF` | 8 | 撞限流后周期最多放大到几倍 |
| `MANUAL_COOLDOWN` | 60 | 单卡手动刷新的冷却秒数 |
| `MANUAL_BURST` | 5 | 手动刷新令牌桶容量 |

## 项目结构

```
cursor_dashboard/
├── client.py     cursor.com 5 个内部接口的封装 + AuthExpired / RateLimited
├── usage.py      把 5 份原始返回拼成前后端共用的结构（纯计算）
├── snapshot.py   每个账号"最后已知状态"，失败不覆盖成功
├── scheduler.py  后台错峰刷新，自适应退避
├── store.py      SQLite 读写、事务、快照表和旧 JSON 自动迁移
├── config.py     环境变量集中在这里
├── cli.py        命令行入口 cursor-quota
├── server.py     FastAPI 服务端 cursor-panel，只做编排
└── web/index.html   单文件前端，无 CDN 依赖
```

`client` + `usage` 是取数层，CLI 和面板都用它，改字段只需要改一处。

## 刷新策略

**页面不回源。** 打开面板只是读服务端快照，所有对 cursor.com 的访问都由后台调度器
发出，一个账号一个账号地慢慢刷。

之所以这么改：过去「刷新全部」会在几秒内打出 N×5 个请求（42 个账号就是 210 个），
瞬时几十 QPS，Cursor / Vercel 的边缘防护直接回 **403 + HTML 拦截页**。而旧代码把所有
403 都当成 cookie 失效，于是整屏卡片变红，用户去重新粘贴 cookie，那次粘贴同样被挡，
看起来就像「新 cookie 也不管用」。

现在分三层挡住这件事：

1. **限速率，不只限并发。** 信号量限的是并发，接口够快时 3 个并发照样能打出几十 QPS，
   而边缘防护看的就是速率。所以真正的闸门是 `REQUEST_MIN_INTERVAL`——任意两个出站
   请求之间至少隔这么久，所有路径（后台刷新、手动刷新、新增账号验活）共用这一道闸。
2. **摊平在时间轴上。** 调度器每次挑「最久没刷过」的账号刷一个，间隔是
   `REFRESH_INTERVAL / 账号数`。42 个账号 / 900 秒 = 每 21 秒一个，约 0.24 QPS，
   比原来低三个数量级。撞上限流就把间隔翻倍（最多 `REFRESH_MAX_BACKOFF` 倍），
   连续 10 次成功再收回来一档。超过 `REFRESH_IDLE_AFTER` 没人看面板就降速，
   有人打开页面立刻恢复。
3. **区分「被挡住」和「真失效」。** 401、跳登录页、403 + JSON 判为会话失效；
   403 + HTML、429、503 判为临时限流，退避重试。一个账号的 5 个接口里混着两种错误时
   **按限流处理**——宁可多等一轮，也不能冤枉用户去重粘一个好 cookie。

> ⚠️ **`REFRESH_INTERVAL` 别往下调。** 错开只降低瞬时密度，周期越短长期总量越大：
> 设成 60 秒的话 42 个账号一天就是 30 万次请求，从同一个 IP 出去，比手动刷新的总量
> 高一个数量级，反而可能招来按 IP 的封禁。额度是月度数据，`days_left` 以天计，
> 秒级新鲜度没有意义。

快照的关键性质是**失败不覆盖成功**：

- 刷新失败只写 error，`data` 原样留着。卡片继续显示上一次的数据，底下挂一行黄字说明
  为什么没更新。这样一次限流不会擦掉好数据，也不必靠重启服务来恢复。
- 认证失效要**连续确认两次**才把卡片标红。计数按「同一种失败连续出现几次」算，
  中间夹一次限流就重新计数。从没成功过的账号例外，第一次失效就直接提示重新粘贴。
- 快照落 SQLite（`snapshots` 表，只存 cookie 的哈希、不存明文），重启后页面立刻有数据，
  调度器也知道该先刷谁。换 cookie 会让旧快照自动作废。

其余设计点：

- `fetch_one` 每次新建 `requests.Session`——`Session` 跨线程共享不安全，而这 5 个
  请求本来就要同时发出去。每次调用结束都会显式关闭 Session。
- 默认线程池只有 `min(32, cpu+4)`（8 核机器是 12），所以显式设成 `MAX_WORKERS`。
  这只是通用线程池容量，不代表允许同量的 Cursor 出站连接。
- 页面一个请求就能拿回整组卡片（都是快照），开着的时候每 60 秒静默重取一次，
  切回标签页也会立刻重取——所以正常情况下不需要人去点任何刷新。
- 浏览器记住最后选择的部门和排序方式。切换部门会取消上一轮未完成请求。
- 账号默认按添加顺序显示，也可按综合剩余百分比从低到高或从高到低排序。

## 添加账号

面板点「+ 添加账号」，填写姓名并粘贴 cookie，保存前服务端会先验活。所属部门选填：
可以从下拉框选择已有部门，也可以选择“新建部门…”后手动输入名称；不选择则归入
“未分组”。

cookie 取法：浏览器登录 cursor.com → F12 → Application → Cookies →
`https://cursor.com` → 找到 `WorkosCursorSessionToken` → 复制 Value。

只能手工复制这一条路：它是 **httpOnly** cookie，网页 JS 读不到——跨域读不到，
httpOnly 连 cursor.com 自己的页面 JS 都读不到，iframe 嵌 cursor.com 会被
`frame-ancestors` 挡掉。浏览器安全模型就是要禁止「A 站页面读 B 站登录态」。

## 部门分组

账号卡片上方按部门提供标签页，可查看全部账号或只看一个部门；标签后的数字是该部门
账号数。浏览器会记住最后选择的部门，下次打开默认回到相同部门。选择“全部”即可
跨部门查看，旧账号在数据库升级后会先归入“未分组”。

新增账号时默认带入当前正在查看的部门。已有账号可点卡片右上角的楼宇图标单独调整
分组，不需要重新粘贴 cookie。

卡片中的部门和邮箱使用完整宽度，长邮箱会自动换行而不是省略。添加账号、调整分组和
面板口令弹窗都可通过右上角关闭按钮、取消按钮或 Esc 关闭。

鼠标移到卡片上，右上角出现四个图标按钮（悬停有中文说明）：

| 图标 | 作用 |
|---|---|
| 楼宇 | 调整账号所属部门，不改变 cookie |
| 🔑 钥匙 | 重新授权——粘贴新的 cookie。按 email 去重，等于续期，不会多出一张卡片 |
| ↻ 刷新 | 只刷这一个账号（`POST /api/accounts/{id}/refresh`，立刻回源） |
| 🗑 垃圾桶 | 从服务端账号库删除，不影响 Cursor 账号本身 |

单卡刷新受冷却和令牌桶约束：60 秒内刚成功刷过就直接返回现有数据，点太快会提示稍后
再试。这不影响可用性——后台本来就在自动更新。

cookie 确认失效后卡片才会变红，点钥匙图标重新粘贴即可。只是被临时限流时卡片不变红，
数据照常显示，底下会说明「Cursor 暂时限制了请求，后台会自动重试」。

每张卡片都有「最后统计」一行，显示这份数据是什么时候取到的（后台是错开刷的，每张卡
的时间本来就不一样）。页头显示后台轮一遍所有账号需要多久。

## 卡片上的字段

| 字段 | 含义 |
|---|---|
| Cursor Models | Auto / Composer 等 Cursor 自家模型的额度用量 |
| Other Models | 第三方高级模型（Claude、GPT 等）的额度用量 |
| 综合 | 两者合并口径 |
| 额度刷新 | 订阅账单周期结束时间，按浏览器本地时区显示；不足两天时精确到小时 |
| 本周期消费 | 已消费金额 / 套餐包含额度 |
| 按量付费 | 超出包含额度后是否继续按量计费 |
| Grok Bot 周额度 | Cursor 里 Grok Bot 的**独立**额度，与主额度分开算、每周重置。不用 Grok Bot 就恒为 100% |

百分比是官方接口直接给的（`autoPercentUsed` / `apiPercentUsed` /
`totalPercentUsed`），代码只做 `100 - x`，没有自己按金额折算。所以「本周期消费
$193 / 包含额度 $20」和「综合剩余 61%」并存是正常的——消费里含 Cursor 赠送额度
（`bonusSpend`），和百分比不是一个尺度。

数据来自 cursor.com 的非公开内部接口（`client.py` 里有标注），字段随时可能变。

## 安全

服务端存的是**等同登录态**的会话 token，拿到就能以该账号身份调 Cursor 接口。

- `PANEL_TOKEN` 非空时，所有 `/api/*` 需要 `X-Panel-Token` 头；页面会弹框问一次并
  记在 localStorage。**对外监听务必设置**，不设会在启动时打印警告。
- `accounts.db` 权限 0600，且已在 `.gitignore` 里，别提交。
- 服务端从不把 cookie 回传给前端，只回传额度数据。
- 公网部署建议再套一层 HTTPS 反代——口令和 cookie 都走明文 HTTP 的话没意义。

## 命令行

```bash
uv run cursor-quota                  # 终端进度条
uv run cursor-quota --json           # 结构化输出，便于入库
uv run cursor-quota -c other.json
```

全部成功退出码 0，任一账号失败为 1，方便挂定时任务。

## accounts.db

面板和默认的 `cursor-quota` 共用 SQLite 账号库。首次启动时，如果数据库尚未初始化，
会自动把旧 `accounts.json` 导入；导入完成后旧文件会保留供核对，但不会再次读取，
后续以 `accounts.db` 为准。

旧 JSON 格式仍可用于迁移，也可通过 `cursor-quota -c other.json` 临时查询：

```json
[{ "label": "主号", "department": "智慧运维", "cookie": "WorkosCursorSessionToken 的值" }]
```

`label` / `cookie` 必需，`department` 可选，面板另外会写 `email`、`updated_at`。
数据库 schema 会自动升级，现有账号和 cookie 不会被重写。数据库路径可用
`DATABASE_PATH` 环境变量修改。SQLite 解决并发登记时的覆盖问题，但 cookie 目前仍是
明文存储。

## 注意

cookie 失效时接口不返回 401，而是 307 跳登录页，`client.py` 的 `_call` 里已按此
判定会话失效（不跟跳转，否则只会拿到一个无关的 404）。

> 历史上试过两条自动取 cookie 的路，都已删除：
> **Playwright 拉起浏览器让用户登录** —— Cursor 登录页的人机验证会识别自动化浏览器；
> **Chrome 扩展读 cookie 回填** —— 能用，但要求每个使用者装扩展，不适合服务端部署。
