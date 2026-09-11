from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from cursor_dashboard.local.commands import LocalCommands


class LocalCommandTest(unittest.TestCase):
    def check_execution(self, platform, shell):
        with tempfile.TemporaryDirectory(prefix="cursor-command-") as temporary:
            commands = LocalCommands(Path(temporary) / "space and 'quote")
            for fail in (False, True):
                if platform == 'macos':
                    script = "printf 'fixture script\\n'\n" + ('exit 7\n' if fail else '')
                else:
                    script = "Write-Output 'fixture script'\n" + ("throw 'fixture failure'\n" if fail else '')
                with patch('cursor_dashboard.local.commands.render_script', return_value={
                        'script': script, 'expires_at': int(time.time()) + 300}):
                    command = commands.render(None, platform)['command']
                flag = '-c' if platform == 'macos' else '-Command'
                result = subprocess.run([shell, flag, command], capture_output=True, text=True, timeout=10)
                self.assertIn('fixture script', result.stdout)
                self.assertEqual(result.returncode != 0, fail, result.stderr)
                self.assertEqual(list(commands.directory.iterdir()), [])
                missing = subprocess.run([shell, flag, command], capture_output=True, text=True, timeout=10)
                self.assertNotEqual(missing.returncode, 0)

    @unittest.skipUnless(shutil.which('bash'), 'Bash required')
    def test_bash_quoted_path_success_failure_and_cleanup(self):
        self.check_execution('macos', shutil.which('bash'))

    @unittest.skipUnless(shutil.which('zsh'), 'Zsh required')
    def test_zsh_quoted_path_success_failure_and_cleanup(self):
        self.check_execution('macos', shutil.which('zsh'))

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell required')
    def test_powershell_quoted_path_success_failure_and_cleanup(self):
        self.check_execution('windows', shutil.which('pwsh'))
