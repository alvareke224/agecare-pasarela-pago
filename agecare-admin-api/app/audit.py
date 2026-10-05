"""Escritura del registro de auditoría (sección 14)."""
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models

SENSITIVE_KEYS = {"password", "password_hash", "mfa_secret", "refresh_hash", "token"}


def _mask(data: dict | None) -> dict | None:
    if data is None:
        return None
    return {k: ("***" if k in SENSITIVE_KEYS else v) for k, v in data.items()}


async def audit(db: AsyncSession, request: Request, action: str,
                entity_type: str | None = None, entity_id: UUID | None = None,
                before: dict | None = None, after: dict | None = None,
                actor: models.AdminUser | None = None) -> None:
    actor = actor or getattr(request.state, "actor", None)
    db.add(models.AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else None,
        actor_role=actor.role if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=_mask(before),
        after=_mask(after),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:300],
    ))
