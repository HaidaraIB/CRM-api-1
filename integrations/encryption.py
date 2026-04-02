"""
Encryption utilities for sensitive data (Access Tokens, Refresh Tokens).
Uses Fernet symmetric encryption from the cryptography library.
"""
import logging
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

_fernet_instance = None


class EncryptionKeyMissing(Exception):
    """Raised when INTEGRATION_ENCRYPTION_KEY is not configured."""
    pass


def get_encryption_key():
    """
    Retrieve the Fernet encryption key from settings.
    Raises EncryptionKeyMissing if not configured in production.
    """
    key = getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "") or ""

    if not key:
        if settings.DEBUG:
            logger.warning(
                "INTEGRATION_ENCRYPTION_KEY not set. "
                "Encryption/decryption will fail for new tokens."
            )
            raise EncryptionKeyMissing(
                "INTEGRATION_ENCRYPTION_KEY not configured. "
                "Set it in your .env file."
            )
        raise EncryptionKeyMissing(
            "INTEGRATION_ENCRYPTION_KEY is required in production. "
            "Set it in your .env file."
        )

    if isinstance(key, str):
        key = key.encode()

    return key


def _get_fernet():
    """Return a cached Fernet instance (reuses key across calls)."""
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(get_encryption_key())
    return _fernet_instance


def encrypt_token(token):
    """Encrypt a plaintext token string. Returns encrypted string or None."""
    if not token:
        return None

    try:
        encrypted = _get_fernet().encrypt(token.encode())
        return encrypted.decode()
    except EncryptionKeyMissing:
        logger.error("Cannot encrypt token: encryption key not configured.")
        raise
    except Exception as e:
        logger.error(f"Failed to encrypt token: {e}")
        raise


def decrypt_token(encrypted_token):
    """
    Decrypt an encrypted token string.
    Falls back to returning the token as-is only if it looks like
    a legacy unencrypted value (not a valid Fernet token).
    """
    if not encrypted_token:
        return None

    try:
        decrypted = _get_fernet().decrypt(encrypted_token.encode())
        return decrypted.decode()
    except EncryptionKeyMissing:
        logger.error("Cannot decrypt token: encryption key not configured.")
        raise
    except InvalidToken:
        logger.warning(
            "Token is not a valid Fernet token — "
            "treating as legacy unencrypted value."
        )
        return encrypted_token
    except Exception as e:
        logger.error(f"Failed to decrypt token: {e}")
        raise
