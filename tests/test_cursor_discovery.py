from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil

from cursor_dashboard.local.cursor import CursorInstallation
from cursor_dashboard.local.cursor_windows import path_candidates, registry_candidates, shortcut_candidates


class CursorDiscoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cursor-discovery-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(patch('cursor_dashboard.local.cursor.sys', SimpleNamespace(platform='win32')))
        self.enterContext(patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'local'),
            'APPDATA': str(self.root / 'roaming'), 'ProgramFiles': str(self.root / 'programs'),
            'ProgramFiles(x86)': str(self.root / 'programs-x86'), 'ProgramW6432': str(self.root / 'programs'), 'CURSOR_EXE': ''}))
        self.processes = self.enterContext(patch('psutil.process_iter', return_value=[]))
        self.paths = self.enterContext(patch('cursor_dashboard.local.cursor_windows.path_candidates', return_value=[]))
        self.registry = self.enterContext(patch('cursor_dashboard.local.cursor_windows.registry_candidates', return_value=[]))
        self.shortcuts = self.enterContext(patch('cursor_dashboard.local.cursor_windows.shortcut_candidates', return_value=[]))

    def install(self, directory='D 盘/软件/Cursor'):
        executable = self.root / directory / 'Cursor.exe'
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        package = executable.parent / 'resources/app/package.json'
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text('{}')
        return executable

    def data(self, directory='roaming/Cursor'):
        data = self.root / directory
        database = data / 'User/globalStorage/state.vscdb'
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value BLOB)')
        return data

    def process(self, executable, *arguments):
        return SimpleNamespace(info={'name': 'Cursor.exe', 'exe': str(executable)},
                               cmdline=Mock(return_value=[str(executable), *arguments]), pid=123)

    def test_running_custom_install_beats_stale_default_and_preserves_custom_data(self):
        default = self.install('local/Programs/cursor')
        executable = self.install()
        data = self.data('工作 数据/Cursor')
        self.processes.return_value = [self.process(executable, '--user-data-dir', str(data))]
        installation = CursorInstallation()
        result = installation.detect()
        self.assertTrue(result['available'], result)
        self.assertTrue(result['running'])
        self.assertEqual(result['source'], 'process')
        self.assertNotEqual(installation.executable, default)
        self.assertEqual(installation.executable, executable)
        self.assertEqual(installation.database, data / 'User/globalStorage/state.vscdb')
        with patch.object(installation, 'running', return_value=[123]), patch('subprocess.Popen') as launch:
            installation.restart()
        self.assertEqual(launch.call_args.args[0], [str(executable), '--user-data-dir', str(data)])

    def test_path_registry_and_shortcuts_skip_incomplete_and_stale_candidates(self):
        valid = self.install()
        self.data()
        incomplete = self.root / 'incomplete/Cursor.exe'
        incomplete.parent.mkdir(); incomplete.touch()
        for source, provider in (('path', self.paths), ('registry', self.registry), ('shortcut', self.shortcuts)):
            with self.subTest(source=source):
                self.paths.return_value = self.registry.return_value = self.shortcuts.return_value = []
                provider.return_value = [self.root / 'gone/Cursor.exe', incomplete, valid.parent]
                result = CursorInstallation().detect()
                self.assertTrue(result['available'], result)
                self.assertEqual(result['source'], source)

    def test_refresh_finds_cursor_opened_after_panel_started(self):
        installation = CursorInstallation()
        self.assertFalse(installation.detect()['available'])
        executable = self.install()
        self.data()
        self.processes.return_value = [self.process(executable)]
        installation.refresh()
        self.assertTrue(installation.detect()['available'])

    def test_manual_paths_accept_quoted_directories_environment_spaces_and_unicode(self):
        executable = self.install()
        data = self.data()
        installation = CursorInstallation(executable_path=f'"{executable.parent}"', user_data_path='%APPDATA%/Cursor')
        self.assertTrue(installation.detect()['available'])
        self.assertEqual(installation.executable, executable)
        self.assertEqual(installation.user_data, data)
        self.processes.assert_called()

    def test_manual_invalid_path_never_falls_back_to_another_install(self):
        self.install('local/Programs/cursor'); self.data()
        for path in ('relative/Cursor.exe', str(self.root / 'missing'), str(self.root / 'bad\0.exe')):
            with self.subTest(path=path):
                installation = CursorInstallation(executable_path=path)
                self.assertFalse(installation.detect()['available'])
                self.assertIsNone(installation.executable)

    def test_missing_database_is_distinct_and_read_only_validation_never_creates_it(self):
        executable = self.install()
        installation = CursorInstallation(executable_path=str(executable))
        result = installation.detect()
        self.assertFalse(result['available'])
        self.assertIn('用户数据库', result['reason'])
        self.assertEqual(result['executable_path'], str(executable))
        self.assertFalse(installation.database.exists())
        data = self.data()
        with closing(sqlite3.connect(data / 'User/globalStorage/state.vscdb')) as database:
            database.execute('DROP TABLE ItemTable')
        self.assertIn('格式不正确', installation.detect()['reason'])

    def test_portable_install_never_falls_back_to_default_database(self):
        executable = self.install(); self.data()
        (executable.parent / 'data').mkdir()
        installation = CursorInstallation(executable_path=str(executable))
        self.assertFalse(installation.detect()['available'])
        self.assertEqual(installation.user_data, executable.parent / 'data/user-data')
        portable = self.data('D 盘/软件/Cursor/data/user-data')
        installation.refresh()
        self.assertTrue(installation.detect()['available'])
        self.assertEqual(installation.user_data, portable)
        result = CursorInstallation(executable_path=str(executable), user_data_path=str(self.root / 'roaming/Cursor')).detect()
        self.assertFalse(result['available'])
        self.assertIn('便携', result['reason'])

    def test_multiple_installations_or_data_directories_block_switching(self):
        first = self.install(); second = self.install('another/Cursor')
        data = self.data(); other_data = self.data('another-data')
        for processes in ([self.process(first), self.process(second)],
                          [self.process(first), self.process(first, '--user-data-dir=' + str(other_data))]):
            with self.subTest(processes=len(processes)):
                self.processes.return_value = processes
                self.assertFalse(CursorInstallation().detect()['available'])
        self.processes.return_value = [self.process(first, '--user-data-dir=' + str(other_data))]
        installation = CursorInstallation(executable_path=str(first), user_data_path=str(data))
        self.assertFalse(installation.detect()['available'])

    def test_inaccessible_process_cannot_trigger_quit_or_data_writes(self):
        executable = self.install(); self.data()
        process = self.process(executable)
        process.cmdline.side_effect = psutil.AccessDenied(123)
        self.processes.return_value = [process]
        result = CursorInstallation().detect()
        self.assertFalse(result['available'])
        self.assertIn('启动参数', result['reason'])

    def test_symbolic_data_directory_is_rejected(self):
        executable = self.install(); data = self.data()
        link = self.root / 'linked-data'
        try:
            link.symlink_to(data, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows runner cannot create symbolic links')
        result = CursorInstallation(executable_path=str(executable), user_data_path=str(link)).detect()
        self.assertFalse(result['available'])
        self.assertIn('符号链接', result['reason'])

    def test_macos_manual_bundle_and_custom_data_restart(self):
        with patch('cursor_dashboard.local.cursor.sys', SimpleNamespace(platform='darwin')):
            app = self.root / '软件/Cursor.app'
            for relative in ('Contents/MacOS/Cursor', 'Contents/Resources/app/bin/cursor'):
                path = app / relative; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            data = self.data('mac-data')
            installation = CursorInstallation(executable_path=str(app), user_data_path=str(data))
            self.assertTrue(installation.detect()['available'])
            with patch.object(installation, 'running', return_value=[123]), patch('subprocess.Popen') as launch:
                installation.restart()
            self.assertEqual(launch.call_args.args[0], ['/usr/bin/open', '-a', str(app), '--args', '--user-data-dir', str(data)])


class WindowsDiscoverySourcesTest(unittest.TestCase):
    def test_path_cli_wrappers_resolve_every_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            expected, directories = [], []
            for name in ('old', 'new'):
                install = Path(directory) / name
                wrapper = install / 'resources/app/bin/cursor.cmd'
                wrapper.parent.mkdir(parents=True); wrapper.touch()
                directories.append(str(wrapper.parent))
                expected.extend([wrapper, install / 'Cursor.exe'])
            with patch('os.get_exec_path', return_value=directories):
                self.assertEqual(list(path_candidates()), expected)

    def test_shortcut_json_keeps_unicode_and_does_not_execute_targets(self):
        target = 'D:\\软件 Space\\Cursor\\Cursor.exe'
        with patch.dict(os.environ, {'SystemRoot': 'C:\\Windows'}), patch('subprocess.run') as run:
            run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps([target], ensure_ascii=False).encode('utf-8-sig'))
            self.assertEqual(shortcut_candidates(), [target])
            self.assertNotIn(target, run.call_args.args[0])
            self.assertFalse(run.call_args.kwargs.get('shell', False))
            run.side_effect = subprocess.TimeoutExpired('powershell', 8)
            self.assertEqual(shortcut_candidates(), [])

    def test_registry_reads_both_views_skips_unrelated_and_inaccessible_entries(self):
        from contextlib import nullcontext
        base = r'Software\Microsoft\Windows\CurrentVersion'
        def open_key(hive, path, *args):
            if hive == 'HKLM':
                raise PermissionError()
            return nullcontext(path)
        def value(key, name):
            values = {(base + r'\App Paths\Cursor.exe', ''): r'D:\Cursor\Cursor.exe',
                      ('cursor', 'DisplayName'): 'Cursor (User)', ('cursor', 'InstallLocation'): r'D:\Cursor',
                      ('cursor', 'DisplayIcon'): r'"D:\Cursor\Cursor.exe",0', ('other', 'DisplayName'): 'Other app'}
            if (key, name) not in values:
                raise OSError()
            return values[key, name], 1
        registry = SimpleNamespace(HKEY_CURRENT_USER='HKCU', HKEY_LOCAL_MACHINE='HKLM',
            KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, KEY_READ=1,
            OpenKey=Mock(side_effect=open_key), QueryValueEx=value, QueryInfoKey=lambda key: (3,),
            EnumKey=lambda key, index: ['cursor', 'other', 'gone'][index])
        with patch.dict('sys.modules', {'winreg': registry}):
            candidates = list(registry_candidates())
        self.assertEqual(candidates, [r'D:\Cursor\Cursor.exe', r'D:\Cursor', r'"D:\Cursor\Cursor.exe"'] * 2)
        self.assertEqual({call.args[3] for call in registry.OpenKey.call_args_list if len(call.args) == 4}, {257, 513})


if __name__ == '__main__':
    unittest.main()
