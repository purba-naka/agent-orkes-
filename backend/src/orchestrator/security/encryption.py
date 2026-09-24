import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from orchestrator.config import settings


class CredentialVault:
    def __init__(self, base64_key: str | None = None) -> None:
        key_str = base64_key or settings.app_encryption_key
        raw_key = base64.b64decode(key_str)
        if len(raw_key) != 32:
            raise ValueError(
                f"Master encryption key must be exactly 32 bytes, got {len(raw_key)}"
            )
        self._aesgcm = AESGCM(raw_key)
        self.key_version = 1

    def encrypt(self, secret: str, aad: bytes | None = None) -> tuple[bytes, bytes, int]:
        """Encrypt secret with AES-256-GCM.
        Returns: (ciphertext, nonce, key_version)
        """
        nonce = os.urandom(12)  # Standard 96-bit nonce for AES-GCM
        ciphertext = self._aesgcm.encrypt(nonce, secret.encode("utf-8"), aad)
        return ciphertext, nonce, self.key_version

    def decrypt(self, ciphertext: bytes, nonce: bytes, aad: bytes | None = None) -> str:
        """Decrypt AES-256-GCM ciphertext.
        Returns plaintext string.
        """
        plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, aad)
        return plaintext_bytes.decode("utf-8")


vault = CredentialVault()
