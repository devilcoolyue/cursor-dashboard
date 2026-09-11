"""Delivery integrity failures are rejected before an update is attempted."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parents[1] / "dev/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p6-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        payload = self.root / "panel.dmg"
        payload.write_bytes(b"synthetic-delivery-only")
        self.report = {"format": 1, "channel": "v2-preview", "source_commit": "a" * 40,
                       "artifacts": [{"name": payload.name, "size": payload.stat().st_size,
                                      "sha256": release.sha256(payload)}]}
        self.write_manifest()

    def write_manifest(self):
        (self.root / "release-manifest.json").write_text(json.dumps(self.report))
        self.write_checksums()

    def write_checksums(self):
        (self.root / "SHA256SUMS").write_text("".join(
            f"{release.sha256(path)}  {path.name}\n" for path in sorted(self.root.iterdir()) if path.name != "SHA256SUMS"))

    def test_intact_delivery_and_corrupted_payload_or_manifest(self):
        self.assertTrue(release.verify(self.root)["verified"])
        (self.root / "panel.dmg").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            release.verify(self.root)
        self.write_checksums()
        with self.assertRaisesRegex(ValueError, "digest/size mismatch"):
            release.verify(self.root)

    def test_unlisted_file_missing_file_and_duplicate_entries_rejected(self):
        (self.root / "unexpected.txt").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "unlisted files"):
            release.verify(self.root)
        (self.root / "unexpected.txt").unlink()
        sums = self.root / "SHA256SUMS"
        sums.write_text(sums.read_text() * 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            release.verify(self.root)
        self.write_checksums()
        (self.root / "panel.dmg").unlink()
        with self.assertRaisesRegex(ValueError, "missing or unlisted"):
            release.verify(self.root)

    def test_traversal_and_manifest_artifact_substitution_rejected(self):
        sums = self.root / "SHA256SUMS"
        sums.write_text("a" * 64 + "  ../outside\n")
        with self.assertRaises(ValueError):
            release.verify(self.root)
        self.report["artifacts"][0]["name"] = "../outside"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "artifact set differs"):
            release.verify(self.root)

    def test_wheel_requires_web_and_rejects_runtime_data_and_token_material(self):
        wheel = self.root / "panel.whl"
        for bad, content in (("cursor_dashboard/core.db", b"synthetic"),
                             ("cursor_dashboard/innocent.txt", b"SQLite format 3\x00synthetic"),
                             ("cursor_dashboard/innocent.txt", b"-----BEGIN " + b"PRIVATE KEY-----"),
                             ("../outside", b"synthetic")):
            with self.subTest(bad=bad, content=content), zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("cursor_dashboard/web_v2/index.html", "<title>Fixture</title>")
                archive.writestr(bad, content)
            with self.assertRaises(ValueError):
                release.audit_wheel(wheel)
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("cursor_dashboard/__init__.py", "")
        with self.assertRaisesRegex(ValueError, "Web assets"):
            release.audit_wheel(wheel)

    def test_project_versions_are_consistent(self):
        self.assertRegex(release.check_versions(), r"^\d+\.\d+\.\d+$")
