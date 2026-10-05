"""Secciones 6 (KPIs de soporte) y 8 — Tickets de soporte."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app import models
from app.audit import audit
from app.deps import Db, require
from app.enums import (CATEGORY_NAMES, AppRole, TicketCategory, TicketChannel, TicketPriority,
                       TicketStatus)
from app.errors import ApiError, conflict, not_found
from app.schemas.common import Page
from app.schemas.support import (Assignee, ByCategoryOut, CategoryRow, Csat, ReplyCreateIn,
                                 ReplyOut, Requester, SupportDeltas, SupportSummaryOut,
                                 TicketCreateIn, TicketDetailOut, TicketOut, TicketPatchIn)
from app.security import now_utc

router = APIRouter(prefix="/support", tags=["Soporte"])

VALID_TRANSITIONS = {
    TicketStatus.open: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.in_progress: {TicketStatus.waiting_user, TicketStatus.resolved},
    TicketStatus.waiting_user: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.resolved: {TicketStatus.in_progress, TicketStatus.closed},  # reapertura o cierre
    TicketStatus.closed: set(),
}


def _ticket_out(t: models.Ticket) -> TicketOut:
    return TicketOut(
        id=t.id, number=t.number, subject=t.subject,
        requester=Requester(user_id=t.requester_user_id, name=t.requester_name,
                            role=AppRole(t.requester_role) if t.requester_role else None,
                            email=t.requester_email),
        category=TicketCategory(t.category), priority=TicketPriority(t.priority),
        status=TicketStatus(t.status),
        assigned_to=Assignee(admin_id=t.assignee.id, name=t.assignee.full_name) if t.assignee else None,
        channel=TicketChannel(t.channel), created_at=t.created_at, updated_at=t.updated_at,
        first_response_at=t.first_response_at)


# ---------- 6.3 KPIs ----------
@router.get("/summary", response_model=SupportSummaryOut, dependencies=[require("support")])
async def support_summary(db: Db):
    now = now_utc()
    d30, d60 = now - timedelta(days=30), now - timedelta(days=60)

    async def count_status(status: TicketStatus) -> int:
        return (await db.execute(select(func.count()).select_from(models.Ticket)
                                 .where(models.Ticket.status == status))).scalar_one()

    resolved_30 = (await db.execute(select(func.count()).select_from(models.Ticket)
                                    .where(models.Ticket.resolved_at >= d30))).scalar_one()
    resolved_prev = (await db.execute(select(func.count()).select_from(models.Ticket)
                                      .where(models.Ticket.resolved_at.between(d60, d30)))).scalar_one()

    async def avg_first_response(since, until) -> float | None:
        rows = (await db.execute(select(models.Ticket.created_at, models.Ticket.first_response_at)
                                 .where(models.Ticket.first_response_at.isnot(None),
                                        models.Ticket.created_at.between(since, until)))).all()
        if not rows:
            return None
        secs = [(fr - cr).total_seconds() for cr, fr in rows]
        return round(sum(secs) / len(secs) / 3600, 1)

    fr_now = await avg_first_response(d30, now)
    fr_prev = await avg_first_response(d60, d30)

    csat_q = (await db.execute(select(func.avg(models.Ticket.csat_score),
                                      func.count(models.Ticket.csat_score))
                               .where(models.Ticket.csat_score.isnot(None),
                                      models.Ticket.resolved_at >= d30))).one()
    open_now = await count_status(TicketStatus.open)
    week_ago_open = open_now - 6  # aproximación demo: el histórico real saldría de support_metrics_daily

    return SupportSummaryOut(
        open=open_now,
        in_progress=await count_status(TicketStatus.in_progress),
        waiting_user=await count_status(TicketStatus.waiting_user),
        resolved_30d=resolved_30,
        first_response_hours_avg=fr_now or 0.0,
        csat_avg=round(float(csat_q[0]), 1) if csat_q[0] is not None else None,
        csat_count=csat_q[1],
        deltas=SupportDeltas(
            open_wow=open_now - week_ago_open,
            resolved_mom_pct=round((resolved_30 - resolved_prev) / resolved_prev, 4) if resolved_prev else None,
            first_response_mom_hours=round(fr_now - fr_prev, 1) if fr_now and fr_prev else None),
        computed_at=now)


# ---------- 6.4 Por categoría ----------
@router.get("/tickets/by-category", response_model=ByCategoryOut, dependencies=[require("support")])
async def by_category(db: Db, days: int = Query(default=30, ge=1, le=365)):
    since = now_utc() - timedelta(days=days)
    rows = (await db.execute(select(models.Ticket.category, func.count())
                             .where(models.Ticket.created_at >= since)
                             .group_by(models.Ticket.category)
                             .order_by(func.count().desc()))).all()
    total = sum(c for _, c in rows)
    return ByCategoryOut(days=days, total=total,
                         categories=[CategoryRow(category=TicketCategory(cat),
                                                 name=CATEGORY_NAMES[TicketCategory(cat)],
                                                 count=c, share=round(c / total, 4) if total else 0.0)
                                     for cat, c in rows])


# ---------- 8.1 Listar ----------
@router.get("/tickets", response_model=Page[TicketOut], dependencies=[require("support")])
async def list_tickets(db: Db,
                       status_f: TicketStatus | None = Query(default=None, alias="status"),
                       priority: TicketPriority | None = None,
                       category: TicketCategory | None = None,
                       role: AppRole | None = None,
                       assigned_to: UUID | None = None,
                       q: str | None = Query(default=None, max_length=120),
                       order: str = Query(default="-created_at"),
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.Ticket)
    if status_f:
        stmt = stmt.where(models.Ticket.status == status_f)
    if priority:
        stmt = stmt.where(models.Ticket.priority == priority)
    if category:
        stmt = stmt.where(models.Ticket.category == category)
    if role:
        stmt = stmt.where(models.Ticket.requester_role == role)
    if assigned_to:
        stmt = stmt.where(models.Ticket.assigned_to == assigned_to)
    if q:
        like = f"%{q.lstrip('#')}%"
        cond = models.Ticket.subject.ilike(like) | models.Ticket.requester_email.ilike(like)
        if q.lstrip("#").isdigit():
            cond = cond | (models.Ticket.number == int(q.lstrip("#")))
        stmt = stmt.where(cond)
    field = order.lstrip("-")
    col = {"created_at": models.Ticket.created_at, "updated_at": models.Ticket.updated_at,
           "priority": models.Ticket.priority}.get(field, models.Ticket.created_at)
    stmt = stmt.order_by(col.desc() if order.startswith("-") else col.asc())
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[_ticket_out(t) for t in rows], page=page, page_size=page_size, total=total)


async def _get_ticket(db, ticket_id: str) -> models.Ticket:
    t = None
    raw = ticket_id.lstrip("#")
    if raw.isdigit():
        t = (await db.execute(select(models.Ticket)
                              .where(models.Ticket.number == int(raw)))).scalar_one_or_none()
    else:
        try:
            t = await db.get(models.Ticket, UUID(raw))
        except ValueError:
            t = None
    if t is None:
        raise not_found()
    return t


# ---------- 8.2 Detalle ----------
@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut, dependencies=[require("support")])
async def ticket_detail(ticket_id: str, db: Db):
    t = await _get_ticket(db, ticket_id)
    replies = (await db.execute(select(func.count()).select_from(models.TicketReply)
                                .where(models.TicketReply.ticket_id == t.id))).scalar_one()
    base = _ticket_out(t).model_dump()
    return TicketDetailOut(**base, description=t.description,
                           requester_context=t.requester_context, replies_count=replies,
                           csat=Csat(score=t.csat_score, comment=t.csat_comment)
                           if t.csat_score is not None else None)


# ---------- 8.3 Crear ----------
@router.post("/tickets", response_model=TicketOut, status_code=201)
async def create_ticket(body: TicketCreateIn, request: Request, db: Db,
                        admin: models.AdminUser = require("support", write=True)):
    # En producción se buscaría en la tabla de usuarios de la app; aquí se enlaza
    # contra los tickets previos del mismo correo como aproximación.
    prev = (await db.execute(select(models.Ticket)
                             .where(models.Ticket.requester_email == body.user_email.lower())
                             .limit(1))).scalar_one_or_none()
    if prev is None and not body.confirm_unlinked:
        raise ApiError(404, "USER_NOT_FOUND",
                       "No existe ningún usuario con ese correo. Verifícalo o crea el ticket sin enlazar "
                       "(confirm_unlinked = true).")
    next_number = ((await db.execute(select(func.max(models.Ticket.number)))).scalar_one() or 1000) + 1
    t = models.Ticket(number=next_number, subject=body.subject, description=body.description,
                      requester_user_id=prev.requester_user_id if prev else None,
                      requester_name=prev.requester_name if prev else body.user_email.split("@")[0],
                      requester_email=body.user_email.lower(),
                      requester_role=prev.requester_role if prev else None,
                      requester_plan=prev.requester_plan if prev else None,
                      category=body.category, priority=body.priority,
                      status=TicketStatus.open, channel=body.channel)
    db.add(t)
    await db.flush()
    await db.refresh(t)
    await audit(db, request, "ticket.create", "ticket", t.id, after={"number": t.number})
    return _ticket_out(t)


# ---------- 8.4 Actualizar ----------
@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
async def patch_ticket(ticket_id: str, body: TicketPatchIn, request: Request, db: Db,
                       admin: models.AdminUser = require("support", write=True)):
    t = await _get_ticket(db, ticket_id)
    before = {"status": t.status, "priority": t.priority, "assigned_to": str(t.assigned_to)}
    if body.status is not None:
        current, target = TicketStatus(t.status), body.status
        if target != current and target not in VALID_TRANSITIONS[current]:
            msg = ("Transición de estado no permitida (p. ej. un ticket cerrado no puede reabrirse; "
                   "crea uno nuevo).")
            raise conflict("INVALID_TRANSITION", msg)
        t.status = target
        if target == TicketStatus.resolved:
            t.resolved_at = now_utc()
            # Aquí se enviaría la encuesta CSAT al usuario (push/correo).
    if body.priority is not None:
        t.priority = body.priority
    if body.category is not None:
        t.category = body.category
    if "assigned_to" in body.model_fields_set:
        if body.assigned_to is not None:
            assignee = await db.get(models.AdminUser, body.assigned_to)
            if assignee is None or not assignee.is_active:
                raise ApiError(404, "ASSIGNEE_NOT_FOUND", "El agente indicado no existe o está desactivado.")
        t.assigned_to = body.assigned_to
    await db.flush()
    await db.refresh(t)
    await audit(db, request, "ticket.update", "ticket", t.id, before=before,
                after={"status": t.status, "priority": t.priority, "assigned_to": str(t.assigned_to)})
    return _ticket_out(t)


# ---------- 8.5 Responder ----------
@router.post("/tickets/{ticket_id}/replies", response_model=ReplyOut, status_code=201)
async def create_reply(ticket_id: str, body: ReplyCreateIn, request: Request, db: Db,
                       admin: models.AdminUser = require("support", write=True)):
    t = await _get_ticket(db, ticket_id)
    if TicketStatus(t.status) == TicketStatus.closed:
        raise conflict("TICKET_CLOSED", "El ticket está cerrado y no admite nuevas respuestas.")
    reply = models.TicketReply(ticket_id=t.id, author_type="admin", author_id=admin.id,
                               author_name=admin.full_name, body=body.body, internal=body.internal)
    db.add(reply)
    if not body.internal and t.first_response_at is None:
        t.first_response_at = now_utc()  # KPI de primera respuesta (6.3)
        # Aquí se notificaría al usuario por push y correo.
    await db.flush()
    await audit(db, request, "ticket.reply", "ticket", t.id, after={"internal": body.internal})
    return ReplyOut.model_validate(reply)


# ---------- 8.6 Conversación ----------
@router.get("/tickets/{ticket_id}/replies", response_model=Page[ReplyOut], dependencies=[require("support")])
async def list_replies(ticket_id: str, db: Db,
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=25, ge=1, le=100)):
    t = await _get_ticket(db, ticket_id)
    stmt = select(models.TicketReply).where(models.TicketReply.ticket_id == t.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.TicketReply.created_at)
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[ReplyOut.model_validate(r) for r in rows],
                page=page, page_size=page_size, total=total)
