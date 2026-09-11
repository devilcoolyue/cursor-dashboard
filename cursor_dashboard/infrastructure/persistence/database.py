from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...domain.core import CoreError, Conflict


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", hide_parameters=True,
                                    connect_args={"timeout": 10, "check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def configure(connection, _record):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            mode = connection.get_execution_options().get("sqlite_txn_mode", "DEFERRED")
            connection.exec_driver_sql("BEGIN IMMEDIATE" if mode == "IMMEDIATE" else "BEGIN")

    def alembic_config(self):
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
        return config

    def upgrade(self):
        with self.engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if tables and "alembic_version" not in tables:
                raise Conflict("Target is not a V2 database; use the legacy import command")
        with self.engine.connect().execution_options(sqlite_txn_mode="IMMEDIATE") as connection:
            with connection.begin():
                config = self.alembic_config()
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        # WAL is changed outside a transaction and only by explicit upgrade/init.
        raw = self.engine.raw_connection()
        try:
            raw.execute("PRAGMA journal_mode=WAL")
        finally:
            raw.close()
        self.protect_files()

    def require_current(self):
        with self.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        if current != ScriptDirectory.from_config(self.alembic_config()).get_current_head():
            raise CoreError("Database schema is not current; stop the runtime and run upgrade")

    def protect_files(self):
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.exists():
                os.chmod(path, 0o600)

    @contextmanager
    def transaction(self, *, write=False):
        try:
            with self.engine.connect().execution_options(sqlite_txn_mode="IMMEDIATE" if write else "DEFERRED") as conn:
                with conn.begin(), Session(bind=conn, expire_on_commit=False) as session:
                    yield session
                    session.flush()
            if write:
                self.protect_files()
        except SQLAlchemyError:
            raise Conflict("Database operation failed; check resource constraints and storage") from None

    def close(self):
        self.engine.dispose()
