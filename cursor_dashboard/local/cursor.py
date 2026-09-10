"""Fixed macOS/Windows Cursor adapter. No custom paths, shell, or force quit."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from ..domain.core import Conflict


class CursorInstallation:
    def __init__(self):
        user_dir = Path.home()
        self.platform = "macos" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else "unsupported"
        self.app = self.executable = self.database = None
        if self.platform == "macos":
            self.database = user_dir / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
            for app in (Path("/Applications/Cursor.app"), user_dir / "Applications/Cursor.app"):
                executable = app / "Contents/MacOS/Cursor"
                if executable.is_file():
                    self.app, self.executable = app, executable
                    break
        elif self.platform == "windows":
            appdata = os.environ.get("APPDATA")
            if appdata:
                self.database = Path(appdata) / "Cursor/User/globalStorage/state.vscdb"
            candidates = []
            for variable, relative in (("LOCALAPPDATA", "Programs/cursor"),
                                       ("ProgramFiles", "Cursor"), ("ProgramFiles(x86)", "Cursor")):
                if os.environ.get(variable):
                    candidates.append(Path(os.environ[variable]) / relative / "Cursor.exe")
            for executable in candidates:
                if executable.is_file() and (executable.parent / "resources/app/package.json").is_file():
                    self.app, self.executable = executable.parent, executable
                    break

    def require(self):
        if self.platform == "unsupported":
            raise Conflict("Native switching requires macOS or Windows")
        if self.executable is None or self.database is None or not self.database.is_file():
            raise Conflict("Install and open Cursor once in the default user directory")
        if self.database.is_symlink() or any(p.is_symlink() for p in self.database.parents):
            raise Conflict("Custom or symbolic-link Cursor data directories are not supported")

    def running(self):
        import psutil
        processes = []
        for process in psutil.process_iter(["name", "exe"]):
            try:
                name = (process.info["name"] or "").lower()
                executable = process.info["exe"]
                # Include helpers, which can still hold SQLite connections after the main window closes.
                if name == "cursor" or name == "cursor.exe" or name.startswith("cursor helper"):
                    if not executable or self.app is None or not Path(executable).is_relative_to(self.app):
                        raise Conflict("Another Cursor installation is running; close it before switching")
                    processes.append(process.pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                raise Conflict("Cannot verify that Cursor is fully stopped") from None
        return processes

    def detect(self):
        try:
            self.require()
            return {"platform": self.platform, "available": True, "running": bool(self.running()), "reason": None}
        except Conflict as error:
            return {"platform": self.platform, "available": False, "running": False, "reason": str(error)}

    def quit(self, timeout=30):
        self.require()
        pids = self.running()
        if not pids:
            return
        helper = None
        try:
            if self.platform == "macos":
                helper = subprocess.Popen(["/usr/bin/osascript", "-e", "on run argv", "-e",
                    "tell application (item 1 of argv) to quit", "-e", "end run", str(self.app)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

                @callback_type
                def close_window(window, _):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
                    if pid.value in pids:
                        user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE permits save/cancel.
                    return True
                user32.EnumWindows(close_window, 0)
            deadline = time.monotonic() + timeout
            while self.running():
                if time.monotonic() >= deadline or (helper and helper.poll() not in (None, 0)):
                    raise Conflict("Cursor did not exit; save work and close it manually before retrying")
                time.sleep(.2)
        finally:
            if helper:
                if helper.poll() is None:
                    helper.kill()  # Only our AppleScript helper, never Cursor.
                helper.wait()

    def ensure_stopped(self):
        if self.running():
            raise Conflict("Cursor reopened during switching; no further writes were made")

    def restart(self):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"ELECTRON_RUN_AS_NODE", "VSCODE_IPC_HOOK_CLI", "PYTHONHOME", "PYTHONPATH"}}
        command = (["/usr/bin/open", "-a", str(self.app)] if self.platform == "macos" else [str(self.executable)])
        child = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.running():
                return
            if child.poll() not in (None, 0):
                break
            time.sleep(.2)
        raise Conflict("Login was written but Cursor did not restart; open Cursor or restore the backup")
