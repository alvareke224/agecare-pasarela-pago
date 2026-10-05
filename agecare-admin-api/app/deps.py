"""Dependencias de seguridad: administrador autenticado y control por rol."""
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.database import get_db
from app.enums import READ, WRITE, AdminRole
from app.errors import forbidden, unauthorized
from app.security import decode_access_token


async def get_current_admin(request: Request,
                            db: Annotated[AsyncSession, Depends(get_db)]) -> models.AdminUser:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise unauthorized()
    try:
        payload = decode_access_token(auth.removeprefix("Bearer ").strip())
    except jwt.PyJWTError:
        raise unauthorized()
    admin = await db.get(models.AdminUser, UUID(payload["sub"]))
    if admin is None or not admin.is_active:
        raise unauthorized()
    request.state.actor = admin
    return admin


CurrentAdmin = Annotated[models.AdminUser, Depends(get_current_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]


def require(module: str, write: bool = False):
    """Factoría de dependencia: exige acceso de lectura o escritura sobre un módulo."""
    allowed = WRITE.get(module, set()) if write else READ.get(module, set())

    async def checker(admin: CurrentAdmin) -> models.AdminUser:
        if AdminRole(admin.role) not in allowed:
            raise forbidden()
        return admin

    return Depends(checker)


def permissions_for(role: AdminRole) -> list[str]:
    """Módulos accesibles (lectura) para un rol; alimenta el menú de la consola."""
    return sorted(m for m, roles in READ.items() if role in roles)
