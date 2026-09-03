"""额度池反解和按模型明细的组装。全是纯计算，不发请求。

反解用的两组百分比来自同一个真实账号的两个不同时刻，都应解出 $450 / $45 / $495。
"""

import unittest

from cursor_dashboard.usage import assemble, assemble_detail, pool_limits


class PoolLimitsTest(unittest.TestCase):
    def test_real_snapshot(self):
        """实测数据：两个时刻、不同的 api 用量，池大小必须稳定。"""
        self.assertEqual(
            pool_limits(22928, 45.76888888888889, 51.82222222222222, 46.31919191919192),
            (450.0, 45.0, 495.0),
        )
        self.assertEqual(
            pool_limits(23124, 45.76888888888889, 56.177777777777784, 46.71515151515152),
            (450.0, 45.0, 495.0),
        )

    def test_single_sided_usage(self):
        """只用其中一类也要解得出——另一类的百分比是 0，不是缺失。"""
        self.assertEqual(pool_limits(22500, 50.0, 0.0, 22500 / 49500 * 100),
                         (450.0, 45.0, 495.0))
        self.assertEqual(pool_limits(2250, 0.0, 50.0, 2250 / 49500 * 100),
                         (450.0, 45.0, 495.0))

    def test_nothing_used(self):
        self.assertEqual(pool_limits(0, 0, 0, 0), (None, None, None))

    def test_capped_percentages_are_not_trusted(self):
        """百分比截顶在 100，代进方程就是假数据。三组都来自 42 个账号的实测。"""
        # 某一档触顶：总池仍然对得上 $495，但分池必须放弃
        self.assertEqual(pool_limits(48708, 98.03, 100.00, 98.40), (None, None, 495.0))
        self.assertEqual(pool_limits(48950, 100.00, 87.58, 98.89), (None, None, 494.99))
        # 全用满：解出来的"总池"其实是消费额，会随着继续消费一直变大，一概不要
        self.assertEqual(pool_limits(49532, 100.0, 100.0, 100.0), (None, None, None))

    def test_uncapped_result_stays_precise(self):
        """加了触顶保护后，正常账号的解不受影响。"""
        self.assertEqual(
            pool_limits(22928, 45.76888888888889, 51.82222222222222, 46.31919191919192),
            (450.0, 45.0, 495.0),
        )

    def test_degenerate_keeps_total_only(self):
        """两类用量比例恰好等于池的比例时分不开，只能报总池，不许瞎猜分池。"""
        self.assertEqual(pool_limits(4950, 10.0, 10.0, 10.0), (None, None, 495.0))

    def test_assemble_exposes_limits(self):
        data = assemble("测试", {}, {}, {}, {"planUsage": {
            "totalSpend": 22928,
            "autoPercentUsed": 45.76888888888889,
            "apiPercentUsed": 51.82222222222222,
            "totalPercentUsed": 46.31919191919192,
        }}, {})
        quota = data["quota"]
        self.assertEqual(quota["cursor_models"]["limit_usd"], 450.0)
        self.assertEqual(quota["other_models"]["limit_usd"], 45.0)
        self.assertEqual(quota["overall"]["limit_usd"], 495.0)
        # 百分比仍然是主口径，美元只是换算出来的
        self.assertAlmostEqual(quota["cursor_models"]["used_usd"], 205.96, places=1)
        self.assertAlmostEqual(quota["cursor_models"]["remaining_usd"], 244.04, places=1)

    def test_assemble_without_usage_leaves_none(self):
        quota = assemble("测试", {}, {}, {}, {}, {})["quota"]
        for slot in quota.values():
            self.assertIsNone(slot["limit_usd"])
            self.assertIsNone(slot["used_usd"])
            self.assertIsNone(slot["remaining_usd"])


