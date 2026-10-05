"""Hash de contraseñas (Argon2id), JWT y TOTP."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

_ph = PasswordHasher()  # Argon2id por defecto


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def verify_totp(secret: str, code: str) -> bool:
    """Valida un código TOTP con ventana de ±1 intervalo."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Normaliza datetimes leídos de la BD: SQLite (tests) los devuelve naive."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def create_access_token(admin_id: UUID, role: str) -> str:
    s = get_settings()
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "access",
        "iat": int(now_utc().timestamp()),
        "exp": now_utc() + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm],
                      options={"require": ["exp", "sub"]})


def new_refresh_token() -> tuple[str, str, datetime]:
    """Devuelve (token en claro, hash SHA-256, expiración)."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh(token), now_utc() + timedelta(hours=get_settings().refresh_token_hours)


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
