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
| `REQUEST_CONCURRENCY` | 10 | 同时访问 cursor.com 的请求上限，过高会触发拒绝连接 |
| `REQUEST_RETRIES` | 2 | 连接错误、超时、429/5xx 的重试次数 |
| `RETRY_BASE_DELAY` | 0.25 | 指数退避基础秒数，每次重试另加随机抖动 |
| `CACHE_TTL` | 60 | 额度结果缓存秒数，0 表示每次都回源 |

## 项目结构

```
cursor_dashboard/
├── client.py    cursor.com 5 个内部接口的封装 + AuthExpired
├── usage.py     把 5 份原始返回拼成前后端共用的结构（纯计算）
├── cache.py     按账号缓存 + single-flight 锁
├── store.py     SQLite 读写、事务和旧 JSON 自动迁移
├── config.py    环境变量集中在这里
├── cli.py       命令行入口 cursor-quota
├── server.py    FastAPI 服务端 cursor-panel，只做编排
└── web/index.html   单文件前端，无 CDN 依赖
```

`client` + `usage` 是取数层，CLI 和面板都用它，改字段只需要改一处。

## 刷新性能

取数仍是两层并发：**账号之间**并发，**单账号内的 5 个接口**也并发。
`server.probe` 会先把 N×5 个任务打平，再由全局 semaphore 把实际出站连接限制在
`REQUEST_CONCURRENCY` 以内。这样单账号仍能并发取数，多账号刷新也不会突然向
cursor.com 建立几十个连接。

几个设计点：

- `fetch_one` 每次新建 `requests.Session`——`Session` 跨线程共享不安全，而这 5 个
  请求本来就要同时发出去。每次调用结束都会显式关闭 Session。
- 连接错误、超时、429 和常见 5xx 会用指数退避重试；认证失效和普通 4xx 不重试。
- 默认线程池只有 `min(32, cpu+4)`（8 核机器是 12），账号一多就排队，所以显式设成
  `MAX_WORKERS`。这只是通用线程池容量，不代表允许同量的 Cursor 出站连接。
- 页面首次加载读缓存并显示「数据 N 秒前」；点「刷新」走 `?force=1` 强制回源。
- 缓存带 single-flight：多个访客同时强制刷新时，先到的那个回源，其余复用它的结果，
  不会把同一个账号打 N 遍。cookie 变更或删除账号会立即让缓存失效。
- 正常结果按 `CACHE_TTL` 缓存；连接类错误只缓存 5 秒，避免偶发失败长期留在卡片上。

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

鼠标移到卡片上，右上角出现四个图标按钮（悬停有中文说明）：

| 图标 | 作用 |
|---|---|
| 楼宇 | 调整账号所属部门，不改变 cookie |
| 🔑 钥匙 | 重新授权——粘贴新的 cookie。按 email 去重，等于续期，不会多出一张卡片 |
| ↻ 刷新 | 只刷这一个账号（`POST /api/accounts/{id}/refresh`，强制回源） |
| 🗑 垃圾桶 | 从服务端账号库删除，不影响 Cursor 账号本身 |

cookie 失效后卡片会变红提示，点钥匙图标重新粘贴即可。
刷新时卡片会先变成骨架动画，数据全部拿到后再整体渲染，不会出现半截内容。

## 卡片上的字段

| 字段 | 含义 |
|---|---|
| Cursor Models | Auto / Composer 等 Cursor 自家模型的额度用量 |
| Other Models | 第三方高级模型（Claude、GPT 等）的额度用量 |
| 综合 | 两者合并口径 |
| 额度刷新 | 订阅账单周期结束时间，届时额度重置 |
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
