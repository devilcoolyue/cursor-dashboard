"""运行期配置，全部来自环境变量。集中在这里，改的时候不用翻代码。"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_INDEX = PACKAGE_DIR / "web" / "index.html"

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

# 账号多时不能把 N×5 个请求同时压向 cursor.com，连接洪峰会导致 Connection refused。
REQUEST_CONCURRENCY = max(1, int(os.environ.get("REQUEST_CONCURRENCY", "10")))
REQUEST_RETRIES = max(0, int(os.environ.get("REQUEST_RETRIES", "2")))
RETRY_BASE_DELAY = max(0.0, float(os.environ.get("RETRY_BASE_DELAY", "0.25")))

# 每次刷新都要打 cursor.com 5×N 次，容易触发限流；缓存内的结果直接复用。
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))
