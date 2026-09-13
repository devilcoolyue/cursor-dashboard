"""Release preparation and interrupted publication preserve immutable source and artifacts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev"))
from release_cli import prepare  # noqa: E402
from release_pipeline import LOCKS, TARGETS, sha256, verify_parts  # noqa: E402
from release_publish import ApiError, Publisher, verify_public_updates  # noqa: E402


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        from release import check_versions
        self.current = check_versions()
        files = ["pyproject.toml", "cursor_dashboard/__init__.py", "desktop/sidecar/pyproject.toml",
                 "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/tauri.conf.json", "desktop/package.json",
                 "frontend/package.json", "Dockerfile", "deploy/v2/.env.example", "deploy/v2/compose.yaml",
                 "README.md", "README_CN.md", "docs/automatic-updates.md", "docs/supported-platforms.md",
                 "docs/v2-release-operations.md", "docs/v2-desktop-operations.md", *LOCKS]
        for name in files:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        (self.root / "docs/archive").mkdir()
        self.history = self.root / f"docs/archive/v{self.current}.md"
        self.history.write_text(f"Historical version {self.current}")
        self.notes = self.root / "notes.md"
        self.notes.write_text("# v99.0.0\n\nSynthetic release notes")

    def test_versions_stay_consistent_without_modifying_history_or_dependencies(self):
        from release import check_versions
        before = (self.root / "uv.lock").read_text()
        prepare("99.0.0", self.notes, self.root)
        self.assertEqual(check_versions(self.root), "99.0.0")
        self.assertEqual(self.history.read_text(), f"Historical version {self.current}")
        self.assertIn(f"archive/v{self.current}.md", (self.root / "docs/v2-release-operations.md").read_text())
        after = (self.root / "uv.lock").read_text()
        self.assertEqual(before.replace(f'name = "cursor-dashboard"\nversion = "{self.current}"',
                                        'name = "cursor-dashboard"\nversion = "99.0.0"'), after)

    def test_bad_input_is_rejected_before_any_version_changes(self):
        before = (self.root / "pyproject.toml").read_bytes()
        with self.assertRaises(ValueError):
            prepare(self.current, self.notes, self.root)
        (self.root / "README_CN.md").unlink()
        with self.assertRaises(FileNotFoundError):
            prepare("99.0.0", self.notes, self.root)
        self.assertEqual((self.root / "pyproject.toml").read_bytes(), before)


class PartsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict("os.environ", {"GITHUB_RUN_ID": "fixture-run"}).start()
        for target in TARGETS:
            directory = self.root / target
            directory.mkdir()
            payload = directory / "fixture.txt"
            payload.write_text(target)
            smoke = {"samples": [{"mode": mode, "process_tree_cleaned": True, "startup_log_verified": True,
                                 "backend": {"frozen": True}, "frontend": {"rendered_accounts": 2}}
                                for mode in ("cold", "warm", "crash")]}
            record = {"version": "0.0.9", "source_revision": "a" * 40, "target": target,
                      "run_id": "fixture-run", "lock_sha256": {n: sha256(ROOT / n) for n in LOCKS},
                      "files": {payload.name: {"size": payload.stat().st_size, "sha256": sha256(payload)}},
                      "installed_smoke": smoke}
            (directory / "part.json").write_text(json.dumps(record))

    def test_parts_require_all_targets_same_source_and_intact_bytes(self):
        self.assertEqual(len(verify_parts(self.root, "0.0.9", "a" * 40)), 4)
        path = self.root / TARGETS[0] / "fixture.txt"
        path.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            verify_parts(self.root, "0.0.9", "a" * 40)

    def test_mixed_source_and_missing_installation_evidence_are_rejected(self):
        path = self.root / TARGETS[0] / "part.json"
        record = json.loads(path.read_text())
        record["source_revision"] = "b" * 40
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "mix versions"):
            verify_parts(self.root, "0.0.9", "a" * 40)
        record["source_revision"] = "a" * 40
        record["installed_smoke"]["samples"][0]["startup_log_verified"] = False
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "checks failed"):
            verify_parts(self.root, "0.0.9", "a" * 40)


class FakeGitHub:
    def __init__(self):
        self.tag = None
        self.release = None
        self.mutations = []
        self.interrupt_create = self.interrupt_upload = self.interrupt_publish = False

    def request(self, method, path, *, data=None, file=None):
        if method != "GET":
            self.mutations.append((method, path))
        if "/git/ref/tags/" in path:
            if self.tag is None:
                raise ApiError(404)
            return {"object": {"type": "commit", "sha": self.tag}}
        if path.endswith("/git/refs"):
            self.tag = data["sha"]
            return {}
        if "/releases?" in path:
            return [copy.deepcopy(self.release)] if self.release else []
        if path.endswith("/releases") and method == "POST":
            self.release = {**data, "id": 42, "assets": [], "html_url": "https://github.com/devilcoolyue/cursor-panel/releases/tag/v0.0.9"}
            if self.interrupt_create:
                raise ApiError(500)
            return copy.deepcopy(self.release)
        if "/assets?" in path:
            item = {"id": 1, "name": file.name, "state": "uploaded", "size": file.stat().st_size, "digest": "sha256:" + sha256(file)}
            self.release["assets"].append(item)
            if self.interrupt_upload:
                raise ApiError(0)
            return item
        if path.endswith("/releases/42"):
            if method == "PATCH":
                self.release.update(data)
                if self.interrupt_publish:
                    raise ApiError(502)
            return copy.deepcopy(self.release)
        if path.endswith("/releases/latest"):
            return copy.deepcopy(self.release)
        raise AssertionError((method, path))


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        payload = self.root / "fixture.exe"
        payload.write_bytes(b"synthetic-installer")
        self.inventory = {payload.name: {"size": payload.stat().st_size, "sha256": sha256(payload)}}
        self.api = FakeGitHub()
        self.publisher = Publisher(self.api, "0.0.9", "a" * 40, self.root, self.inventory, "Notes", sleep=lambda _: None)

    def test_lost_create_upload_and_publish_responses_do_not_duplicate_mutations(self):
        self.api.interrupt_create = self.api.interrupt_upload = self.api.interrupt_publish = True
        self.assertTrue(self.publisher.publish().endswith("v0.0.9"))
        self.assertEqual(len(self.api.release["assets"]), 1)
        self.assertEqual(sum(p.endswith("/releases") for _, p in self.api.mutations), 1)
        self.assertEqual(sum("/assets?" in p for _, p in self.api.mutations), 1)
        self.assertTrue(all(p.startswith("/repos/devilcoolyue/cursor-panel/") for _, p in self.api.mutations))
        self.assertFalse(self.api.release["draft"])
        before = list(self.api.mutations)
        self.publisher.publish()
        self.assertEqual(self.api.mutations, before)

    def test_existing_tag_cannot_be_moved(self):
        self.api.tag = "b" * 40
        with self.assertRaisesRegex(ValueError, "different source"):
            self.publisher.publish()
        self.assertEqual(self.api.mutations, [])

    def test_public_update_verification_checks_the_legacy_redirect_too(self):
        names = ("latest.json", "server-update.json", "server-update.json.sig")
        for name in names:
            (self.root / name).write_bytes(name.encode())

        def response(url, **kwargs):
            return SimpleNamespace(content=url.rsplit("/", 1)[-1].encode(), raise_for_status=lambda: None)

        with patch("release_publish.requests.get", side_effect=response) as get:
            verify_public_updates(self.root, "0.0.9")
        self.assertEqual({call.args[0] for call in get.call_args_list}, {
            f"https://github.com/devilcoolyue/{repo}/releases/download/v0.0.9/{name}"
            for repo in ("cursor-panel", "cursor-dashboard") for name in names})

        def broken_redirect(url, **kwargs):
            result = response(url)
            if "/cursor-dashboard/" in url:
                result.content = b"wrong-release"
            return result

        with patch("release_publish.requests.get", side_effect=broken_redirect):
            with self.assertRaisesRegex(ValueError, "Public update metadata differs"):
                verify_public_updates(self.root, "0.0.9")

    def test_corrupt_remote_asset_is_never_overwritten(self):
        self.publisher.ensure_tag()
        self.publisher.ensure_release()
        self.api.release["assets"] = [{"id": 1, "name": "fixture.exe", "state": "uploaded", "size": 1, "digest": "sha256:wrong"}]
        before = list(self.api.mutations)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.publisher.publish()
        self.assertEqual(self.api.mutations, before)
        self.assertTrue(self.api.release["draft"])

    def test_completed_release_is_immutable_even_if_local_bytes_differ(self):
        self.publisher.publish()
        self.inventory["fixture.exe"]["sha256"] = "f" * 64
        before = list(self.api.mutations)
        with self.assertRaisesRegex(ValueError, "checksums"):
            self.publisher.publish()
        self.assertEqual(self.api.mutations, before)


if __name__ == "__main__":
    unittest.main()
