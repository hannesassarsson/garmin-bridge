"""Encrypted Garmin token storage in a private Supabase table.

Render Free has no persistent disk, so the Garmin session tokens are kept in
Supabase instead. The token JSON is encrypted here (AES-256-GCM) before it
leaves the service; Supabase only ever stores ciphertext. Token values are
never logged.
"""

import base64
import hashlib
import os
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_TABLE = "garmin_tokens"
_ROW_ID = "default"
_PREFIX = "v1:"


class TokenStoreError(Exception):
    """Raised when tokens cannot be read from or written to Supabase."""


class SupabaseTokenStore:
    def __init__(self, url: str, secret_key: str, encryption_key: str, timeout: float = 10) -> None:
        if not url.startswith("https://"):
            raise TokenStoreError("SUPABASE_URL must start with https://")
        self._endpoint = f"{url.rstrip('/')}/rest/v1/{_TABLE}"
        self._headers = {"apikey": secret_key, "Content-Type": "application/json"}
        # Any long random string works (e.g. Render's "Generate" value); hash it
        # to get exactly 32 bytes for AES-256.
        self._aes = AESGCM(hashlib.sha256(encryption_key.encode()).digest())
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "SupabaseTokenStore | None":
        """Return a store if all Supabase variables are set, else None."""
        names = ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "TOKEN_ENCRYPTION_KEY")
        values = [os.getenv(name) for name in names]
        if not any(values):
            return None
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            raise TokenStoreError(f"Missing environment variables: {', '.join(missing)}")
        return cls(*values)

    def _encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode(), _ROW_ID.encode())
        return _PREFIX + base64.b64encode(nonce + ciphertext).decode()

    def _decrypt(self, stored: str) -> str:
        if not stored.startswith(_PREFIX):
            raise TokenStoreError("Stored tokens have an unknown format")
        raw = base64.b64decode(stored[len(_PREFIX):])
        try:
            return self._aes.decrypt(raw[:12], raw[12:], _ROW_ID.encode()).decode()
        except Exception as exc:
            raise TokenStoreError("Stored tokens could not be decrypted (wrong TOKEN_ENCRYPTION_KEY?)") from exc

    def load(self) -> str | None:
        """Return the decrypted token JSON, or None if nothing is stored yet."""
        try:
            resp = requests.get(
                self._endpoint,
                headers=self._headers,
                params={"id": f"eq.{_ROW_ID}", "select": "token_ciphertext"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TokenStoreError(f"Supabase request failed: {type(exc).__name__}") from None
        if not resp.ok:
            raise TokenStoreError(f"Supabase read failed: HTTP {resp.status_code}")
        rows = resp.json()
        if not rows:
            return None
        return self._decrypt(rows[0]["token_ciphertext"])

    def save(self, token_json: str) -> None:
        """Encrypt and upsert the token JSON."""
        try:
            resp = requests.post(
                self._endpoint,
                headers={**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                params={"on_conflict": "id"},
                json={
                    "id": _ROW_ID,
                    "token_ciphertext": self._encrypt(token_json),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TokenStoreError(f"Supabase request failed: {type(exc).__name__}") from None
        if not resp.ok:
            raise TokenStoreError(f"Supabase write failed: HTTP {resp.status_code}")
