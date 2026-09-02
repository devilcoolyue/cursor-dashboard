"""SQLite 账号库读写。

cookie 仍是等同登录态的会话 token，所以数据库文件保持 0600。SQLite 的事务和
唯一约束负责处理多人同时登记；首次启动会把旧 accounts.json 导入一次。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATABASE_PATH, LEGACY_ACCOUNTS_PATH


class AccountsError(RuntimeError):
    """账号数据库无法读取或写入，需要人工处理。"""


_init_lock = threading.Lock()
_initialized: set[Path] = set()


def _database_key() -> Path:
    return DATABASE_PATH.resolve(strict=False)


def _raw_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _read_legacy_accounts() -> list[dict]:
    if not LEGACY_ACCOUNTS_PATH.exists():
        return []
    try:
        data = json.loads(LEGACY_ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountsError(f"旧账号库 {LEGACY_ACCOUNTS_PATH} 解析失败：{exc}") from exc
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("cookie")]


def _legacy_updated_at(account: dict, fallback: int) -> int:
    try:
        return int(account.get("updated_at") or fallback)
    except (TypeError, ValueError):
        return fallback


def _import_legacy(conn: sqlite3.Connection, accounts: list[dict]) -> None:
    now = int(time.time())
    for index, account in enumerate(accounts, start=1):
        cookie = str(account["cookie"])
        email = str(account.get("email") or "").strip() or None
        label = str(account.get("label") or email or f"账号{index}")
        department = str(account.get("department") or "").strip()
        updated_at = _legacy_updated_at(account, now)
        if email:
            conn.execute(
                """
                INSERT INTO accounts (label, cookie, email, department, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    label = excluded.label,
                    cookie = excluded.cookie,
                    department = excluded.department,
                    updated_at = excluded.updated_at
                """,
                (label, cookie, email, department, updated_at),
            )
        else:
            conn.execute(
                """
                INSERT INTO accounts (label, cookie, email, department, updated_at)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (label, cookie, department, updated_at),
            )


def _set_database_permissions() -> None:
    for path in (
        DATABASE_PATH,
        Path(f"{DATABASE_PATH}-wal"),
        Path(f"{DATABASE_PATH}-shm"),
    ):
        if path.exists():
            os.chmod(path, 0o600)


def _ensure_database() -> None:
    key = _database_key()
    if key in _initialized and DATABASE_PATH.exists():
        return

    with _init_lock:
        if key in _initialized and DATABASE_PATH.exists():
            return
        if key == LEGACY_ACCOUNTS_PATH.resolve(strict=False):
            raise AccountsError("DATABASE_PATH 不能与旧 ACCOUNTS_PATH 指向同一个文件")

        conn: sqlite3.Connection | None = None
        try:
            conn = _raw_connect()
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    cookie TEXT NOT NULL,
                    email TEXT UNIQUE,
                    department TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            # 后台刷新的结果快照。存的是 usage.assemble() 的产物，**不含 cookie**；
            # fingerprint 只是 cookie 的哈希，用来判断"换过 cookie 了、旧快照作废"。
            # 落盘是为了重启后页面立刻有数据，调度器也知道该先刷谁。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    account_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload TEXT,
                    ok_at INTEGER,
                    error_kind TEXT,
                    error_message TEXT,
                    error_at INTEGER,
                    failures INTEGER NOT NULL DEFAULT 0,
                    attempted_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()
            }
            if "department" not in columns:
                conn.execute(
                    "ALTER TABLE accounts ADD COLUMN department TEXT NOT NULL DEFAULT ''"
                )

            migrated = conn.execute(
                "SELECT 1 FROM metadata WHERE key = 'legacy_json_migrated'"
            ).fetchone()
            if not migrated:
                _import_legacy(conn, _read_legacy_accounts())
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES ('legacy_json_migrated', ?)",
                    (str(int(time.time())),),
                )
            conn.execute(
                """
                INSERT INTO metadata (key, value) VALUES ('schema_version', '3')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
            conn.commit()
            _set_database_permissions()
        except AccountsError:
            if conn:
                conn.rollback()
            raise
        except (OSError, sqlite3.Error) as exc:
            if conn:
                conn.rollback()
            raise AccountsError(f"账号数据库 {DATABASE_PATH} 操作失败：{exc}") from exc
        finally:
            if conn:
                conn.close()
        _initialized.add(key)


def _connect() -> sqlite3.Connection:
    _ensure_database()
    return _raw_connect()


def _row_to_account(row: sqlite3.Row) -> dict:
    return {
        "label": row["label"],
        "cookie": row["cookie"],
        "email": row["email"],
        "department": row["department"],
        "updated_at": row["updated_at"],
    }


def load_accounts() -> list[dict]:
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT label, cookie, email, department, updated_at FROM accounts ORDER BY id"
        ).fetchall()
        return [_row_to_account(row) for row in rows]
    except AccountsError:
        raise
    except sqlite3.Error as exc:
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 读取失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def upsert_account(
    cookie: str,
    email: str | None,
    label: str | None,
    department: str | None = None,
) -> dict:
    """按 email 原子去重：同一账号重新回填只更新已有记录。"""
    email = (email or "").strip() or None
    requested_label = (label or "").strip() or None
    requested_department = department.strip() if department is not None else None
    now = int(time.time())
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        row = None
        if email:
            row = conn.execute(
                "SELECT id, label, department FROM accounts WHERE email = ?", (email,)
            ).fetchone()

        if row:
            final_label = requested_label or row["label"]
            final_department = (
                requested_department
                if requested_department is not None
                else row["department"]
            )
            conn.execute(
                """
                UPDATE accounts
                SET label = ?, cookie = ?, department = ?, updated_at = ?
                WHERE id = ?
                """,
                (final_label, cookie, final_department, now, row["id"]),
            )
            account_id_value = row["id"]
        else:
            if requested_label:
                final_label = requested_label
            elif email:
                final_label = email
            else:
                count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
                final_label = f"账号{count + 1}"
            final_department = requested_department or ""
            cursor = conn.execute(
                """
                INSERT INTO accounts (label, cookie, email, department, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (final_label, cookie, email, final_department, now),
            )
            account_id_value = cursor.lastrowid

        saved = conn.execute(
            """
            SELECT label, cookie, email, department, updated_at
            FROM accounts WHERE id = ?
            """,
            (account_id_value,),
        ).fetchone()
        conn.commit()
        return _row_to_account(saved)
    except AccountsError:
        if conn:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn:
            conn.rollback()
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 写入失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def update_account_department(account_key: str, department: str) -> dict | None:
    """只更新账号所属部门，不要求用户重新提交 cookie。"""
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id FROM accounts
            WHERE (email IS NOT NULL AND email != '' AND email = ?)
               OR ((email IS NULL OR email = '') AND label = ?)
            """,
            (account_key, account_key),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        conn.execute(
            "UPDATE accounts SET department = ?, updated_at = ? WHERE id = ?",
            (department.strip(), int(time.time()), row["id"]),
        )
        saved = conn.execute(
            """
            SELECT label, cookie, email, department, updated_at
            FROM accounts WHERE id = ?
            """,
            (row["id"],),
        ).fetchone()
        conn.commit()
        return _row_to_account(saved)
    except AccountsError:
        if conn:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn:
            conn.rollback()
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 分组更新失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def delete_account(account_key: str) -> bool:
    """按公开账号标识原子删除；返回是否找到记录。"""
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            DELETE FROM accounts
            WHERE (email IS NOT NULL AND email != '' AND email = ?)
               OR ((email IS NULL OR email = '') AND label = ?)
            """,
            (account_key, account_key),
        )
        conn.execute("DELETE FROM snapshots WHERE account_id = ?", (account_key,))
        conn.commit()
        return cursor.rowcount > 0
    except AccountsError:
        if conn:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn:
            conn.rollback()
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 删除失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def account_id(acc: dict) -> str:
    """卡片和快照的账号标识：优先 email，没有就退回 label。"""
    return acc.get("email") or acc.get("label") or "unnamed"