class GrokWeeklyTest(unittest.TestCase):
    """Grok Bot 的周额度是独立的池子，接口只给周期起点，重置时间要自己 +7 天。"""

    def test_reset_is_seven_days_after_period_start(self):
        grok = assemble("测试", {}, {}, {}, {}, {
            "usagePercent": 12.5,
            "currentPeriodStart": "2026-09-01T00:00:00Z",
        })["grok_weekly"]
        self.assertEqual(grok["used_pct"], 12.5)
        self.assertEqual(grok["remaining_pct"], 87.5)
        self.assertEqual(grok["reset_at"][:10], "2026-09-08")

    def test_missing_period_start_keeps_percentages(self):
        grok = assemble("测试", {}, {}, {}, {}, {"usagePercent": 0})["grok_weekly"]
        self.assertEqual(grok["remaining_pct"], 100.0)
        self.assertIsNone(grok["reset_at"])

    def test_failed_endpoint_drops_the_whole_row(self):
        """client.grok_status 失败时返回 {}，整条给 None，前端就不显示这一行。"""
        self.assertIsNone(assemble("测试", {}, {}, {}, {}, {})["grok_weekly"])


class AssembleDetailTest(unittest.TestCase):
    payload = {
        "aggregations": [
            {"modelIntent": "cursor-grok-4.6-high-fast", "inputTokens": "6574701",
             "outputTokens": "756508", "cacheReadTokens": "99230848",
             "totalCents": 13360.28537, "tier": 2},
            {"modelIntent": "gpt-5.6-sol-high", "inputTokens": "974503",
             "outputTokens": "107920", "cacheWriteTokens": "1982167",
             "cacheReadTokens": "20759927", "totalCents": 2308.14, "tier": 1},
            {"modelIntent": "default", "inputTokens": "83475", "outputTokens": "2800",
             "cacheReadTokens": "72714", "totalCents": 18.11, "tier": 2},
            {"modelIntent": "gpt-5.6-sol-xhigh", "inputTokens": None,
             "outputTokens": None, "totalCents": 0, "tier": 1},
        ],
        "totalCostCents": 15686.53537,
        "totalInputTokens": "7632679",
        "totalOutputTokens": "867228",
        "totalCacheWriteTokens": "1982167",
        "totalCacheReadTokens": "120063489",
    }

    def test_groups_by_tier(self):
        detail = assemble_detail(self.payload)
        groups = {g["key"]: g for g in detail["groups"]}
        self.assertEqual([m["model"] for m in groups["cursor_models"]["models"]],
                         ["cursor-grok-4.6-high-fast", "default"])
        self.assertEqual([m["model"] for m in groups["other_models"]["models"]],
                         ["gpt-5.6-sol-high", "gpt-5.6-sol-xhigh"])
        # 组内小计按分求和再转美元，不是每行取整后相加
        self.assertEqual(groups["cursor_models"]["spend_usd"], 133.78)
        self.assertEqual(groups["other_models"]["spend_usd"], 23.08)

    def test_totals_and_tokens(self):
        detail = assemble_detail(self.payload)
        self.assertEqual(detail["totals"]["spend_usd"], 156.87)
        self.assertEqual(detail["totals"]["model_count"], 4)
        self.assertEqual(detail["totals"]["input_tokens"], 7632679)
        first = detail["groups"][0]["models"][0]
        self.assertEqual(first["cache_write_tokens"], 0)   # 字段缺失当 0
        self.assertEqual(first["total_tokens"], 6574701 + 756508 + 99230848)

    def test_sorted_by_spend(self):
        models = [m for g in assemble_detail(self.payload)["groups"] for m in g["models"]]
        self.assertEqual(models[0]["model"], "cursor-grok-4.6-high-fast")

    def test_empty_payload(self):
        detail = assemble_detail(None)
        self.assertEqual(detail["totals"]["model_count"], 0)
        self.assertEqual([g["models"] for g in detail["groups"]], [[], []])


if __name__ == "__main__":
    unittest.main()
