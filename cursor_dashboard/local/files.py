from __future__ import annotations

import os
from pathlib import Path
import subprocess

from ..domain.core import Conflict


def protect(path: Path):
    if path.is_symlink():
        raise Conflict("Symbolic links are not supported for private desktop files")
    if os.name == "nt":
        # Restrict the owned directory/file, including inheritance for new backups.
        account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
        grant = "(OI)(CI)F" if path.is_dir() else "F"
        result = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"{account}:{grant}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=15, creationflags=0x08000000)
        if result.returncode:
            raise Conflict("Cannot restrict private desktop file permissions")
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect(path)
    return path


def write_new(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        protect(path)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if fd is not None:
            os.close(fd)
        path.unlink(missing_ok=True)
        raise
