"""Encrypted-at-rest storage for rotated Ring OAuth tokens (Fernet, AES-128-CBC + HMAC).
Tokens never leave the backend and are never sent to the browser."""
from __future__ import annotations

import json
import os
import time

from cryptography.fernet import Fernet


class TokenVault:
    def __init__(self, db, settings):
        self.db = db
        key = settings.token_encryption_key
        if not key:
            kf = settings.data_dir / ".token_key"
            if not kf.exists():
                kf.write_bytes(Fernet.generate_key())
                os.chmod(kf, 0o600)
            key = kf.read_bytes().decode()
        self.f = Fernet(key.encode() if isinstance(key, str) else key)

    def save(self, tokens: dict):
        blob = self.f.encrypt(json.dumps(tokens).encode())
        self.db.x("INSERT OR REPLACE INTO secrets(name, value, updated_at) VALUES('ring_tokens', ?, ?)", (blob, time.time()))

    def load(self) -> dict | None:
        r = self.db.one("SELECT value FROM secrets WHERE name='ring_tokens'")
        return json.loads(self.f.decrypt(r["value"])) if r else None

    def clear(self) -> bool:
        """Remove the stored (rotated) Ring OAuth tokens. Returns True if something was deleted."""
        return self.db.x("DELETE FROM secrets WHERE name='ring_tokens'").rowcount > 0

    # The single live household is the only Ring-connected account in this implementation. When it is deleted we
    # remember a fingerprint of the operator-supplied env credentials so a restart does not silently reconnect with
    # them; supplying NEW credentials (a re-link) clears the marker.
    @staticmethod
    def env_fingerprint(settings=None) -> str:
        """Fingerprint of the OPERATOR-supplied credentials (environment), not of rotated tokens held in memory."""
        import hashlib
        parts = [os.getenv(k, "") for k in ("RING_ACCESS_TOKEN", "RING_REFRESH_TOKEN", "RING_CLIENT_ID")]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def mark_disconnected(self, settings):
        self.db.x("INSERT OR REPLACE INTO secrets(name, value, updated_at) VALUES('ring_disconnected', ?, ?)",
                  (self.env_fingerprint(settings).encode(), time.time()))

    def is_disconnected(self, settings) -> bool:
        r = self.db.one("SELECT value FROM secrets WHERE name='ring_disconnected'")
        if not r:
            return False
        if bytes(r["value"]).decode() == self.env_fingerprint(settings):
            return True
        self.db.x("DELETE FROM secrets WHERE name='ring_disconnected'")  # new credentials supplied: re-linked
        return False
