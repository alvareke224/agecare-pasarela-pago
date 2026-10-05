"""Sección 11 — Moderación."""
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app import models
from app.audit import audit
from app.deps import Db, require
from app.enums import ModerationItemType, ModerationStatus, RejectReason
from app.errors import conflict, invalid, not_found
from app.schemas.common import Page
from app.schemas.operation import ApproveIn, ModerationItemOut, RejectIn
from app.security import now_utc

router = APIRouter(prefix="/moderation", tags=["Moderación"])


def _out(m: models.ModerationItem) -> ModerationItemOut:
    return ModerationItemOut(
        id=m.id, type=ModerationItemType(m.type), content=m.content,
        author={"user_id": str(m.author_user_id) if m.author_user_id else None,
                "name": m.author_name, "role": m.author_role},
        reported_by=m.reported_by, report_reason=m.report_reason,
        status=ModerationStatus(m.status), decided_by_name=m.decided_by_name,
        decided_at=m.decided_at, created_at=m.created_at)


async def _get_pending(db, item_id: UUID) -> models.ModerationItem:
    m = await db.get(models.ModerationItem, item_id)
    if m is None:
        raise not_found()
    if ModerationStatus(m.status) != ModerationStatus.pending:
        raise conflict("ALREADY_MODERATED", "Este elemento ya fue moderado por otra persona. Recarga la cola.")
    return m


# ---------- 11.1 Cola ----------
@router.get("/queue", response_model=Page[ModerationItemOut], dependencies=[require("moderation")])
async def queue(db: Db,
                type_f: ModerationItemType | None = Query(default=None, alias="type"),
                status_f: ModerationStatus = Query(default=ModerationStatus.pending, alias="status"),
                page: int = Query(default=1, ge=1),
                page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.ModerationItem).where(models.ModerationItem.status == status_f)
    if type_f:
        stmt = stmt.where(models.ModerationItem.type == type_f)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    # seguridad primero, luego por antigüedad
    rows = (await db.execute(stmt.order_by(models.ModerationItem.is_safety.desc(),
                                           models.ModerationItem.created_at.asc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[_out(m) for m in rows], page=page, page_size=page_size, total=total)


# ---------- 11.2 Aprobar ----------
@router.post("/queue/{item_id}/approve", response_model=ModerationItemOut)
async def approve(item_id: UUID, body: ApproveIn, request: Request, db: Db,
                  admin: models.AdminUser = require("moderation", write=True)):
    m = await _get_pending(db, item_id)
    m.status = ModerationStatus.approved
    m.decision_note = body.note
    m.decided_by = admin.id
    m.decided_by_name = admin.full_name
    m.decided_at = now_utc()
    # Una reseña aprobada pasaría a publicada en el marketplace; un reporte se cierra sin acción.
    await audit(db, request, "moderation.approve", "moderation_item", m.id)
    return _out(m)


# ---------- 11.3 Rechazar ----------
@router.post("/queue/{item_id}/reject", response_model=ModerationItemOut)
async def reject(item_id: UUID, body: RejectIn, request: Request, db: Db,
                 admin: models.AdminUser = require("moderation", write=True)):
    if body.reason_code == RejectReason.other and not body.note:
        raise invalid("NOTE_REQUIRED", "Con motivo «other» debes detallar la razón del rechazo.")
    m = await _get_pending(db, item_id)
    m.status = ModerationStatus.rejected
    m.reject_reason_code = body.reason_code
    m.decision_note = body.note
    m.decided_by = admin.id
    m.decided_by_name = admin.full_name
    m.decided_at = now_utc()
    # El autor recibiría una notificación con el motivo; 3 rechazos "offensive" en 90 días
    # escalan un caso al admin (job de reglas, fuera de esta API).
    await audit(db, request, "moderation.reject", "moderation_item", m.id,
                after={"reason": body.reason_code})
    return _out(m)
