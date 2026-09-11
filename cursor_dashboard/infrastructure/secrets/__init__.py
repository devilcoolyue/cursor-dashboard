from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import stat
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...domain.core import KeyProvider, SecretError, Secrets


class FileKeyProvider:
    def __init__(self, path: Path):
        try:
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise SecretError("Key file must be accessible only to its owner (0600)")
            document = json.loads(path.read_text(encoding="utf-8"))
            self._active = document["active"]
            self._keys = {name: base64.b64decode(value, validate=True)
                          for name, value in document["keys"].items()}
            if self._active not in self._keys or any(len(value) != 32 for value in self._keys.values()):
                raise ValueError()
        except SecretError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise SecretError("Key file is missing or invalid; restore the original key file") from None

    @property
    def active_id(self):
        return self._active

    def get(self, key_id):
        try:
            return self._keys[key_id]
        except KeyError:
            raise SecretError("Required key version is unavailable") from None

    @staticmethod
    def create(path: Path):
        key_id = str(uuid.uuid4())
        data = json.dumps({"active": key_id, "keys": {
            key_id: base64.b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")}}).encode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise SecretError("Key file already exists; it will not be replaced") from None
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


class Cipher:
    def __init__(self, keys: KeyProvider):
        self.keys = keys

    def encrypt(self, payload: bytes, context: list) -> tuple[str, bytes]:
        key_id = self.keys.active_id
        nonce = os.urandom(12)
        aad = json.dumps([key_id, *context], separators=(",", ":")).encode()
        return key_id, nonce + AESGCM(self.keys.get(key_id)).encrypt(nonce, payload, aad)

    def decrypt(self, key_id: str, payload: bytes, context: list) -> bytes:
        aad = json.dumps([key_id, *context], separators=(",", ":")).encode()
        try:
            return AESGCM(self.keys.get(key_id)).decrypt(payload[:12], payload[12:], aad)
        except (InvalidTag, ValueError, TypeError):
            raise SecretError("Cannot decrypt stored data; check the key and backup integrity") from None

    def seal(self, secrets: Secrets, ref):
        data = json.dumps({"cookie": secrets.cookie, "access_token": secrets.access_token,
                           "refresh_token": secrets.refresh_token}, separators=(",", ":")).encode()
        return self.encrypt(data, [ref.workspace_id, ref.account_id, ref.generation, ref.version])

    def unseal(self, key_id, payload, ref) -> Secrets:
        try:
            data = json.loads(self.decrypt(key_id, payload, [ref.workspace_id, ref.account_id,
                                                            ref.generation, ref.version]))
            if set(data) != {"cookie", "access_token", "refresh_token"} or not all(isinstance(v, str) for v in data.values()):
                raise ValueError()
            return Secrets(**data)
        except (ValueError, TypeError):
            raise SecretError("Credential payload is invalid") from None
