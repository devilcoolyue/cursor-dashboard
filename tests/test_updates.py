from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cursor_dashboard.api.app import create_app
from cursor_dashboard.updates import releases
from cursor_dashboard.updates.agent import Agent
from cursor_dashboard.updates.control import UpdateControl, atomic_json
from test_identity import IdentityFixture
from test_v2_api import APIClient


def signing_fixture(data):
    key = Ed25519PrivateKey.generate()
    key_id = b"test-key"
    public = b"Ed" + key_id + key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signature = key.sign(hashlib.blake2b(data).digest())
    packet = b"ED" + key_id + signature
    comment = b"timestamp:1"
    encode = lambda value: base64.b64encode(value).decode()
    pub = encode(("untrusted comment: test\n" + encode(public) + "\n").encode())
    signed = encode(("untrusted comment: test\n" + encode(packet) + "\ntrusted comment: "
                     + comment.decode() + "\n" + encode(key.sign(signature + comment)) + "\n").encode())
    return pub, signed


class ReleaseTest(unittest.TestCase):
    def test_native_and_server_trust_the_same_public_key(self):
        config = Path(__file__).resolve().parents[1] / "desktop/src-tauri/tauri.conf.json"
        self.assertEqual(json.loads(config.read_text())["plugins"]["updater"]["pubkey"], releases.PUBLIC_KEY)

    def test_signed_manifest_rejects_an_untrusted_download_origin(self):
        manifest = {"format": 1, "api_version": 1, "version": "0.0.2", "target": "linux-amd64",
                    "image_id": "sha256:" + "a" * 64, "artifact": {"name": "image.tar", "size": 10,
                    "sha256": "b" * 64, "url": "https://untrusted.test/image.tar"}}
        raw = json.dumps(manifest).encode()
        public, signature = signing_fixture(raw)
        verify = releases.verify_signature
        with patch.object(releases, "download_metadata", side_effect=[raw, signature.encode()]), \
             patch.object(releases, "verify_signature", side_effect=lambda data, sig: verify(data, sig, public)):
            with self.assertRaises(releases.UpdateError):
                releases.server_manifest("0.0.2")

    def test_signature_rejects_tampering_and_a_different_signer(self):
        data = b'{"version":"1.2.3"}'
        public, signature = signing_fixture(data)
        releases.verify_signature(data, signature, public)
        for payload, key in ((data + b" ", public), (data, signing_fixture(data)[0])):
            with self.assertRaises(releases.UpdateError):
                releases.verify_signature(payload, signature, key)
        damaged = base64.b64decode(signature).replace(b"timestamp:1", b"timestamp:2")
        with self.assertRaises(releases.UpdateError):
            releases.verify_signature(data, base64.b64encode(damaged).decode(), public)

    def test_version_comparison_is_numeric_and_never_downgrades(self):
        for latest, expected in (("0.0.10", True), ("0.0.2", False), ("0.0.1", False)):
            with patch.object(releases, "latest_release", return_value={"version": latest, "notes": "", "release_url": "", "published_at": None}):
                self.assertEqual(releases.check_release("0.0.2")["available"], expected)
        for bad in ("latest", "1.0", "1.0.0;rm", "01.0.0", "1.0.0-beta.1"):
            with self.assertRaises(releases.UpdateError):
                releases.version_tuple(bad)

    def test_queue_is_exclusive_and_requires_a_live_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = UpdateControl(root)
            with self.assertRaises(releases.UpdateError):
                control.enqueue("1.0.0")
            atomic_json(root / "agent.json", {"heartbeat": time.time()})
            first = control.enqueue("1.0.0")
            self.assertEqual(control.status()["job_id"], first["job_id"])
            self.assertEqual(control.status()["stage"], "queued")
            with self.assertRaises(releases.UpdateError):
                control.enqueue("1.0.1")


