"""Fernet-based symmetric encryption for storing OAuth credentials at rest.

Credentials are JSON-serialised and encrypted with Fernet before being written
to the database.  The encryption key is derived from the application
``SECRET_KEY`` setting using SHA-256 so that any 32+ character secret can be
used without requiring it to be already a valid Fernet key.

Usage::

    from app.utils.encryption import encrypt_credentials, decrypt_credentials

    encrypted = encrypt_credentials({"access_token": "...", "refresh_token": "..."})
    credentials = decrypt_credentials(encrypted)
"""

from __future__ import annotations

import hashlib
import json
import base64

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    """Build a ``Fernet`` instance keyed from the application ``SECRET_KEY``.

    The key derivation uses SHA-256 so that any string can be used as the
    secret without needing to be exactly 32 bytes in its raw form.  The
    resulting digest is URL-safe-base64-encoded as required by Fernet.

    Returns
    -------
    Fernet
        A ready-to-use Fernet symmetric cipher.
    """
    # Import here to avoid circular imports at module load time
    from app.config import get_settings

    settings = get_settings()
    raw_secret = settings.secret_key.encode("utf-8")

    # SHA-256 always produces 32 bytes — exactly what Fernet requires
    key_bytes = hashlib.sha256(raw_secret).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_credentials(data: dict) -> str:
    """Serialize and encrypt a credentials dictionary.

    Parameters
    ----------
    data:
        A dictionary of OAuth credentials (e.g. ``access_token``,
        ``refresh_token``, ``client_id``, ``client_secret``).

    Returns
    -------
    str
        A base64-encoded Fernet-encrypted string safe for database storage.
    """
    fernet = _get_fernet()
    plaintext = json.dumps(data, ensure_ascii=True).encode("utf-8")
    encrypted_bytes = fernet.encrypt(plaintext)
    # fernet.encrypt already returns URL-safe base64; decode to str for DB storage
    return encrypted_bytes.decode("utf-8")


def decrypt_credentials(encrypted: str) -> dict:
    """Decrypt and deserialize an encrypted credentials string.

    Parameters
    ----------
    encrypted:
        A Fernet-encrypted, base64-encoded string previously produced by
        :func:`encrypt_credentials`.

    Returns
    -------
    dict
        The original credentials dictionary.

    Raises
    ------
    cryptography.fernet.InvalidToken
        If the token is malformed, has been tampered with, or was encrypted
        with a different key.
    ValueError
        If the decrypted bytes cannot be parsed as JSON.
    """
    fernet = _get_fernet()
    try:
        decrypted_bytes = fernet.decrypt(encrypted.encode("utf-8"))
    except InvalidToken as exc:
        raise InvalidToken(
            "Failed to decrypt credentials — the token may be invalid or the "
            "SECRET_KEY may have changed."
        ) from exc

    try:
        return json.loads(decrypted_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Decrypted credentials are not valid JSON: {exc}"
        ) from exc
