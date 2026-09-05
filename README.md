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
| `DETAIL_TTL` | 60 | 按模型明细的缓存秒数 |

## 项目结构

```
cursor_dashboard/
├── client.py     cursor.com 内部接口的封装 + AuthExpired / RateLimited
├── usage.py      把原始返回拼成前后端共用的结构、反解额度池（纯计算）
├── pools.py      同套餐额度池登记表，补上用满账号解不出的上限
├── snapshot.py   每个账号"最后已知状态"，失败不覆盖成功
├── scheduler.py  后台错峰刷新，自适应退避
├── store.py      SQLite 读写、事务、快照表和旧 JSON 自动迁移
├── config.py     环境变量集中在这里
├── cli.py        命令行入口 cursor-quota
├── server.py     FastAPI 服务端 cursor-panel，只做编排
└── web/            前端：index.html + css/（令牌、骨架、组件、皮肤）+ js/（ui.js、app.js）
                 无构建步骤、无 CDN 依赖，挂在 /static 下
```

`client` + `usage` 是取数层，CLI 和面板都用它，改字段只需要改一处。

## 刷新策略

**页面不回源。** 打开面板只是读服务端快照，所有对 cursor.com 的访问都由后台调度器
发出，一个账号一个账号地慢慢刷。

之所以这么改：过去「刷新全部」会在几秒内打出 N×4 个请求（42 个账号就是 168 个），
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

- `fetch_one` 每次新建 `requests.Session`——`Session` 跨线程共享不安全，而这 4 个
  请求本来就要同时发出去。每次调用结束都会显式关闭 Session。
- 默认线程池只有 `min(32, cpu+4)`（8 核机器是 12），所以显式设成 `MAX_WORKERS`。
  这只是通用线程池容量，不代表允许同量的 Cursor 出站连接。
- 页面一个请求就能拿回整组卡片（都是快照），开着的时候每 60 秒静默重取一次，
  切回标签页也会立刻重取——所以正常情况下不需要人去点任何刷新。
- 浏览器记住最后选择的部门和排序方式。切换部门会取消上一轮未完成请求。
- 账号默认按添加顺序显示，也可按综合剩余百分比从低到高或从高到低排序。

## 液态玻璃动效测试

在独立工作区运行 `uv run python dev/preview.py --port 8789`，打开
`http://127.0.0.1:8789`。预览使用内存中的模拟账号，不访问 Cursor、不读取真实账号库，
重启后数据重置。默认进入液态玻璃主题，添加测试账号时 Cookie 填 `demo`。

可验证明暗滑块、部门切换与卡片入场、额度明细展开/收回、单卡刷新、新账号凝聚五处
动效。仅玻璃主题启用；系统开启「减少动态效果」后停用液态形变。明暗与部门滑块分三段
走完 950ms：整块先收成一滴（圆头 + 尖尾，尖朝运动的反方向），这滴水从中间那些 tab
底下穿过去，落位后再摊开成框。形状由 `clip-path` 画——`border-radius` 出不来水滴，
椭圆角接出来是蛋、直角配正方形又只有一丁点尾巴。位移不足 4px 时直接就位不演。滑动途中轮廓和投影各加重一档，浅色下用靛蓝描边
（白描边在白托盘上看不见）。
单卡刷新期间那张卡退回骨架，失败显示原因，完成后按当前排序更新位置并把焦点放回刷新
按钮；后台轮询不触发入场动效。
详情从额度行的实际轮廓展开，宽高采用不同的缓动节奏，文字保持原字号并在展开途中显现；
收起采用独立轨迹，支持中途关闭时从当前形态连续收回。

浏览器回归脚本：`playwright-cli run-code --filename dev/verify-glass.js`，连接上面的
测试页后执行，截图保存在 `output/playwright/`。
详情逐帧验证使用 `playwright-cli run-code --filename dev/verify-detail-motion.js`。

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

排序与部门选择使用统一的主题下拉菜单，支持方向键、Enter 选择、Esc 收起、长列表滚动
和视口边缘避让。删除账号会显示带姓名、邮箱的确认弹窗；提交期间禁用重复操作，失败时
在弹窗内显示原因并允许重试。

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

## 前端公共组件

`web/js/ui.js` 暴露 `PanelUI`，`web/css/ui.css` 负责组件样式，颜色、阴影和圆角沿用
`tokens.css`。所有资源本地加载，无需 npm、构建工具或 CDN。

```javascript
PanelUI.init();                         // 增强现有单选 select，注册 dialog；可重复调用
PanelUI.select.refresh(selectElement);  // 代码修改 value 后同步显示；选项变化会自动同步
PanelUI.select.focus(selectElement);    // 聚焦可见的选择器
PanelUI.open(dialogElement);            // 打开自定义内容弹窗
PanelUI.close(dialogElement);           // 关闭弹窗并恢复焦点与背景滚动

const confirmed = await PanelUI.confirm({
  title: '删除账号',
  message: '确认移除这个账号？',
  subject: '账号姓名',
  detail: 'name@example.com',
  tone: 'danger',
  confirmText: '删除账号',
  pendingText: '删除中…',
  onConfirm: async () => { /* 执行请求；抛出的错误会显示在弹窗内 */ },
});
await PanelUI.alert({ title: '操作完成', message: '账号信息已更新。' });
```

