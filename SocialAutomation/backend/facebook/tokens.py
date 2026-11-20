"""Encrypt Facebook credentials before they are written to the database."""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenEncryptionError(Exception):
    """Raised when the application cannot safely encrypt or read a token."""


def _cipher():
    key = settings.FACEBOOK_TOKEN_ENCRYPTION_KEY
    if not key:
        raise TokenEncryptionError(
            'Facebook token encryption is not configured. Set FACEBOOK_TOKEN_ENCRYPTION_KEY.'
        )
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, UnicodeEncodeError):
        raise TokenEncryptionError(
            'FACEBOOK_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.'
        ) from None


def validate_token_encryption_configuration():
    _cipher()


def encrypt_token(token):
    if not token:
        raise TokenEncryptionError('Cannot store an empty Facebook access token.')
    return _cipher().encrypt(token.encode('utf-8')).decode('ascii')


def decrypt_token(encrypted_token):
    try:
        return _cipher().decrypt(encrypted_token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeEncodeError):
        raise TokenEncryptionError(
            'The stored Facebook token cannot be decrypted. Reconnect the Facebook account.'
        ) from None
