from __future__ import annotations

import os
from pathlib import Path

from ..domain.core import Locked


class RuntimeLock:
    """Shared by V2 runtime, maintenance CLI and schema upgrades. OS releases on crash."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(fd, "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise Locked("V2 data directory is in use; stop the runtime before maintenance") from None
        self.stream = stream
        return self

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()
