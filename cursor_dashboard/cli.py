"""命令行入口：把每个账号的额度打印到终端。

    cursor-quota                # 终端进度条
    cursor-quota --json         # 结构化输出，便于入库
    cursor-quota -c other.json  # 临时读取旧版 JSON

默认读取 Web 面板共用的 SQLite 账号库；旧 JSON 格式见 accounts.example.json。
Cookie 取法：浏览器登录 cursor.com
→ F12 → Application → Cookies → https://cursor.com → 复制 WorkosCursorSessionToken 的 Value。

全部成功退出码 0，任一账号失败为 1，方便挂定时任务。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from .client import AuthExpired, CursorClient, RateLimited
from .store import AccountsError, load_accounts
from .usage import collect


def bar(p, width=24):
    filled = int(round(width * min(100.0, max(0.0, p)) / 100))
    return "█" * filled + "░" * (width - filled)


def render(d: dict) -> str:
    q, c, p = d["quota"], d["cycle"], d["plan"]
    lines = [
        "=" * 58,
        f"{d['label']}  <{d['email']}>",
        f"套餐: {p['name']} {p['price'] or ''}   包含额度: ${p['included_usd']}",
        f"刷新: {(c['reset_at'] or '未知')[:19]} UTC" + (f"  (剩 {c['days_left']} 天)" if c["days_left"] is not None else ""),
        "-" * 58,
        f"Cursor Models (Auto/Composer/Grok)  剩余 {q['cursor_models']['remaining_pct']:5.1f}%  {bar(q['cursor_models']['used_pct'])}",
        f"Other Models  (第三方高级模型)      剩余 {q['other_models']['remaining_pct']:5.1f}%  {bar(q['other_models']['used_pct'])}",
        f"综合                                剩余 {q['overall']['remaining_pct']:5.1f}%  {bar(q['overall']['used_pct'])}",
        "-" * 58,
        f"本周期消费 ${d['spend_usd']['total']} "
        f"(含额度 ${d['spend_usd']['from_included']} + 赠送 ${d['spend_usd']['from_bonus']})",
        f"按量付费: {'开启' if d['on_demand']['enabled'] else '关闭'}"
        + (f"  已用 ${d['on_demand']['used_usd']}" if d["on_demand"]["enabled"] else ""),
    ]
    if d.get("notice"):
        lines.append(f"⚠ {d['notice']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="查询 Cursor 账号额度")
    ap.add_argument("-c", "--config", help="读取指定的旧版 JSON 账号文件")
    ap.add_argument("--json", action="store_true", help="输出 JSON，方便入库")
    args = ap.parse_args()

    if args.config:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            sys.exit(f"找不到配置文件 {cfg_path}")
        accounts = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        try:
            accounts = load_accounts()
        except AccountsError as exc:
            sys.exit(str(exc))
    if not accounts:
        sys.exit("账号库为空，请先在 Web 面板添加账号")

    results, failed = [], 0
    for acc in accounts:
        client = CursorClient(acc["cookie"], acc.get("label", ""))
        try:
            results.append(collect(client))
        except AuthExpired as e:
            failed += 1
            results.append({"label": client.label, "error": str(e)})
        except RateLimited as e:
            failed += 1
            results.append({"label": client.label, "error": str(e)})
        except requests.RequestException as e:
            failed += 1
            results.append({"label": client.label, "error": f"请求失败: {e}"})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(render(r) if "error" not in r else f"\n[{r['label']}] ✗ {r['error']}")
        print("=" * 58)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
