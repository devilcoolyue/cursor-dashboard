"""运行期配置，全部来自环境变量。集中在这里，改的时候不用翻代码。"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
# 页面拆成了 index.html + css/ + js/，整个 web/ 目录挂到 /static 下静态托管。
# 仍然没有任何构建步骤，也不引任何 CDN：改完刷新浏览器就生效。
WEB_DIR = PACKAGE_DIR / "web"
WEB_INDEX = WEB_DIR / "index.html"

# SQLite 账号库。旧 ACCOUNTS_PATH 仅作为首次启动时的 JSON 迁移来源；若已有部署设置
# 了它，数据库会自然落到同目录、同文件名的 .db 文件中。
LEGACY_ACCOUNTS_PATH = Path(
    os.environ.get("ACCOUNTS_PATH") or Path.cwd() / "accounts.json"
)
DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH") or LEGACY_ACCOUNTS_PATH.with_suffix(".db")
)

# 非空则所有 /api/* 需要带 X-Panel-Token。公网部署务必设置：
# 这个服务存的是等同登录态的会话 token，裸奔等于把账号送人。
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "").strip()

# 取数全是网络等待，线程开大不占 CPU。默认线程池只有 min(32, cpu+4)，
# 小机器上是 6~12，所以保留较大的通用线程池；Cursor 出站连接另有限流。
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "48"))


def _positive_float(name: str, default: str) -> float:
    return max(0.0, float(os.environ.get(name, default)))


# ---------- 出站节流 ----------
# 限并发不等于限速率：10 个并发在接口够快时依然是每秒几十个请求，而 Cursor 的边缘
# 防护看的是单位时间请求数。所以并发压到很小，真正的闸门是下面的最小间隔。
REQUEST_CONCURRENCY = max(1, int(os.environ.get("REQUEST_CONCURRENCY", "3")))
# 任意两个出站请求之间的最小间隔（秒）。所有访问 cursor.com 的路径都过这道闸。
REQUEST_MIN_INTERVAL = _positive_float("REQUEST_MIN_INTERVAL", "0.5")

REQUEST_RETRIES = max(0, int(os.environ.get("REQUEST_RETRIES", "2")))
RETRY_BASE_DELAY = _positive_float("RETRY_BASE_DELAY", "0.25")
# 被挡住时退避得更狠：连接抖动几百毫秒就够，限流要等几秒才有意义。
RATE_LIMIT_RETRIES = max(0, int(os.environ.get("RATE_LIMIT_RETRIES", "3")))
RATE_LIMIT_BASE_DELAY = _positive_float("RATE_LIMIT_BASE_DELAY", "2.0")

# ---------- 后台刷新 ----------
# 页面不再回源，只读快照；回源由后台调度器一个一个账号慢慢做。
REFRESH_ENABLED = os.environ.get("REFRESH_ENABLED", "1").strip() not in ("0", "false", "no")
# 每个账号的目标刷新周期（秒）。调度器把它均摊成账号之间的间隔：
# 42 个账号 / 900 秒 = 每 21 秒刷一个，平均 0.24 QPS。
# **别往下调到几分钟**：错开只降瞬时密度，周期越短长期总量越大，
# 同一个 IP 上 24 小时不停打，反而会招来更严的封禁。额度是月度数据，不需要秒级新鲜。
REFRESH_INTERVAL = max(60.0, _positive_float("REFRESH_INTERVAL", "900"))
# 两次回源之间的绝对下限，防止账号数很多时把间隔压没了。
REFRESH_MIN_GAP = _positive_float("REFRESH_MIN_GAP", "2.0")
# 这么久没人访问面板就降速，没人看的时候不值得持续打 cursor.com。
REFRESH_IDLE_AFTER = _positive_float("REFRESH_IDLE_AFTER", "1800")
REFRESH_IDLE_FACTOR = max(1.0, _positive_float("REFRESH_IDLE_FACTOR", "4"))
# 撞上限流后间隔翻倍，最多放大到这个倍数；连续成功再慢慢收回来。
REFRESH_MAX_BACKOFF = max(1.0, _positive_float("REFRESH_MAX_BACKOFF", "8"))

# ---------- 手动刷新 ----------
# 单卡刷新保留，但要拦住"狂点一片卡片"这种新的洪峰入口。
MANUAL_COOLDOWN = _positive_float("MANUAL_COOLDOWN", "60")
MANUAL_BURST = max(1, int(os.environ.get("MANUAL_BURST", "5")))
# 按模型明细的缓存寿命。明细只在点开卡片时才回源，这道缓存挡的是"同一张卡连点几下"，
# 不是为了少刷新——额度是月度数据，一分钟内的两次点击看到同一份结果没有区别。
DETAIL_TTL = _positive_float("DETAIL_TTL", "60")
