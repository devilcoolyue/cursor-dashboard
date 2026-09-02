from __future__ import annotations

import json
import sqlite3
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
            {
                "label": "主号",
                "cookie": "cookie-1",
                "email": "one@example.com",
                "department": "智慧运维",
            },
            {"label": "备用号", "cookie": "cookie-2"},
        ]
        store.LEGACY_ACCOUNTS_PATH.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )

        accounts = store.load_accounts()

        self.assertEqual([item["label"] for item in accounts], ["主号", "备用号"])
        self.assertEqual(
            [item["department"] for item in accounts], ["智慧运维", ""]
        )
        self.assertEqual(stat.S_IMODE(store.DATABASE_PATH.stat().st_mode), 0o600)
        self.assertTrue(store.delete_account("one@example.com"))
        self.assertTrue(store.delete_account("备用号"))

        legacy.append({"label": "不应再次导入", "cookie": "cookie-3"})
        store.LEGACY_ACCOUNTS_PATH.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        self.assertEqual(store.load_accounts(), [])

    def test_upsert_by_email_updates_in_place(self) -> None:
        first = store.upsert_account(
            "old-cookie", "same@example.com", "原标签", "智慧运维"
        )
        second = store.upsert_account("new-cookie", "same@example.com", None)

        self.assertEqual(first["label"], "原标签")
        self.assertEqual(second["label"], "原标签")
        self.assertEqual(second["cookie"], "new-cookie")
        self.assertEqual(second["department"], "智慧运维")
        self.assertEqual(len(store.load_accounts()), 1)

    def test_migrates_v1_database_without_losing_accounts(self) -> None:
        conn = sqlite3.connect(store.DATABASE_PATH)
        conn.execute(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                cookie TEXT NOT NULL,
                email TEXT UNIQUE,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata VALUES ('schema_version', '1')")
        conn.execute(
            "INSERT INTO accounts (label, cookie, email, updated_at) VALUES (?, ?, ?, ?)",
            ("旧账号", "old-cookie", "old@example.com", 123),
        )
        conn.commit()
        conn.close()

        accounts = store.load_accounts()

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["email"], "old@example.com")
        self.assertEqual(accounts[0]["cookie"], "old-cookie")
        self.assertEqual(accounts[0]["department"], "")
        conn = sqlite3.connect(store.DATABASE_PATH)
        version = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        conn.close()
        self.assertEqual(version, "3")
        self.assertIn("snapshots", tables)

    def test_updates_department_without_changing_cookie(self) -> None:
        store.upsert_account("keep-cookie", "one@example.com", "张三", "研发中心")

        updated = store.update_account_department("one@example.com", " 智慧运维 ")

        self.assertIsNotNone(updated)
        self.assertEqual(updated["department"], "智慧运维")
        self.assertEqual(updated["cookie"], "keep-cookie")
        self.assertIsNone(store.update_account_department("missing", "其他部门"))

    def test_concurrent_writes_do_not_overwrite_accounts(self) -> None:
        def insert(index: int) -> None:
            store.upsert_account(
                f"cookie-{index}",
                f"user-{index}@example.com",
                f"账号{index}",
                "智慧运维" if index % 2 else "研发中心",
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(insert, range(40)))

        accounts = store.load_accounts()
        self.assertEqual(len(accounts), 40)
        self.assertEqual(len({item["email"] for item in accounts}), 40)
        self.assertEqual({item["department"] for item in accounts}, {"智慧运维", "研发中心"})


if __name__ == "__main__":
    unittest.main()