class UpdateAPITest(IdentityFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.core.config = replace(self.config, mode="server")
        self.app = create_app(self.core, public_origin="https://panel.example.test")
        self.client = APIClient(self.app)

    async def test_versions_are_visible_but_update_install_requires_admin_and_csrf(self):
        status, value, _ = await self.client.request("GET", "/api/v1/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(value["app_version"], releases.__version__)
        self.assertEqual((await self.client.request("GET", "/api/v1/updates"))[0], 401)
        self.assertEqual((await self.client.request("POST", "/api/v1/instance/update", {"version": "0.0.2"}))[0], 401)
        await self.client.login()
        self.assertEqual((await self.client.request("POST", "/api/v1/instance/update", {"version": "0.0.2"}, headers={"x-csrf-token": None}))[0], 403)
        self.assertEqual((await self.client.request("GET", "/api/v1/instance/update"))[1]["enabled"], False)
        with patch.object(releases, "check_release", return_value={"current_version": "0.0.1", "latest_version": "0.0.2", "available": True,
                "installable": True, "notes": "", "release_url": releases.RELEASES_URL, "published_at": None}), \
             patch.object(UpdateControl, "enqueue", return_value={"enabled": True, "stage": "queued", "job_id": str(uuid.uuid4()), "version": "0.0.2", "message": None}):
            self.assertEqual((await self.client.request("GET", "/api/v1/updates"))[0], 200)
            self.assertEqual((await self.client.request("POST", "/api/v1/instance/update", {"version": "0.0.2"}))[0], 202)
        with patch.object(releases, "check_release", return_value={"available": True, "installable": False, "latest_version": "0.0.2"}):
            self.assertEqual((await self.client.request("POST", "/api/v1/instance/update", {"version": "0.0.2"}))[0], 409)
        from cursor_dashboard.infrastructure.persistence.models import User
        with self.core.db.transaction(write=True) as session:
            session.get(User, self.actor.user_id).instance_admin = False
        self.assertEqual((await self.client.request("GET", "/api/v1/instance/update"))[0], 403)
        self.assertEqual((await self.client.request("POST", "/api/v1/instance/update", {"version": "0.0.2"}))[0], 403)


class AgentTest(unittest.TestCase):
    def test_interrupted_update_recovers_original_and_clears_pending_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control, private = root / "control", root / "private"
            control.mkdir(); private.mkdir()
            env = root / ".env"
            env.write_text("CURSOR_PANEL_IMAGE=new\n")
            job = {"job_id": str(uuid.uuid4()), "version": "0.0.2", "environment": "CURSOR_PANEL_IMAGE=sha256:" + "a" * 64 + "\n"}
            atomic_json(private / "recovery.json", job)
            atomic_json(control / "request.json", {"version": "0.0.2"})
            (control / "pending").touch()
            calls = []
            agent = Agent([root / "compose.yaml"], env, control, private, runner=lambda *args, **kw: calls.append(args))
            agent.recover()
            self.assertEqual(env.read_text(), job["environment"])
            self.assertEqual(json.loads((control / "status.json").read_text())["stage"], "failed")
            self.assertFalse((control / "request.json").exists())
            self.assertFalse((control / "pending").exists())
            self.assertFalse((private / "recovery.json").exists())

    def run_upgrade(self, *, fail_at=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        control, private = root / "control", root / "private"
        control.mkdir(); private.mkdir()
        env = root / ".env"
        original = "CURSOR_PANEL_DOMAIN=panel.example.test\nCURSOR_PANEL_IMAGE=old\n"
        env.write_text(original)
        old_image, image = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        calls, failed = [], False
        def runner(*args, **kwargs):
            nonlocal failed
            calls.append(args)
            if args[:1] == ("compose",) and "ps" in args:
                return "a" * 64
            if args[:1] == ("inspect",):
                return json.dumps([{"Image": old_image, "Config": {"Labels": {"org.opencontainers.image.version": "0.0.1"}},
                    "Mounts": [{"Destination": "/var/lib/cursor-panel", "Type": "volume", "Name": "original-data"},
                               {"Destination": "/run/cursor-secrets", "Type": "volume", "Name": "original-keys"}]}])
            if args[:2] == ("image", "inspect"):
                return json.dumps([{"Id": image, "Architecture": "amd64", "Os": "linux", "Config": {"Labels": {"org.opencontainers.image.version": "0.0.2"}}}])
            if fail_at and fail_at in args and not failed:
                failed = True
                raise releases.UpdateError("Synthetic failure")
            return ""
        agent = Agent([root / "compose.yaml"], env, control, private, runner=runner,
                      manifest=lambda version: {"image_id": image, "artifact": {}}, downloader=lambda artifact, path: path.write_bytes(b"image"))
        agent.execute({"job_id": str(uuid.uuid4()), "version": "0.0.2", "requested_at": time.time()})
        return root, env, original, calls, json.loads((control / "status.json").read_text())

    def test_upgrade_uses_a_separate_data_volume_and_retains_the_original(self):
        root, env, original, calls, status = self.run_upgrade()
        self.assertEqual(status["stage"], "complete")
        self.assertIn("CURSOR_PANEL_DATA_VOLUME=original-data-update-", env.read_text())
        upgrade = next(call for call in calls if "upgrade" in call)
        self.assertTrue(any("original-data-update-" in arg for arg in upgrade))
        self.assertNotIn("original-data:/var/lib/cursor-panel", upgrade)
        self.assertFalse(any(call[:2] == ("volume", "rm") for call in calls))
        self.assertEqual(next(root.glob(".env.before-*")).read_text(), original)

    def test_migration_and_health_failures_restore_original_environment(self):
        for fail_at in ("upgrade", "--wait"):
            with self.subTest(fail_at=fail_at):
                _, env, original, calls, status = self.run_upgrade(fail_at=fail_at)
                self.assertEqual(status["stage"], "failed")
                self.assertIn("CURSOR_PANEL_IMAGE=sha256:" + "a" * 64, env.read_text())
                self.assertIn("CURSOR_PANEL_DATA_VOLUME=original-data", env.read_text())
                self.assertIn("up", calls[-1])

    def test_failed_download_does_not_stop_the_running_server(self):
        _, env, original, calls, status = self.run_upgrade(fail_at="load")
        self.assertEqual(status["stage"], "failed")
        self.assertEqual(env.read_text(), original)
        self.assertFalse(any("stop" in call for call in calls))
