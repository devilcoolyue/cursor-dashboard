"""Read Windows installation hints without executing discovered commands or shortcuts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


def path_candidates():
    # Check every PATH entry: an old CLI wrapper must not hide a newer install.
    for directory in os.get_exec_path():
        if not directory:
            continue
        for name in ("Cursor.exe", "cursor", "cursor.cmd"):
            path = Path(directory.strip('"')) / name
            try:
                if path.is_file():
                    yield path
                    # <install>/resources/app/bin/cursor.cmd
                    if name != "Cursor.exe" and len(path.parents) >= 4:
                        yield path.parents[3] / "Cursor.exe"
            except OSError:
                continue


def registry_candidates():
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            base = r"Software\Microsoft\Windows\CurrentVersion"
            try:
                with winreg.OpenKey(hive, base + r"\App Paths\Cursor.exe", 0, winreg.KEY_READ | view) as key:
                    yield winreg.QueryValueEx(key, "")[0]
            except OSError:
                pass
            try:
                with winreg.OpenKey(hive, base + r"\Uninstall", 0, winreg.KEY_READ | view) as uninstall:
                    for index in range(winreg.QueryInfoKey(uninstall)[0]):
                        try:
                            name = winreg.EnumKey(uninstall, index)
                            with winreg.OpenKey(uninstall, name) as key:
                                display = winreg.QueryValueEx(key, "DisplayName")[0]
                                if not isinstance(display, str) or not re.match(r"^Cursor(?:\s|$)", display, re.I):
                                    continue
                                for value in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        candidate = winreg.QueryValueEx(key, value)[0]
                                        if isinstance(candidate, str):
                                            yield re.sub(r",\s*-?\d+\s*$", "", candidate) if value == "DisplayIcon" else candidate
                                    except OSError:
                                        pass
                        except OSError:
                            continue
            except OSError:
                pass


# Fixed script: paths supplied by the user are never interpolated into PowerShell.
# Special folders include redirected/OneDrive desktops and per-machine shortcuts.
SHORTCUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$shell = $null
try {
    $shell = New-Object -ComObject WScript.Shell
    $targets = @(foreach ($name in @('DesktopDirectory', 'CommonDesktopDirectory', 'Programs', 'CommonPrograms')) {
        $folder = [Environment]::GetFolderPath($name)
        if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
        $recursive = $name -in @('Programs', 'CommonPrograms')
        foreach ($link in @(Get-ChildItem -LiteralPath $folder -Filter '*Cursor*.lnk' -File -Recurse:$recursive -ErrorAction SilentlyContinue)) {
            $shortcut = $null
            try {
                $shortcut = $shell.CreateShortcut($link.FullName)
                $shortcut.TargetPath
            } catch {} finally {
                if ($shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
            }
        }
    })
    ConvertTo-Json -InputObject $targets -Compress
} finally {
    if ($shell) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) }
}
"""


def shortcut_candidates():
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return []
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_SCRIPT],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return []
        targets = json.loads(result.stdout.decode("utf-8-sig"))
        return [path for path in targets if isinstance(path, str)] if isinstance(targets, list) else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
