"""Cifrado de tbk_user en reposo (Fernet: AES-128-CBC + HMAC-SHA256).

En producción la llave vive en Azure Key Vault y se inyecta como
ADMIN_BILLING_ENCRYPTION_KEY. En dev/tests se deriva del secreto JWT para
que el proyecto arranque sin configuración; config.validate_billing()
impide desplegar a producción sin llave propia.

Rotación de llaves (MultiFernet): siempre se cifra con la llave vigente y se
descifra con ella o con las anteriores de ADMIN_BILLING_ENCRYPTION_KEYS_OLD
(separadas por coma). Fuera de prod se acepta además la llave de desarrollo, para
que los datos demo cargados con ella (agecare_neon_setup.sql) sigan legibles en
staging. `rotate()` re-cifra un valor con la llave vigente.
"""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings


DEV_JWT_SECRET = "cambia-esto-en-produccion"   # valor por defecto de ADMIN_JWT_SECRET


class DecryptionError(Exception):
    """El valor no se pudo descifrar con ninguna llave conocida."""


def _derived_key(jwt_secret: str) -> str:
    digest = hashlib.sha256(("agecare-billing-dev:" + jwt_secret).encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


@lru_cache
def _fernet() -> MultiFernet:
    s = get_settings()
    keys = [s.billing_encryption_key or _derived_key(s.jwt_secret)]
    keys += [k.strip() for k in s.billing_encryption_keys_old.split(",") if k.strip()]
    if s.environment != "prod":
        keys += [_derived_key(s.jwt_secret), _derived_key(DEV_JWT_SECRET)]
    unique = list(dict.fromkeys(keys))
    return MultiFernet([Fernet(k.encode()) for k in unique])


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(token: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(token)).decode()
    except InvalidToken as exc:   # InvalidToken no trae mensaje: se reemplaza por uno útil
        raise DecryptionError("No se pudo descifrar tbk_user: la llave ADMIN_BILLING_ENCRYPTION_KEY "
                              "no es la que se usó al guardarlo (agrega la anterior a "
                              "ADMIN_BILLING_ENCRYPTION_KEYS_OLD).") from exc


def is_current(token: bytes) -> bool:
    """True si el valor ya está cifrado con la llave vigente (la primera)."""
    try:
        _fernet()._fernets[0].decrypt(bytes(token))
        return True
    except InvalidToken:
        return False


def rotate(token: bytes) -> bytes:
    """Re-cifra con la llave vigente (lo usa el cobro para migrar valores antiguos)."""
    return _fernet().rotate(bytes(token))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
