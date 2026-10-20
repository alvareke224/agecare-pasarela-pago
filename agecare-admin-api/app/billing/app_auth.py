"""Autenticación de usuarios finales de la app para los endpoints /api/v1/billing.

La API principal de AgeCare emite el JWT del usuario; este módulo solo lo
verifica (HS256 con secreto compartido o RS256 con llave pública) y lee:

    sub          uuid del usuario (app.users.id)          obligatorio
    email        correo                                    obligatorio
    name         nombre visible                            opcional
    family_ids   familias que administra el usuario        para el plan Platino
    provider_id  proveedor (agencia/cuidadora) que opera   para el plan Proveedor

Contrato a confirmar con el equipo de la API principal (ver README).
"""
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request

from app.config import get_settings
from app.errors import ApiError
from app.logs import actor_var


@dataclass
class AppUser:
    id: UUID
    email: str
    name: str | None = None
    family_ids: list[UUID] = field(default_factory=list)
    provider_id: UUID | None = None


def _unauthorized() -> ApiError:
    return ApiError(401, "UNAUTHORIZED", "Tu sesión expiró. Vuelve a iniciar sesión en la app.")


def decode_app_token(token: str) -> AppUser:
    s = get_settings()
    options: dict = {"require": ["exp", "sub", "email"]}
    try:
        if s.app_jwt_audience:
            payload = jwt.decode(token, s.app_jwt_secret, algorithms=[s.app_jwt_algorithm],
                                 audience=s.app_jwt_audience, options=options)
        else:
            payload = jwt.decode(token, s.app_jwt_secret, algorithms=[s.app_jwt_algorithm],
                                 options={**options, "verify_aud": False})
        return AppUser(
            id=UUID(payload["sub"]),
            email=payload["email"],
            name=payload.get("name"),
            family_ids=[UUID(f) for f in payload.get("family_ids", [])],
            provider_id=UUID(payload["provider_id"]) if payload.get("provider_id") else None,
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise _unauthorized()


async def get_app_user(request: Request) -> AppUser:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise _unauthorized()
    user = decode_app_token(auth.removeprefix("Bearer ").strip())
    request.state.actor_ref = f"user:{user.id}"
    actor_var.set(request.state.actor_ref)
    return user


CurrentAppUser = Annotated[AppUser, Depends(get_app_user)]
