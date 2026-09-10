"""Real private HTTP and packaged OS key-store persistence using owned temporary data."""
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
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from cursor_dashboard.local.keys import SystemKeyStore

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform in {'darwin', 'win32'}, 'Native OS key store requires macOS or Windows')
class LocalBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cursor-p4-backend-')
        self.directory = Path(self.temp.name) / 'fixture-data'
        self.http = build_opener(ProxyHandler({}))
        self.addCleanup(self.cleanup)
        self.start()

    def start(self):
        binary = os.environ.get('P4_BACKEND_BINARY')
        command = [binary] if binary else [sys.executable, str(ROOT / 'sidecar/local_backend.py')]
        self.child = subprocess.Popen(command, cwd=self.temp.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.token = secrets.token_hex(32)
        self.child.stdin.write(json.dumps({'token': self.token, 'data_dir': str(self.directory), 'fixture': True}) + '\n')
        self.child.stdin.flush()
        messages = queue.Queue()
        threading.Thread(target=lambda: messages.put(self.child.stdout.readline()), daemon=True).start()
        line = messages.get(timeout=60)
        self.assertTrue(line, 'Backend did not provide readiness')
        self.ready = json.loads(line)
        self.base = f'http://127.0.0.1:{self.ready["port"]}'

    def stop(self):
        if self.child.stdin and not self.child.stdin.closed:
            self.child.stdin.close()
        try:
            self.child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
            raise AssertionError('Backend did not exit on parent EOF')
        finally:
            self.child.stdout.close()
            self.child.stderr.close()

    def cleanup(self):
        if self.child.poll() is None:
            self.stop()
        store = SystemKeyStore(self.directory)
        if store.read() is not None:
            store.backend().delete_password(store.service, store.account)
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        values = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', **(headers or {})}
        values = {key: value for key, value in values.items() if value is not None}
        try:
            response = self.http.open(Request(self.base + path, method=method,
                data=json.dumps(body).encode() if body is not None else None, headers=values), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            data = response.read()
            return response.status, json.loads(data) if data else None

    def test_os_key_store_core_snapshot_and_private_boundary(self):
        self.assertEqual(self.request('/native/status')[1]['phase'], 'ready')
        self.assertEqual(self.request('/api/v1/bootstrap', headers={'Authorization': None})[0], 401)
        self.assertEqual(self.request('/api/v1/bootstrap', headers={'Origin': 'http://evil.test'})[0], 403)
        self.assertEqual(self.request('/api/v1/bootstrap', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.request('/api/v1/auth/login', 'POST', {})[0], 404)
        identity = self.request('/api/v1/me')[1]
        workspace = identity['workspaces'][0]['id']
        rows = self.request(f'/api/v1/workspaces/{workspace}/accounts')[1]['items']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['data'] for row in rows))
        self.assertNotIn('fixture-cookie', json.dumps(rows))
        self.assertEqual(self.ready['frozen'], bool(os.environ.get('P4_BACKEND_BINARY')))
        self.assertTrue(SystemKeyStore(self.directory).read())
        self.stop()
        self.start()
        self.assertEqual(self.request('/api/v1/me')[1]['id'], identity['id'])
        self.assertEqual(self.request(f'/api/v1/workspaces/{workspace}/accounts')[1]['items'], rows)
        self.stop()
        self.assertEqual(self.child.returncode, 0)

    def test_encrypted_archive_and_fixture_switch_over_actual_http(self):
        workspace = self.request('/api/v1/me')[1]['workspaces'][0]['id']
        rows = self.request(f'/api/v1/workspaces/{workspace}/accounts')[1]['items']
        path = self.directory / 'fixture.cursorarchive'
        status, body = self.request('/native/archive/export', 'POST', {'workspace_id': workspace,
            'path': str(path), 'password': 'fixture archive 42'})
        self.assertEqual(status, 200)
        self.assertEqual(body['count'], 2)
        self.assertNotIn(b'fixture-cookie', path.read_bytes())
        status, body = self.request('/native/switch', 'POST', {'workspace_id': workspace,
            'account_id': rows[0]['id'], 'confirmed': True})
        self.assertEqual(status, 200)
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = self.request('/native/switch')[1]
            if not state['busy']:
                break
            time.sleep(.1)
        self.assertEqual(state['stage'], 'complete')
        self.assertTrue(self.request('/native/backups')[1])
        self.stop()

if __name__ == '__main__':
    unittest.main()
