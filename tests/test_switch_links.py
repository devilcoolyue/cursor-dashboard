from __future__ import annotations

import shutil
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cursor_dashboard.switch_links import SwitchLinks, download_command


class LinkStoreTest(unittest.TestCase):
    def setUp(self):
        self.links = SwitchLinks()
        self.result = {"expires_at": int(time.time()) + 60,
                       "commands": {"macos": {"script": "exit 1"}, "windows": {"script": "throw 'preview'"}}}

    def test_capacity_expiry_and_atomic_consumption(self):
        with patch("cursor_dashboard.switch_links.MAX_LINKS", 1):
            token, expiry = self.links.issue("test", {}, self.result)
            self.assertEqual(expiry, self.result["expires_at"])
            with self.assertRaises(OverflowError):
                self.links.issue("test", {}, self.result)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.links.consume(token, "macos"), range(8)))
            self.assertEqual(sum(item is not None for item in results), 1)
            token, _ = self.links.issue("test", {}, self.result)
            with patch("cursor_dashboard.switch_links.time.time", return_value=expiry):
                self.links.prune()
            self.assertIsNone(self.links.consume(token, "macos"))
            self.links.issue("test", {}, self.result)

    def test_expired_credentials_cannot_issue_link(self):
        self.result["expires_at"] = 1
        with self.assertRaises(ValueError):
            self.links.issue("test", {}, self.result)


class DownloadCommandTest(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                windows = self.path.startswith("/windows/")
                body = ("Write-Output '下载成功 中文'" if windows else "printf '下载成功 中文\\n'").encode()
                failure = self.path.endswith("/error")
                self.send_response(403 if failure else 200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body) + (50 if self.path.endswith("/partial") else 0)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.http.shutdown)
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def check_downloads(self, platform, shell):
        for endpoint in ("ok", "error", "partial"):
            with self.subTest(endpoint=endpoint, shell=shell):
                command = download_command(f"{self.base}/{platform}/{endpoint}", platform)
                result = subprocess.run([shell, "-c" if platform == "macos" else "-Command", command],
                                        text=True, capture_output=True, timeout=10)
                if endpoint == "ok":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("下载成功 中文", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("下载成功", result.stdout)

    @unittest.skipUnless(shutil.which("curl") and shutil.which("bash"), "Bash and curl required")
    def test_bash_download_success_http_failure_and_partial_transfer(self):
        self.check_downloads("macos", shutil.which("bash"))

    @unittest.skipUnless(shutil.which("curl") and shutil.which("zsh"), "Zsh and curl required")
    def test_zsh_download_success_http_failure_and_partial_transfer(self):
        self.check_downloads("macos", shutil.which("zsh"))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required")
    def test_powershell_download_success_http_failure_and_partial_transfer(self):
        self.check_downloads("windows", shutil.which("pwsh"))
