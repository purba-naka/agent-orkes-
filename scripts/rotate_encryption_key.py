"""Re-encrypt stored secrets from an old APP_ENCRYPTION_KEY to the current one.

Usage (dry run, then apply):
    uv run python scripts/rotate_encryption_key.py OLD_BASE64_KEY
    uv run python scripts/rotate_encryption_key.py OLD_BASE64_KEY --apply

Covers credentials and MCP connection OAuth tokens / stdio env. Rows that
already decrypt with the current key are left alone. Never prints secrets.
"""

import asyncio
import sys

from sqlalchemy import select

from orchestrator.db.models import Credential, McpConnection
from orchestrator.db.session import async_session_factory
from orchestrator.security.encryption import CredentialVault, vault as new


def _rotate(old: CredentialVault, ciphertext: bytes, nonce: bytes, aad: bytes):
    """Return new (ciphertext, nonce), or None if already on the current key."""
    try:
        new.decrypt(ciphertext, nonce, aad=aad)
        return None
    except Exception:
        plaintext = old.decrypt(ciphertext, nonce, aad=aad)  # raises if neither key works
        ct, n, _ = new.encrypt(plaintext, aad=aad)
        return ct, n


async def main(old_key: str, apply: bool) -> None:
    old = CredentialVault(old_key)
    async with async_session_factory() as session:
        for cred in (await session.scalars(select(Credential))).all():
            aad = f"credential:{cred.id}:v{cred.key_version}".encode()
            rotated = _rotate(old, cred.ciphertext, cred.nonce, aad)
            print(f"credential {cred.name}: {'rotate' if rotated else 'ok'}")
            if rotated:
                cred.ciphertext, cred.nonce = rotated

        for conn in (await session.scalars(select(McpConnection))).all():
            aad = f"mcp_connection:{conn.id}".encode()
            for field in ("token", "env"):
                ct = getattr(conn, f"{field}_ciphertext")
                if ct is None:
                    continue
                rotated = _rotate(old, ct, getattr(conn, f"{field}_nonce"), aad)
                print(f"mcp {conn.name} {field}: {'rotate' if rotated else 'ok'}")
                if rotated:
                    setattr(conn, f"{field}_ciphertext", rotated[0])
                    setattr(conn, f"{field}_nonce", rotated[1])

        if apply:
            await session.commit()
            print("applied")
        else:
            await session.rollback()
            print("dry run; pass --apply to write")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], "--apply" in sys.argv[2:]))
