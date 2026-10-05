"""Cifrado de tbk_user en reposo (Fernet: AES-128-CBC + HMAC-SHA256).

En producción la llave vive en Azure Key Vault y se inyecta como
ADMIN_BILLING_ENCRYPTION_KEY. En dev/tests se deriva del secreto JWT para
que el proyecto arranque sin configuración; config.validate_billing()
impide desplegar a producción sin llave propia.
"""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    key = s.billing_encryption_key
    if not key:
        digest = hashlib.sha256(("agecare-billing-dev:" + s.jwt_secret).encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(bytes(token)).decode()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
