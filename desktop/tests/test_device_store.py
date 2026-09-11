"""Only owned synthetic device-session entries are used in the OS credential store."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from cursor_dashboard.local.remote import DeviceStore


@unittest.skipUnless(sys.platform in {"darwin", "win32"}, "Native OS credential store requires macOS or Windows")
class DeviceStoreTest(unittest.TestCase):
    def test_owned_device_session_persists_across_processes_and_is_removed(self):
        with tempfile.TemporaryDirectory(prefix="p5-device-store-") as directory:
            row = str(uuid.uuid4())
            store = DeviceStore(Path(directory))
            value = {"token": secrets.token_urlsafe(32), "session_id": str(uuid.uuid4()),
                     "expires_at": time.time() + 60, "origin": "https://synthetic.example.test"}
            self.assertIsNone(store.read_session(row))
            try:
                store.save_session(row, value)
                # Same interpreter executable, including a separate process. No
                # credential in argv, stdout, logs, or a temporary file.
                script = ("import json,sys; from pathlib import Path; from cursor_dashboard.local.remote import DeviceStore; "
                          "p=json.load(sys.stdin); actual=DeviceStore(Path(p['directory'])).read_session(p['id']); "
                          "sys.exit(0 if actual==p['value'] else 1)")
                result = subprocess.run([sys.executable, "-c", script],
                    input=json.dumps({"directory": directory, "id": row, "value": value}),
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(result.returncode, 0, "Owned device credential was not readable after restart")
                self.assertEqual(result.stdout, "")
                self.assertNotIn(value["token"], result.stderr)
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                store.delete_session(row)
            self.assertIsNone(store.read_session(row))
