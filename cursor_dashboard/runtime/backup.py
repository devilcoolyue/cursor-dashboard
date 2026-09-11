"""Offline consistent database backup and restore; keys travel separately."""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from sqlalchemy import update

from ..domain.core import CoreError
from ..infrastructure.persistence.models import SwitchTicket, UserSession
from ..infrastructure.persistence.policy import audit
from .core import Core
from .lock import RuntimeLock


def copy_database(source, destination):
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        # sqlite3's transaction context does not close connections. Release both
        # handles before restore removes staging files, including on Windows.
        with closing(sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CoreError('Backup integrity check failed')
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise


def backup(core, destination):
    destination = Path(destination).resolve()
    report = core.repository.verify()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    copy_database(core.config.database, destination / 'core.db')
    (destination / 'manifest.json').write_text(json.dumps({'format': 1, **report}) + '\n', encoding='utf-8')
    return {'backup_created': True, 'accounts': report['accounts'], 'key_backup_required': True}


def restore(config, source):
    source = Path(source).resolve()
    if not (source / 'core.db').is_file():
        raise CoreError('Backup database is missing')
    config.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with RuntimeLock(config.lock_file):
        if any(p != config.lock_file for p in config.data_dir.iterdir()):
            raise CoreError('Restore requires an empty target data directory')
        with tempfile.TemporaryDirectory(prefix='cursor-restore-') as temporary:
            staging = replace(config, data_dir=Path(temporary))
            copy_database(source / 'core.db', staging.database)
            with Core(staging) as core:
                report = core.repository.verify()
                # Restored server sessions must never resurrect revoked access.
                with core.db.transaction(write=True) as session:
                    session.execute(update(UserSession).values(revoked=True))
                    session.execute(update(SwitchTicket).values(consumed_at=0))
                    audit(session, None, 'operator.restore')
            copy_database(staging.database, config.database)
        return {'restored': True, 'accounts': report['accounts'], 'sessions_revoked': True}