原始 `select` 仍保存值并触发标准 `input` / `change` 事件，可继续使用表单提交。
动态插入组件后调用 `PanelUI.init(container)`；`data-native`、多选和不支持 Popover API
的浏览器保留原生选择器。弹窗的关闭按钮使用 `data-close-dialog="弹窗ID"`，默认支持
Esc 和点击遮罩关闭；设置 `data-dismiss-backdrop="false"` 可禁用遮罩关闭。
下拉展开时 Esc 只收起菜单，再次按下才关闭弹窗。确认弹窗默认聚焦取消按钮。

## 卡片上的字段

| 字段 | 含义 |
|---|---|
| 左上角角标 | 综合剩余额度的四档：充足（>30%）· 偏紧（10~30%）· 告急（≤10%）· 燃尽（0%） |
| 右上角圆环 | 环走过多少 = 本周期过了多少，中心是还剩几天（不足两天显示小时）刷新额度 |
| Cursor Models | Auto / Composer 等 Cursor 自家模型的额度用量，名字后面是这条额度的上限 |
| Other Models | 第三方高级模型（Claude、GPT 等）的额度用量 |
| 综合 | 两者合并口径 |
| 额度刷新 | 订阅账单周期结束的具体时刻，按浏览器本地时区显示（还剩多久看右上角的环） |
| 本周期消费 | 已消费金额 / 本周期额度上限，和「综合」同口径；订阅含的 $20 在悬停提示里 |
| 按量付费 | 超出包含额度后是否继续按量计费 |
| Grok Bot 周额度 | 独立结算的周额度，不占用上面几条；接口没返回时整行不显示 |

百分比是官方接口直接给的（`autoPercentUsed` / `apiPercentUsed` /
`totalPercentUsed`），代码只做 `100 - x`，没有自己按金额折算。「本周期消费」的分母是
下面反解出来的额度上限，跟「综合」同口径（$231.24 / $495 ↔ 剩 53.3%）。订阅本身包含的
$20 是另一回事——超出部分走 Cursor 赠送额度（`bonusSpend`），拿它当分母会显示成
$231.24 / $20.00，看着像超支十倍，所以挪进了悬停提示。

同理，接口里那句 `displayMessage`（"You've hit your usage limit"）走的也是金额口径：
包含额度 $20 一用完它就固定这么返回，哪怕额度还剩九成。挂在卡片上纯属吓人，页面不
显示它。

Grok Bot 的周额度是另一个池子，跟上面三条完全独立：它是 x.ai 的独立桌面 / iOS App
（用 Cursor 账号登录的常驻云端 agent），按周重置，没装这个 App 的账号会一直是 100%。
这一条要多打一个接口（`get-sand-usage-status`），失败时只是这一行不显示，不影响卡片
其余部分。

### 额度上限是怎么来的

「Cursor Models $450」里的 $450 不是接口里的字段，也不是写死的，是从官方百分比反解
出来的（`usage.pool_limits`）。`totalPercentUsed` 是两个池按容量加权的平均数：

```
totalPct·(A+B) = autoPct·A + apiPct·B
    总池  T = totalSpend / totalPct
    池之比  A/B = (apiPct − totalPct) / (totalPct − autoPct)
```

某 Pro 账号在两个不同时刻都解出 A=$450、B=$45、T=$495，且按模型明细分类求和的金额与
`autoPct·A`、`apiPct·B` 逐分吻合。

解不出来的情况有两种：三个百分比互相贴得太近时方程退化；某一档用量触顶（接口把百分比
截在 100，真用到 110% 也报 100）时那一档不可信，综合也触顶时连总池都算不出来。

用满的账号属于后者，但它的池子并不是未知——池子是套餐的常量，所以 `pools.py` 从**同
套餐里解得出的账号**那儿抄一份补上（按套餐存每个账号最近一次观测，逐档取中位数）。
表里每个数都来自真实反解，不是代码里写死的：套餐涨价或换成 Pro+ 会自动跟着变，而一个
账号都没解出来过时就老实留空。`GET /api/status` 的 `plan_pools` 能看到每个套餐现在有
几个账号在支撑这张表。

消费超过上限时金额标红，比如用满的账号显示 `$495.32 / $495`——Cursor 允许小幅超出，
这和「综合 剩 0.0%」说的是同一件事。

这跟上面那条「不拿金额折算百分比」不矛盾：那说的是别拿 `totalSpend` 去除 $20，两者
不是一个尺度；这里是反过来用官方百分比标定池子有多大，百分比仍是唯一的事实来源。

### 按模型明细

点卡片上任意一条额度，弹窗列出**本账单周期内**用过的每个模型、输入/输出/缓存写/
缓存读 token 和花费，数据来自 `get-aggregated-usage-events`（cursor.com 网页 Usage
页面的同一个数据源，但窗口传的是账单周期起点，不是它默认的按天）。分类用返回里的
`tier` 字段，不是按模型名匹配 `autoBucketModels`——那个列表实测滞后，会把新模型全归
到 Other。

缓存写和缓存读分两列，是因为接口本来就分开给（`cacheWriteTokens` / `cacheReadTokens`）
且两者单价差一个量级，加在一起会把花费的大头藏起来：实测某账号 206M tokens 里 185M
是缓存读。Cursor 自家模型（tier 2）的缓存写恒为 0，第三方模型才有。

接口只回传算好的 `totalCents`，**不含单价**，所以弹窗底部挂了官方价目表的链接，要核
对的人自己去查。别把价目表内嵌进代码算成本——金额是 Cursor 算好的，自己乘一遍只会引
入偏差，还得跟着供应商调价维护。

**明细只在点开时才拉，不进后台轮询。** 42 个账号全量拉一遍就是又一次请求洪峰，正是
刷新策略那一节要避免的东西。同一张卡 `DETAIL_TTL` 秒内重复点会命中缓存。

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
