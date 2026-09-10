from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import uuid

from ..domain.core import SecretError


class DesktopKeys:
    """A validated in-memory key provider; serialization stays in native code."""
    def __init__(self, document):
        try:
            self.document = json.loads(document)
            self._active = self.document["active"]
            self._keys = {key: base64.b64decode(value, validate=True)
                          for key, value in self.document["keys"].items()}
            if self._active not in self._keys or any(len(value) != 32 for value in self._keys.values()):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SecretError("System key is invalid") from None

    @classmethod
    def generate(cls):
        key_id = str(uuid.uuid4())
        return cls(json.dumps({"active": key_id, "keys": {
            key_id: base64.b64encode(os.urandom(32)).decode("ascii")}}))

    @property
    def active_id(self):
        return self._active

    def get(self, key_id):
        try:
            return self._keys[key_id]
        except KeyError:
            raise SecretError("Required system key is unavailable") from None


class SystemKeyStore:
    """Select only the OS backend, with no plaintext or third-party fallback."""
    service = "dev.cursor-panel.desktop"

    def __init__(self, data_dir):
        self.account = hashlib.sha256(os.fsencode(data_dir.resolve())).hexdigest()

    @staticmethod
    def backend():
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            raise SecretError("Desktop key storage requires macOS or Windows")
        return Keyring()

    def read(self):
        try:
            value = self.backend().get_password(self.service, self.account)
            return DesktopKeys(value) if value is not None else None
        except Exception:
            raise SecretError("System credential store is locked or unavailable") from None

    def save(self, keys):
        try:
            self.backend().set_password(self.service, self.account, json.dumps(keys.document))
            stored = self.read()
            if stored is None or stored.document != keys.document:
                raise ValueError()
        except Exception:
            raise SecretError("Cannot persist the system key") from None
