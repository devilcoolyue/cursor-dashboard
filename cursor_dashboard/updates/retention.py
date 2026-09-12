"""Retire only this updater's recorded, unused Docker recovery resources."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid

from .control import atomic_json, read_json
from .releases import UpdateError


class UpdateRetention:
    keep = 2
    label = "dev.cursor-panel.update-owner"

    def __init__(self, agent):
        self.agent = agent
        self.directory = agent.private / "retained"
        self.owner = hashlib.sha256(str(agent.env_file).encode()).hexdigest()

    def remember(self, journal, *, failed=False):
        prefix = "new" if failed else "old"
        if not all(journal.get(f"{prefix}_{field}") for field in ("volume", "image")):
            return  # Recovery records from older updater versions have no inventory.
        self.directory.mkdir(mode=0o700, exist_ok=True)
        job_id = str(uuid.UUID(journal["job_id"]))
        destination = self.directory / f"{job_id}.json"
        existing = read_json(destination) if destination.exists() else None
        if existing is None or (existing["failed"] and not failed):
            atomic_json(destination, {"job_id": job_id, "created_at": time.time(), "failed": failed,
                "managed_image": failed, "volume": journal[f"{prefix}_volume"], "image": journal[f"{prefix}_image"]})

    def prune(self):
        if self.agent.journal.exists() or not self.directory.exists() or self.directory.is_symlink():
            return
        try:
            records = []
            for path in self.directory.glob("*.json"):
                record = read_json(path)
                if (path.stem != str(uuid.UUID(record["job_id"]))
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record["volume"])
                        or not re.fullmatch(r"sha256:[a-f0-9]{64}", record["image"])):
                    raise ValueError()
                records.append((record, path))
            records.sort(key=lambda item: (item[0]["created_at"], item[0]["job_id"]), reverse=True)
            successful = [item for item in records if not item[0]["failed"]]
            protected = successful[:self.keep]
            volumes = {record["volume"] for record, _ in protected}
            images = {record["image"] for record, _ in protected}
            candidates = [item for item in records if item not in protected]
            if not candidates:
                return
            runner = self.agent.runner
            owned = set(runner("volume", "ls", "--filter", f"label={self.label}={self.owner}",
                               "--format", "{{.Name}}").splitlines())
            for record, path in candidates:
                volume, image = record["volume"], record["image"]
                if volume in volumes:
                    continue
                if volume in owned:
                    # Includes stopped containers; Docker also refuses an attached volume without force.
                    if runner("ps", "-aq", "--filter", f"volume={volume}").strip():
                        continue
                    record["managed_image"] = True
                    atomic_json(path, record)
                    runner("volume", "rm", volume)
                if record.get("managed_image") and image not in images:
                    present = set(runner("image", "ls", "--no-trunc", "--quiet").splitlines())
                    if image in present:
                        runner("image", "rm", image)  # No --force: other containers/tags protect shared images.
                # Unlabelled original volumes/images remain under manual operator control.
                before = self.agent.env_file.with_name(self.agent.env_file.name + ".before-" + record["job_id"])
                if not before.is_symlink():
                    before.unlink(missing_ok=True)
                path.unlink()
        except (OSError, ValueError, KeyError, TypeError, UpdateError):
            logging.getLogger(__name__).warning("Update retention could not finish; recovery resources were preserved")
