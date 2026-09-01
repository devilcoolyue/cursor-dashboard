from __future__ import annotations

import json
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cursor_dashboard import store


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = store.DATABASE_PATH
        self.original_legacy_path = store.LEGACY_ACCOUNTS_PATH
        root = Path(self.temp_dir.name)
        store.DATABASE_PATH = root / "accounts.db"
        store.LEGACY_ACCOUNTS_PATH = root / "accounts.json"
        store._initialized.clear()

    def tearDown(self) -> None:
        store.DATABASE_PATH = self.original_database_path
        store.LEGACY_ACCOUNTS_PATH = self.original_legacy_path
        store._initialized.clear()
        self.temp_dir.cleanup()

    def test_imports_legacy_json_only_once(self) -> None:
        legacy = [
            {"label": "主号", "cookie": "cookie-1", "email": "one@example.com"},
            {"label": "备用号", "cookie": "cookie-2"},
        ]
        store.LEGACY_ACCOUNTS_PATH.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )

        accounts = store.load_accounts()

        self.assertEqual([item["label"] for item in accounts], ["主号", "备用号"])
        self.assertEqual(stat.S_IMODE(store.DATABASE_PATH.stat().st_mode), 0o600)
        self.assertTrue(store.delete_account("one@example.com"))
        self.assertTrue(store.delete_account("备用号"))

        legacy.append({"label": "不应再次导入", "cookie": "cookie-3"})
        store.LEGACY_ACCOUNTS_PATH.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        self.assertEqual(store.load_accounts(), [])

    def test_upsert_by_email_updates_in_place(self) -> None:
        first = store.upsert_account("old-cookie", "same@example.com", "原标签")
        second = store.upsert_account("new-cookie", "same@example.com", None)

        self.assertEqual(first["label"], "原标签")
        self.assertEqual(second["label"], "原标签")
        self.assertEqual(second["cookie"], "new-cookie")
        self.assertEqual(len(store.load_accounts()), 1)

    def test_concurrent_writes_do_not_overwrite_accounts(self) -> None:
        def insert(index: int) -> None:
            store.upsert_account(
                f"cookie-{index}", f"user-{index}@example.com", f"账号{index}"
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(insert, range(40)))

        accounts = store.load_accounts()
        self.assertEqual(len(accounts), 40)
        self.assertEqual(len({item["email"] for item in accounts}), 40)


if __name__ == "__main__":
    unittest.main()
