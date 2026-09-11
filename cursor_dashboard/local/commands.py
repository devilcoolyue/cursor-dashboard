"""Optional terminal execution with fixed, private local scripts; IPC returns no credentials."""
from __future__ import annotations

import shlex
import time
import uuid

from ..api.manual_switch import render_script
from .files import private_directory, write_new


class LocalCommands:
    def __init__(self, directory, *, preview=False):
        self.directory = private_directory(directory / "switch-scripts")
        self.preview = preview
        self.prune()

    def prune(self, *, all_files=False):
        for path in self.directory.iterdir():
            try:
                if path.suffix in {".sh", ".ps1"} and (all_files or path.stat().st_mtime + 300 <= time.time()):
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                # Terminal execution can remove a script while the scheduler prunes it.
                pass

    def render(self, delivery, platform):
        self.prune()
        script = render_script(delivery, platform, preview=self.preview)
        path = self.directory / (str(uuid.uuid4()) + (".sh" if platform == "macos" else ".ps1"))
        write_new(path, script["script"].encode("utf-8"))
        if platform == "macos":
            quoted = shlex.quote(str(path))
            # Keep Bash's input on a pipe, as in the Web download command (macOS Bash 3.2).
            command = (f"(set -o pipefail; /bin/cat {quoted} | /bin/bash; "
                       f"switch_result=$?; /bin/rm -f {quoted}; exit \"$switch_result\")")
        else:
            quoted = "'" + str(path).replace("'", "''") + "'"
            command = (f"& {{ try {{ & ([scriptblock]::Create([IO.File]::ReadAllText({quoted}))) }} "
                       f"finally {{ Remove-Item -LiteralPath {quoted} -Force -ErrorAction SilentlyContinue }} }}")
        return {"platform": platform, "expires_at": script["expires_at"], "command": command}
