"""同套餐的额度池登记表。

用满的账号三个百分比都被截在 100，自己解不出池子，靠同套餐里解得出的账号补上。
表里的每个数都来自真实反解，不是写死的常量——这几个测试就是守这条线的。
"""

import unittest

from cursor_dashboard import pools

PRO = {"name": "Pro", "membership_type": "pro"}
SOLVED = {
    "plan": PRO,
    "quota": {
        "cursor_models": {"used_pct": 45.77, "remaining_pct": 54.23, "limit_usd": 450.0},
        "other_models": {"used_pct": 56.18, "remaining_pct": 43.82, "limit_usd": 45.0},
        "overall": {"used_pct": 46.72, "remaining_pct": 53.28, "limit_usd": 495.0},
    },
}
# 用满了：pool_limits 三档全给 None
CAPPED = {
    "plan": PRO,
    "quota": {
        "cursor_models": {"used_pct": 100.0, "remaining_pct": 0.0, "limit_usd": None},
        "other_models": {"used_pct": 100.0, "remaining_pct": 0.0, "limit_usd": None},
        "overall": {"used_pct": 100.0, "remaining_pct": 0.0, "limit_usd": None},
    },
}


class PoolsTest(unittest.TestCase):
    def setUp(self) -> None:
        pools.reset()

    def test_fills_a_capped_account_from_the_same_plan(self):
        pools.observe("solved@x.com", SOLVED)
        filled = pools.fill(CAPPED)["quota"]
        self.assertEqual(filled["cursor_models"]["limit_usd"], 450.0)
        self.assertEqual(filled["other_models"]["limit_usd"], 45.0)
        self.assertEqual(filled["overall"]["limit_usd"], 495.0)
        # 补出来的美元数要跟着这个账号自己的百分比走，不是照抄来源账号的
        self.assertEqual(filled["overall"]["used_usd"], 495.0)
        self.assertTrue(filled["overall"]["limit_inferred"])

    def test_nothing_to_copy_leaves_it_empty(self):
        """没有任何账号解出过就老实留空，绝不退回写死的 450。"""
        filled = pools.fill(CAPPED)["quota"]
        for slot in filled.values():
            self.assertIsNone(slot["limit_usd"])

    def test_never_overwrites_a_self_solved_limit(self):
        pools.observe("solved@x.com", {"plan": PRO, "quota": {
            "cursor_models": {"used_pct": 10, "limit_usd": 999.0},
            "other_models": {"used_pct": 10, "limit_usd": 99.0},
            "overall": {"used_pct": 10, "limit_usd": 1098.0},
        }})
        kept = pools.fill(SOLVED)["quota"]
        self.assertEqual(kept["cursor_models"]["limit_usd"], 450.0)
        self.assertNotIn("limit_inferred", kept["cursor_models"])

    def test_partial_solutions_are_not_recorded(self):
        """只解出总池的账号（某一档触顶）不能进表，否则会拿半个解去补别人。"""
        pools.observe("half@x.com", {"plan": PRO, "quota": {
            "cursor_models": {"used_pct": 98.03, "limit_usd": None},
            "other_models": {"used_pct": 100.0, "limit_usd": None},
            "overall": {"used_pct": 98.4, "limit_usd": 495.0},
        }})
        self.assertEqual(pools.resolve(PRO), (None, None, None))

    def test_median_shrugs_off_one_bad_account(self):
        for i, limits in enumerate([(450.0, 45.0, 495.0), (450.0, 45.0, 495.0),
                                    (402.0, 93.0, 495.0)]):
            pools.observe(f"a{i}@x.com", {"plan": PRO, "quota": {
                "cursor_models": {"used_pct": 1, "limit_usd": limits[0]},
                "other_models": {"used_pct": 1, "limit_usd": limits[1]},
                "overall": {"used_pct": 1, "limit_usd": limits[2]},
            }})
        self.assertEqual(pools.resolve(PRO), (450.0, 45.0, 495.0))

    def test_plans_do_not_bleed_into_each_other(self):
        pools.observe("pro@x.com", SOLVED)
        business = {**CAPPED, "plan": {"name": "Business"}}
        for slot in pools.fill(business)["quota"].values():
            self.assertIsNone(slot["limit_usd"])

    def test_same_account_counts_once(self):
        for limit in (450.0, 450.0, 450.0):
            pools.observe("same@x.com", {"plan": PRO, "quota": {
                "cursor_models": {"used_pct": 1, "limit_usd": limit},
                "other_models": {"used_pct": 1, "limit_usd": 45.0},
                "overall": {"used_pct": 1, "limit_usd": 495.0},
            }})
        self.assertEqual(pools.snapshot_state(), {"pro": 1})


if __name__ == "__main__":
    unittest.main()
