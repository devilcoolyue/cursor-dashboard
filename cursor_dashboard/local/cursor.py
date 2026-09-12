"""Discover and validate local Cursor paths; switch without a shell or force quit."""
from __future__ import annotations

from contextlib import closing
import ntpath
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from ..domain.core import Conflict


def normalized_path(value, platform):
    value = str(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    value = (ntpath.expandvars(value) if platform == "windows" else os.path.expandvars(value))
    path = Path(value).expanduser()
    if not value or "\0" in value or not path.is_absolute():
        raise Conflict("请填写完整的本机绝对路径，可从 Cursor 快捷方式属性中复制目标路径。")
    return Path(os.path.abspath(path))


def process_data_dir(arguments):
    for index, argument in enumerate(arguments):
        if argument.startswith("--user-data-dir="):
            return argument.split("=", 1)[1]
        if argument == "--user-data-dir":
            if index + 1 < len(arguments):
                return arguments[index + 1]
            raise Conflict("Cursor 的用户数据启动参数不完整，请关闭后重新打开 Cursor。")
    return None


class CursorInstallation:
    def __init__(self, *, executable_path="", user_data_path=""):
        self.platform = "macos" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else "unsupported"
        self.executable_path = executable_path
        self.user_data_path = user_data_path
        self.refresh()

    def default_data(self):
        if self.platform == "macos":
            return Path.home() / "Library/Application Support/Cursor"
        if self.platform == "windows":
            return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Cursor"
        return None

    def resolve_executable(self, candidate):
        try:
            path = normalized_path(candidate, self.platform)
            if self.platform == "windows":
                if path.is_dir():
                    path /= "Cursor.exe"
                if path.name.lower() == "cursor.exe" and path.is_file() and (path.parent / "resources/app/package.json").is_file():
                    return path.parent, path
            elif self.platform == "macos":
                if path.suffix.lower() == ".app":
                    path /= "Contents/MacOS/Cursor"
                if (path.name == "Cursor" and path.is_file() and len(path.parents) >= 3
                        and (path.parents[2] / "Contents/Resources/app/bin/cursor").is_file()):
                    return path.parents[2], path
        except (OSError, ValueError, Conflict):
            pass
        return None

    def windows_process_candidates(self):
        import psutil
        for process in psutil.process_iter(["name", "exe"]):
            try:
                if (process.info["name"] or "").lower() == "cursor.exe" and process.info["exe"]:
                    yield process.info["exe"]
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue

    def candidates(self):
        if self.executable_path:
            yield "manual", self.executable_path
            return
        if os.environ.get("CURSOR_EXE"):
            yield "environment", os.environ["CURSOR_EXE"]
        if self.platform == "macos":
            for app in (Path("/Applications/Cursor.app"), Path.home() / "Applications/Cursor.app"):
                yield "default", app
        elif self.platform == "windows":
            from .cursor_windows import path_candidates, registry_candidates, shortcut_candidates
            for path in self.windows_process_candidates():
                yield "process", path
            for variable, relative in (("LOCALAPPDATA", "Programs/cursor"),
                                       ("ProgramFiles", "Cursor"), ("ProgramFiles(x86)", "Cursor"),
                                       ("ProgramW6432", "Cursor")):
                if os.environ.get(variable):
                    yield "default", Path(os.environ[variable]) / relative
            for source, discover in (("path", path_candidates), ("registry", registry_candidates), ("shortcut", shortcut_candidates)):
                try:
                    for path in discover():
                        yield source, path
                except OSError:
                    continue  # A stale/inaccessible source must not hide later sources.

    def portable_data(self):
        if self.platform == "windows" and self.app and (self.app / "data").is_dir():
            return self.app / "data/user-data"
        return None

    def running_data_dirs(self):
        """Only inspect main processes of the selected install; never expose their arguments."""
        import psutil
        paths = set()
        for process in psutil.process_iter(["name", "exe"]):
            try:
                expected = "cursor.exe" if self.platform == "windows" else "cursor"
                if (process.info["name"] or "").lower() != expected or not process.info["exe"]:
                    continue
                if Path(process.info["exe"]) != self.executable:
                    continue
                arguments = process.cmdline()
                if any(arg == "--type" or arg.startswith("--type=") for arg in arguments):
                    continue
                custom = process_data_dir(arguments)
                path = self.portable_data() or (normalized_path(custom, self.platform) if custom is not None else self.default_data())
                paths.add(path)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                raise Conflict("无法读取 Cursor 进程路径或启动参数，请关闭 Cursor 后重新检测。") from None
        return paths

    def refresh(self):
        self.app = self.executable = self.database = self.user_data = None
        self.source = self.discovery_error = None
        if self.platform == "unsupported":
            return
        try:
            for source, candidate in self.candidates():
                resolved = self.resolve_executable(candidate)
                if resolved:
                    self.app, self.executable = resolved
                    self.source = source
                    break
            # Installation and data discovery are independent so either missing path is visible.
            self.user_data = normalized_path(self.user_data_path, self.platform) if self.user_data_path else self.default_data()
            if self.executable:
                running_data = self.running_data_dirs()
                if len(running_data) > 1:
                    raise Conflict("检测到多个 Cursor 用户数据目录正在使用，请关闭其他 Cursor 窗口后重新检测。")
                portable = self.portable_data()
                if self.user_data_path:
                    if portable and self.user_data != portable:
                        raise Conflict("此 Cursor 使用便携数据目录，请填写安装目录下的 data/user-data。")
                else:
                    self.user_data = portable or next(iter(running_data), None) or self.default_data()
            if self.user_data:
                self.database = self.user_data / "User/globalStorage/state.vscdb"
        except Conflict as error:
            self.discovery_error = str(error)
        except OSError:
            self.discovery_error = "无法读取 Cursor 路径，请检查目录权限后重新检测。"

    def require(self):
        if self.platform == "unsupported":
            raise Conflict("本机切换目前支持 macOS 和 Windows。")
        if self.discovery_error:
            raise Conflict(self.discovery_error)
        if self.executable is None or not self.resolve_executable(self.executable):
            raise Conflict("未找到完整的 Cursor 安装，请重新检测或手动填写 Cursor 程序路径。")
        if self.database is None or not self.database.is_file():
            raise Conflict("已找到 Cursor 程序，但未找到用户数据库。请打开一次 Cursor，或填写实际使用的用户数据目录。")
        if self.database.is_symlink() or any(p.is_symlink() for p in self.database.parents):
            raise Conflict("Cursor 用户数据目录不能包含符号链接，请填写实际目录。")
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)) as database:
                database.execute("SELECT key, value FROM ItemTable LIMIT 1").fetchone()
        except sqlite3.Error:
            raise Conflict("Cursor 用户数据库不可读或格式不正确，请检查用户数据目录及访问权限。") from None

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
                        raise Conflict("另一处安装的 Cursor 正在运行，请关闭它后重新检测。")
                    processes.append(process.pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                raise Conflict("无法确认 Cursor 已完全退出，请手动关闭后重试。") from None
        if any(path != self.user_data for path in self.running_data_dirs()):
            raise Conflict("正在运行的 Cursor 使用了不同的用户数据目录，请关闭 Cursor 或重新检测。")
        return processes

    def detect(self):
        result = {"platform": self.platform, "available": False, "running": False, "reason": None,
                  "executable_path": str(self.executable) if self.executable else None,
                  "user_data_path": str(self.user_data) if self.user_data else None,
                  "database_path": str(self.database) if self.database else None,
                  "source": self.source,
                  "configured_executable_path": self.executable_path,
                  "configured_user_data_path": self.user_data_path}
        try:
            self.require()
            result.update(available=True, running=bool(self.running()))
        except Conflict as error:
            result["reason"] = str(error)
        except OSError:
            result["reason"] = "无法读取 Cursor 路径，请检查目录权限后重新检测。"
        return result

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
        # Keep custom data selection on restart, including --user-data-dir discovered from a running editor.
        if getattr(self, "user_data", None) and self.user_data != self.default_data() and not self.portable_data():
            command += (["--args"] if self.platform == "macos" else []) + ["--user-data-dir", str(self.user_data)]
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
