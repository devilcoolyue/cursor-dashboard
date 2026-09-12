import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from cursor_dashboard.updates.control import atomic_json, read_json
from cursor_dashboard.updates.retention import UpdateRetention


class UpdateRetentionTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.owned, self.attached, self.images = set(), set(), set()
        self.calls = []
        def runner(*args, **kwargs):
            self.calls.append(args)
            if args[:2] == ("volume", "ls"):
                return "\n".join(self.owned)
            if args[0] == "ps":
                return "container" if args[-1].split("=", 1)[1] in self.attached else ""
            if args[:2] == ("image", "ls"):
                return "\n".join(self.images)
            if args[:2] == ("volume", "rm"):
                self.assertNotIn(args[2], self.attached)
                self.owned.remove(args[2])
            if args[:2] == ("image", "rm"):
                self.images.remove(args[2])
            return ""
        agent = SimpleNamespace(private=self.root, env_file=self.root / ".env", journal=self.root / "recovery.json", runner=runner)
        self.retention = UpdateRetention(agent)

    def remember(self, index, *, failed=False, owned=True):
        job = {"job_id": str(uuid.uuid4()), "old_volume": f"data-update-{index}",
               "old_image": "sha256:" + f"{index:064x}", "new_volume": f"candidate-{index}",
               "new_image": "sha256:" + f"{100+index:064x}"}
        self.retention.remember(job, failed=failed)
        record_path = self.retention.directory / (job["job_id"] + ".json")
        record = read_json(record_path)
        record["created_at"] = index
        atomic_json(record_path, record)
        (self.root / (".env.before-" + job["job_id"])).write_text("synthetic configuration")
        if owned:
            self.owned.add(record["volume"])
        self.images.add(record["image"])
        return job, record

    def test_only_unused_owned_volumes_beyond_two_recovery_points_are_removed(self):
        _, baseline = self.remember(0, owned=False)
        _, oldest = self.remember(1)
        _, attached = self.remember(2)
        self.attached.add(attached["volume"])
        newest = [self.remember(index)[1] for index in (3, 4)]
        self.retention.prune()
        self.assertNotIn(oldest["volume"], self.owned)
        self.assertNotIn(oldest["image"], self.images)
        self.assertIn(baseline["image"], self.images)
        for record in [attached, *newest]:
            self.assertIn(record["volume"], self.owned)
            self.assertIn(record["image"], self.images)
        self.assertFalse(any("--force" in call for call in self.calls))
        self.attached.clear()
        self.retention.prune()
        self.assertNotIn(attached["volume"], self.owned)
        self.assertEqual(len(list(self.retention.directory.glob("*.json"))), 2)

    def test_failed_candidates_are_retired_only_after_recovery_finishes(self):
        _, failed = self.remember(0, failed=True)
        self.retention.agent.journal.write_text("pending recovery")
        self.retention.prune()
        self.assertEqual(self.calls, [])
        self.retention.agent.journal.unlink()
        self.retention.prune()
        self.assertNotIn(failed["volume"], self.owned)
        self.assertNotIn(failed["image"], self.images)

    def test_committing_replaces_candidate_record_and_shared_recovery_image_is_preserved(self):
        job, candidate = self.remember(0, failed=True)
        self.retention.remember(job)
        record = read_json(self.retention.directory / (job["job_id"] + ".json"))
        self.assertFalse(record["failed"])
        self.assertEqual(record["volume"], job["old_volume"])
        self.retention.prune()
        self.assertIn(candidate["volume"], self.owned)

    def test_failed_download_inventory_does_not_require_a_created_volume(self):
        _, failed = self.remember(0, failed=True, owned=False)
        self.retention.prune()
        self.assertNotIn(failed["image"], self.images)
        self.assertFalse(any(call[:2] == ("volume", "rm") for call in self.calls))