# ---------- 刷新结果快照 ----------

def fingerprint(cookie: str) -> str:
    """cookie 的短哈希。快照表里绝不存 cookie 明文，只需要能判断"换过了"。"""
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()[:16]


def _row_to_snapshot(row: sqlite3.Row) -> dict:
    try:
        payload = json.loads(row["payload"]) if row["payload"] else None
    except json.JSONDecodeError:
        payload = None
    error = None
    if row["error_kind"]:
        error = {
            "kind": row["error_kind"],
            "message": row["error_message"] or "",
            "at": row["error_at"] or 0,
        }
    return {
        "fingerprint": row["fingerprint"],
        "data": payload,
        "ok_at": row["ok_at"] or 0,
        "error": error,
        "failures": row["failures"],
        "attempted_at": row["attempted_at"],
    }


def load_snapshots() -> dict[str, dict]:
    """启动时一次性读回内存；之后的读取都走内存，写入才回落盘。"""
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        rows = conn.execute("SELECT * FROM snapshots").fetchall()
        return {row["account_id"]: _row_to_snapshot(row) for row in rows}
    except AccountsError:
        raise
    except sqlite3.Error as exc:
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 快照读取失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def save_snapshot(ident: str, snapshot: dict) -> None:
    error = snapshot.get("error") or {}
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO snapshots (
                account_id, fingerprint, payload, ok_at,
                error_kind, error_message, error_at, failures, attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                payload = excluded.payload,
                ok_at = excluded.ok_at,
                error_kind = excluded.error_kind,
                error_message = excluded.error_message,
                error_at = excluded.error_at,
                failures = excluded.failures,
                attempted_at = excluded.attempted_at
            """,
            (
                ident,
                snapshot.get("fingerprint") or "",
                json.dumps(snapshot["data"], ensure_ascii=False)
                if snapshot.get("data") else None,
                int(snapshot.get("ok_at") or 0),
                error.get("kind"),
                error.get("message"),
                int(error.get("at") or 0) or None,
                int(snapshot.get("failures") or 0),
                int(snapshot.get("attempted_at") or 0),
            ),
        )
        conn.commit()
    except AccountsError:
        if conn:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn:
            conn.rollback()
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 快照写入失败：{exc}") from exc
    finally:
        if conn:
            conn.close()


def delete_snapshot(ident: str) -> None:
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        conn.execute("DELETE FROM snapshots WHERE account_id = ?", (ident,))
        conn.commit()
    except AccountsError:
        if conn:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn:
            conn.rollback()
        raise AccountsError(f"账号数据库 {DATABASE_PATH} 快照删除失败：{exc}") from exc
    finally:
        if conn:
            conn.close()
