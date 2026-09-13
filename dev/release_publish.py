"""Idempotent GitHub release publication; never replaces an existing version's files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import zipfile
from urllib.parse import quote

import requests

from release import ROOT
from release_pipeline import OUTPUT, check, verify_assets, write_json

from cursor_dashboard.updates.releases import REPOSITORY, release_asset_url


class ApiError(RuntimeError):
    def __init__(self, status, message="GitHub request failed"):
        super().__init__(f"{message} (HTTP {status})")
        self.status = status


class GitHub:
    def __init__(self):
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Cursor-Panel-Release"})

    def request(self, method, path, *, data=None, file=None):
        origin = "https://uploads.github.com" if file else "https://api.github.com"
        # Only GET is retried here. Mutations are reconciled by reading remote state.
        for attempt in range(3):
            try:
                if file:
                    with file.open("rb") as stream:
                        response = self.session.request(method, origin + path, data=stream,
                            headers={"Content-Type": "application/octet-stream"}, timeout=(15, 240))
                else:
                    response = self.session.request(method, origin + path, json=data, timeout=(15, 60))
                if response.status_code >= 400:
                    raise ApiError(response.status_code)
                return response.json() if response.content else None
            except requests.RequestException:
                error = ApiError(0, "GitHub connection interrupted")
            except ValueError:
                error = ApiError(0, "GitHub returned incomplete JSON")
            except ApiError as cause:
                if cause.status < 500 and cause.status not in {429}:
                    raise
                error = cause
            if method != "GET" or attempt == 2:
                raise error
            time.sleep(2 ** attempt)


class Publisher:
    def __init__(self, api, version, revision, assets, inventory, notes, sleep=time.sleep):
        self.api, self.version, self.revision = api, version, revision
        self.assets, self.inventory, self.notes, self.sleep = assets, inventory, notes, sleep
        self.base = f"/repos/{REPOSITORY}"
        self.tag = f"v{version}"

    def get_tag(self):
        try:
            value = self.api.request("GET", self.base + f"/git/ref/tags/{self.tag}")["object"]
        except ApiError as error:
            if error.status == 404:
                return None
            raise
        for _ in range(4):
            if value["type"] == "commit":
                return value["sha"]
            if value["type"] != "tag":
                break
            value = self.api.request("GET", self.base + "/git/tags/" + value["sha"])["object"]
        raise ValueError("Release tag does not resolve to a commit")

    def ensure_tag(self):
        existing = self.get_tag()
        if existing and existing != self.revision:
            raise ValueError("Version tag already points to different source; choose a new version")
        if not existing:
            try:
                self.api.request("POST", self.base + "/git/refs", data={"ref": f"refs/tags/{self.tag}", "sha": self.revision})
            except ApiError:
                if self.get_tag() != self.revision:
                    raise

    def check_available(self):
        existing = self.get_tag()
        if existing and existing != self.revision:
            raise ValueError("Version tag already points to different source; choose a new version before building")
        release = self.find_release()
        if release and not release["draft"]:
            raise ValueError("This version is already published; no new release build is needed")
        if release and release["target_commitish"] != self.revision:
            raise ValueError("An existing draft belongs to different source")

    def find_release(self):
        matches = []
        for page in range(1, 21):
            rows = self.api.request("GET", self.base + f"/releases?per_page=100&page={page}")
            matches.extend(row for row in rows if row["tag_name"] == self.tag)
            if len(rows) < 100:
                break
        else:
            raise ValueError("Release list exceeded search bound")
        if len(matches) > 1:
            raise ValueError("Multiple drafts exist for this version; inspect their IDs before continuing")
        return matches[0] if matches else None

    def release_payload(self, draft):
        # Always supply tag_name: partial edits of drafts previously produced untagged releases.
        return {"tag_name": self.tag, "target_commitish": self.revision, "name": f"Cursor Panel {self.tag}",
                "body": self.notes, "draft": draft, "prerelease": False, "make_latest": "true"}

    def ensure_release(self):
        release = self.find_release()
        if release:
            if release["draft"] and release["target_commitish"] != self.revision:
                raise ValueError("Draft belongs to different source; inspect it instead of replacing it")
            return release
        try:
            # The create response has the authoritative ID. The list endpoint can
            # briefly omit a newly created draft even after a successful response.
            release = self.api.request("POST", self.base + "/releases", data=self.release_payload(True))
        except ApiError:
            # A timeout/500 can occur AFTER GitHub creates the draft. Never blindly POST again.
            self.sleep(2)
            release = self.find_release()
            if release is None:
                raise
        if release is None or release["tag_name"] != self.tag or release["target_commitish"] != self.revision:
            raise ValueError("Cannot identify the created release draft")
        return release

    def read_release(self, release_id):
        return self.api.request("GET", self.base + f"/releases/{release_id}")

    def matches(self, asset, name):
        expected = self.inventory[name]
        return (asset["state"] == "uploaded" and asset["size"] == expected["size"]
                and asset.get("digest") == "sha256:" + expected["sha256"])

    def validate_remote(self, release):
        assets = release["assets"]
        if len(assets) != len(self.inventory) or {a["name"] for a in assets} != set(self.inventory):
            raise ValueError("Remote release asset set differs from verified local inventory")
        if any(not self.matches(asset, asset["name"]) for asset in assets):
            raise ValueError("Remote release checksums differ from verified local inventory")
        if release["tag_name"] != self.tag or release["prerelease"] or self.get_tag() != self.revision:
            raise ValueError("Remote release tag or source changed")

    def upload(self, release_id, name):
        for attempt in range(3):
            release = self.read_release(release_id)
            if not release["draft"]:
                raise ValueError("Release became public during upload; refusing to mutate it")
            matches = [a for a in release["assets"] if a["name"] == name]
            if len(matches) > 1:
                raise ValueError("Duplicate remote asset names")
            if matches:
                asset = matches[0]
                if self.matches(asset, name):
                    return
                if asset["state"] != "starter":
                    raise ValueError(f"Existing asset has different content: {name}")
                self.api.request("DELETE", self.base + f"/releases/assets/{asset['id']}")
            try:
                self.api.request("POST", self.base + f"/releases/{release_id}/assets?name={quote(name)}", file=self.assets / name)
            except ApiError as error:
                if error.status not in {0, 408, 429, 500, 502, 503, 504}:
                    raise
            # Re-read even after success, so a dropped response cannot create a duplicate upload.
            current = self.read_release(release_id)
            if any(a["name"] == name and self.matches(a, name) for a in current["assets"]):
                print(f"Verified upload: {name}", flush=True)
                return
            if attempt < 2:
                self.sleep(2 ** attempt)
        raise ValueError(f"Upload incomplete after 3 attempts: {name}; rerun the publish job to resume")

    def publish(self):
        self.ensure_tag()
        release = self.ensure_release()
        release_id = release["id"]
        if not release["draft"]:
            self.validate_remote(self.read_release(release_id))
            return release["html_url"]
        for name in self.inventory:
            self.upload(release_id, name)
        self.validate_remote(self.read_release(release_id))
        for attempt in range(3):
            try:
                self.api.request("PATCH", self.base + f"/releases/{release_id}", data=self.release_payload(False))
            except ApiError as error:
                if error.status not in {0, 408, 429, 500, 502, 503, 504}:
                    raise
            release = self.read_release(release_id)
            if not release["draft"]:
                self.validate_remote(release)
                latest = self.api.request("GET", self.base + "/releases/latest")
                if latest["id"] != release_id:
                    raise ValueError("Release is public but is not latest; inspect release ordering")
                return release["html_url"]
            self.sleep(2 ** attempt)
        raise ValueError("Draft publication did not complete; rerun to resume by release ID")


def verify_public_updates(directory, version):
    # Old installations must also see the exact bytes through the repository redirect.
    for legacy in (False, True):
        for name in ("latest.json", "server-update.json", "server-update.json.sig"):
            url = release_asset_url(version, name, legacy=legacy)
            for attempt in range(3):
                try:
                    response = requests.get(url, timeout=(15, 30))
                    response.raise_for_status()
                    if response.content != (directory / name).read_bytes():
                        raise ValueError(f"Public update metadata differs: {url}")
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--assets", type=Path, default=OUTPUT / "assets")
    parser.add_argument("--publish", action="store_true", help="Create tag/release and publish after verifying all artifacts")
    parser.add_argument("--check-available", action="store_true", help="Read-only preflight: reject version conflicts before building")
    args = parser.parse_args()
    check(args.version, args.revision)
    if args.check_available:
        Publisher(GitHub(), args.version, args.revision, args.assets, {}, "").check_available()
        print(json.dumps({"version": args.version, "available": True}))
        return
    inventory = verify_assets(args.assets, args.version)
    with zipfile.ZipFile(args.assets / f"cursor-panel-v{args.version}-verification.zip") as archive:
        proof = json.loads(archive.read("verification.json"))
        if proof["source_revision"] != args.revision or proof["version"] != args.version:
            raise ValueError("Artifact source differs from requested publication")
    if not args.publish:
        print(json.dumps({"verified": True, "version": args.version, "assets": len(inventory), "published": False}))
        return
    notes = (ROOT / f"docs/archive/v{args.version}.md").read_text(encoding="utf-8")
    notes += "\n\n验证记录：" + proof["run_url"] + "\n"
    url = Publisher(GitHub(), args.version, args.revision, args.assets, inventory, notes).publish()
    # Check the public update endpoints too; a resumed publication repeats this check.
    verify_public_updates(args.assets, args.version)
    write_json(OUTPUT / "publication.json", {"version": args.version, "source_revision": args.revision, "url": url, "published": True})
    print(url)


if __name__ == "__main__":
    main()
