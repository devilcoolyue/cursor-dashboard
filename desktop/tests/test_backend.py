from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request


ROOT = Path(__file__).resolve().parents[1]


class BackendContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p0-contract-")
        self.addCleanup(self.temp.cleanup)
        binary = os.environ.get("P0_BACKEND_BINARY")
        command = [binary] if binary else [sys.executable, str(ROOT / "sidecar" / "backend.py")]
        self.child = subprocess.Popen(command, cwd=self.temp.name, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.cleanup)
        self.token = secrets.token_hex(32)
        self.child.stdin.write(json.dumps({"token": self.token}) + "\n")
        self.child.stdin.flush()
        messages = queue.Queue()
        threading.Thread(target=lambda: messages.put(self.child.stdout.readline()), daemon=True).start()
        self.ready = json.loads(messages.get(timeout=30))
        self.base = f'http://127.0.0.1:{self.ready["port"]}'
        self.http = build_opener(ProxyHandler({}))

    def cleanup(self):
        if self.child.stdin and not self.child.stdin.closed:
            self.child.stdin.close()
        try:
            self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
        self.child.stdout.close()
        self.child.stderr.close()

    def request(self, path="/api/v1/health", *, auth=True, headers=None):
        values = {"Authorization": f"Bearer {self.token}"} if auth else {}
        values.update(headers or {})
        try:
            response = self.http.open(Request(self.base + path, headers=values), timeout=3)
        except HTTPError as response:
            return response.code, json.loads(response.read())
        with response:
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            return response.status, json.loads(response.read())

    def test_packaged_fixture_and_sqlite_work_outside_source_directory(self):
        status, health = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(health["api_version"], 1)
        self.assertEqual(health["pid"], self.ready["pid"])
        self.assertEqual(health["frozen"], bool(os.environ.get("P0_BACKEND_BINARY")))
        status, demo = self.request("/api/v1/demo")
        self.assertEqual(status, 200)
        self.assertEqual(demo["fixture"], "p0-bundled-demo-v1")
        self.assertEqual(len(demo["accounts"]), 2)
        self.assertTrue(all(a["email"].endswith("@example.test") for a in demo["accounts"]))

    def test_auth_host_origin_and_unexposed_routes(self):
        self.assertEqual(self.request(auth=False)[0], 401)
        self.assertEqual(self.request(headers={"Authorization": "Bearer incorrect"})[0], 401)
        self.assertEqual(self.request(headers={"Host": "attacker.test"})[0], 403)
        for origin in ("null", "http://127.0.0.1", "https://attacker.test"):
            self.assertEqual(self.request(headers={"Origin": origin})[0], 403)
        self.assertEqual(self.request(headers={"Sec-Fetch-Site": "same-origin"})[0], 403)
        self.assertEqual(self.request("/docs")[0], 404)
        self.assertEqual(self.request("/api/v1/switch")[0], 404)

    def test_parent_pipe_closure_exits_and_removes_listener(self):
        self.child.stdin.close()
        self.assertEqual(self.child.wait(timeout=5), 0)
        with self.assertRaises((URLError, ConnectionError, TimeoutError)):
            self.http.open(self.base + "/api/v1/health", timeout=1)


if __name__ == "__main__":
    unittest.main()
