"""Consola · Facturación: /api/v1/admin/billing/…

Permisos (matriz de app/enums.py):
    billing          lectura: admin, analista, soporte · escritura: solo admin
    billing_refunds  escritura: solo admin (reembolsos, contracargos, ciclo manual)
Con ADMIN_ENVIRONMENT=prod toda escritura exige además 2FA activo (MFA_REQUIRED).
Toda escritura queda en audit_log.
"""
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select

from app import models as core_models
from app.audit import audit
from app.billing.enums import (PAID_CHARGE_STATUSES, PAID_PLANS, CancelReason, ChargeKind, ChargeStatus,
                               SubscriptionStatus)
from app.billing.gateway import PaymentGateway, get_gateway
from app.billing.jobs import run_billing_cycle
from app.billing.metrics_sync import sync_commercial_metrics
from app.billing.models import BillingEvent, Charge, Refund, Subscription
from app.billing.schemas import (AdminCancelIn, AdminChargeDetailOut, AdminChargeOut, AdminSubscriptionDetailOut,
                                 AdminSubscriptionOut, BillingSummaryOut, ChargebackIn, ChargebackOut, ChargeStats, CycleOut, DevAppTokenOut, EventOut, ExtendIn,
                                 PlanRow,
                                 RefundIn, RefundOut, SubscriptionCounts, Upcoming)
from app.billing.service import BillingService
from app.config import get_settings
from app.deps import Db, require
from app.enums import PLAN_NAMES, PlanCode
from app.errors import conflict, invalid, not_found
from app.schemas.common import Page
from app.security import as_utc, now_utc

router = APIRouter(prefix="/billing", tags=["Facturación"])


def svc(db: Db, gateway: PaymentGateway = Depends(get_gateway)) -> BillingService:
    return BillingService(db, gateway)


async def _sub(db: Db, sub_id: UUID) -> Subscription:
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise not_found("La suscripción no existe.")
    return sub


async def _charge(db: Db, charge_id: UUID, lock: bool = False) -> Charge:
    # lock=True: SELECT … FOR UPDATE OF billing_charges para que dos reembolsos simultáneos no superen
    # lo cobrado (OF: las relaciones se cargan con LEFT JOIN y PostgreSQL no bloquea ese lado)
    stmt = select(Charge).where(Charge.id == charge_id)
    if lock:
        stmt = stmt.with_for_update(of=Charge)
    charge = (await db.execute(stmt)).unique().scalar_one_or_none()
    if charge is None:
        raise not_found("El cobro no existe.")
    return charge


def _snapshot(sub: Subscription) -> dict:
    return {"status": sub.status, "plan_code": sub.plan_code, "cancel_at_period_end": sub.cancel_at_period_end,
            "current_period_end": as_utc(sub.current_period_end).isoformat() if sub.current_period_end else None,
            "next_charge_at": as_utc(sub.next_charge_at).isoformat() if sub.next_charge_at else None}


# ---------------------------------------------------------------- KPIs
@router.get("/summary", response_model=BillingSummaryOut, dependencies=[require("billing")])
async def summary(db: Db):
    now = now_utc()
    d30, in7 = now - timedelta(days=30), now + timedelta(days=7)
    by_plan = {(p, s): (n, amount) for p, s, n, amount in (await db.execute(
        select(Subscription.plan_code, Subscription.status, func.count(), func.coalesce(func.sum(Subscription.price_amount), 0))
        .where(Subscription.status.in_((SubscriptionStatus.active, SubscriptionStatus.past_due)))
        .group_by(Subscription.plan_code, Subscription.status))).all()}
    plans = []
    for p in PAID_PLANS:
        active, mrr_a = by_plan.get((p.value, "active"), (0, 0))
        past_due, mrr_p = by_plan.get((p.value, "past_due"), (0, 0))
        plans.append(PlanRow(plan_code=p, plan_name=PLAN_NAMES[p], active=active, past_due=past_due,
                             mrr=int(mrr_a) + int(mrr_p)))

    async def count(*conds) -> int:
        return (await db.execute(select(func.count()).select_from(Subscription).where(*conds))).scalar_one()

    counts = SubscriptionCounts(
        active=sum(r.active for r in plans), past_due=sum(r.past_due for r in plans),
        incomplete=await count(Subscription.status == SubscriptionStatus.incomplete),
        cancel_scheduled=await count(Subscription.status == SubscriptionStatus.active,
                                     Subscription.cancel_at_period_end.is_(True)),
        canceled_30d=await count(Subscription.status == SubscriptionStatus.canceled, Subscription.canceled_at >= d30),
        started_30d=await count(Subscription.started_at >= d30))

    rows = (await db.execute(select(Charge.status, func.count(), func.coalesce(func.sum(Charge.amount), 0),
                                    func.coalesce(func.sum(Charge.refunded_amount), 0))
                             .where(Charge.created_at >= d30).group_by(Charge.status))).all()
    approved = sum(n for st, n, _, _ in rows if st in PAID_CHARGE_STATUSES)
    failed = sum(n for st, n, _, _ in rows if st == ChargeStatus.failed)
    gross = sum(int(a) for st, _, a, _ in rows if st in PAID_CHARGE_STATUSES)
    refunded = (await db.execute(select(func.coalesce(func.sum(Refund.amount), 0))
                                 .where(Refund.status == "succeeded", Refund.created_at >= d30))).scalar_one()
    upcoming = (await db.execute(select(func.count(), func.coalesce(func.sum(Subscription.price_amount), 0)).where(
        Subscription.status == SubscriptionStatus.active, Subscription.cancel_at_period_end.is_(False),
        Subscription.next_charge_at.between(now, in7)))).one()
    unresolved = (await db.execute(select(func.count()).select_from(Charge).where(
        Charge.status.in_((ChargeStatus.pending, ChargeStatus.unknown))))).scalar_one()
    return BillingSummaryOut(
        currency_code=get_settings().billing_currency, mrr=sum(r.mrr for r in plans), plans=plans,
        subscriptions=counts,
        charges_30d=ChargeStats(approved=approved, failed=failed,
                                approval_rate=round(approved / (approved + failed), 4) if approved + failed else None,
                                gross_revenue=gross, refunded=int(refunded), net_revenue=gross - int(refunded)),
        upcoming_7d=Upcoming(count=upcoming[0], amount=int(upcoming[1])),
        unresolved_charges=unresolved, gateway=get_gateway().name, computed_at=now)


# ---------------------------------------------------------------- suscripciones
@router.get("/subscriptions", response_model=Page[AdminSubscriptionOut], dependencies=[require("billing")])
async def list_subscriptions(db: Db,
                             status_f: SubscriptionStatus | None = Query(default=None, alias="status"),
                             plan: PlanCode | None = None,
                             cancel_scheduled: bool | None = None,
                             user_id: UUID | None = None,
                             q: str | None = Query(default=None, max_length=120,
                                                   description="Correo o nombre del pagador"),
                             order: str = Query(default="-created_at",
                                                pattern="^-?(created_at|next_charge_at|price_amount)$"),
                             page: int = Query(default=1, ge=1),
                             page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(Subscription)
    if status_f:
        stmt = stmt.where(Subscription.status == status_f)
    if plan:
        stmt = stmt.where(Subscription.plan_code == plan)
    if cancel_scheduled is not None:
        stmt = stmt.where(Subscription.cancel_at_period_end.is_(cancel_scheduled))
    if user_id:
        stmt = stmt.where(Subscription.user_id == user_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Subscription.user_email.ilike(like), Subscription.user_name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    col = getattr(Subscription, order.lstrip("-"))
    stmt = stmt.order_by(col.desc().nulls_last() if order.startswith("-") else col.asc().nulls_last())
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).unique().scalars().all()
    return Page[AdminSubscriptionOut](items=[AdminSubscriptionOut.of(s) for s in rows], page=page,
                                      page_size=page_size, total=total)


@router.get("/subscriptions/{sub_id}", response_model=AdminSubscriptionDetailOut, dependencies=[require("billing")])
async def subscription_detail(sub_id: UUID, db: Db):
    sub = await _sub(db, sub_id)
    charges = (await db.execute(select(Charge).where(Charge.subscription_id == sub.id)
                                .order_by(Charge.created_at.desc()))).unique().scalars().all()
    events = (await db.execute(select(BillingEvent).where(BillingEvent.subscription_id == sub.id)
                               .order_by(BillingEvent.id.desc()).limit(200))).scalars().all()
    return AdminSubscriptionDetailOut(subscription=AdminSubscriptionOut.of(sub),
                                      charges=[AdminChargeOut.of(c) for c in charges],
                                      events=[EventOut.of(e) for e in events])


@router.post("/subscriptions/{sub_id}/cancel", response_model=AdminSubscriptionOut)
async def admin_cancel(sub_id: UUID, body: AdminCancelIn, request: Request, db: Db,
                       service: BillingService = Depends(svc),
                       admin: core_models.AdminUser = require("billing", write=True)):
    sub = await _sub(db, sub_id)
    before = _snapshot(sub)
    reason = CancelReason(body.reason)
    if body.immediate or sub.status != SubscriptionStatus.active:
        if sub.status in (SubscriptionStatus.canceled, SubscriptionStatus.expired):
            raise conflict("INVALID_TRANSITION", "La suscripción ya terminó.")
        service.cancel_now(sub, reason, note=body.note, actor_type="admin", actor_id=admin.id)
    else:
        service.schedule_cancel(sub, "admin", admin.id, reason, note=body.note)
    await audit(db, request, "billing.subscription_cancel", "billing_subscription", sub.id,
                before=before, after={**_snapshot(sub), "reason": reason.value, "note": body.note})
    return AdminSubscriptionOut.of(sub)


@router.post("/subscriptions/{sub_id}/retry-charge", response_model=AdminChargeOut)
async def admin_retry(sub_id: UUID, request: Request, db: Db, service: BillingService = Depends(svc),
                      admin: core_models.AdminUser = require("billing", write=True)):
    """Reintenta ya el cobro de una suscripción en mora (no consume un reintento programado)."""
    sub = await _sub(db, sub_id)
    if sub.status != SubscriptionStatus.past_due:
        raise conflict("NOT_PAST_DUE", "Solo se reintenta el cobro de suscripciones en mora.")
    if await service._has_inflight(sub.id):
        raise conflict("CHARGE_IN_PROGRESS", "Ya hay un cobro en proceso para esta suscripción.")
    before = _snapshot(sub)
    await audit(db, request, "billing.charge_retry", "billing_subscription", sub.id, before=before)
    charge = await service.charge(sub, ChargeKind.manual, requested_by=admin.id, count_failure=False)
    await audit(db, request, "billing.charge_retry_result", "billing_charge", charge.id,
                after={"status": charge.status, "buy_order": charge.buy_order, "failure_code": charge.failure_code})
    return AdminChargeOut.of(charge)


@router.post("/subscriptions/{sub_id}/extend", response_model=AdminSubscriptionOut)
async def admin_extend(sub_id: UUID, body: ExtendIn, request: Request, db: Db,
                       service: BillingService = Depends(svc),
                       admin: core_models.AdminUser = require("billing", write=True)):
    """Extiende el periodo pagado sin cobro (compensación de soporte)."""
    sub = await _sub(db, sub_id)
    before = _snapshot(sub)
    service.extend(sub, body.days, admin.id, body.reason)
    await audit(db, request, "billing.subscription_extend", "billing_subscription", sub.id,
                before=before, after={**_snapshot(sub), "days": body.days, "reason": body.reason})
    return AdminSubscriptionOut.of(sub)


# ---------------------------------------------------------------- cobros
@router.get("/charges", response_model=Page[AdminChargeOut], dependencies=[require("billing")])
async def list_charges(db: Db,
                       status_f: ChargeStatus | None = Query(default=None, alias="status"),
                       kind: ChargeKind | None = None,
                       subscription_id: UUID | None = None,
                       q: str | None = Query(default=None, max_length=120, description="Orden de compra o correo"),
                       date_from: datetime | None = None,
                       date_to: datetime | None = None,
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=25, ge=1, le=100)):
    if date_from and date_to and date_to < date_from:
        raise invalid("INVALID_RANGE", "La fecha final debe ser posterior a la inicial.")
    stmt = select(Charge).join(Subscription, Charge.subscription_id == Subscription.id)
    if status_f:
        stmt = stmt.where(Charge.status == status_f)
    if kind:
        stmt = stmt.where(Charge.kind == kind)
    if subscription_id:
        stmt = stmt.where(Charge.subscription_id == subscription_id)
    if date_from:
        stmt = stmt.where(Charge.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Charge.created_at <= date_to)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Charge.buy_order.ilike(like), Subscription.user_email.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Charge.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).unique().scalars().all()
    return Page[AdminChargeOut](items=[AdminChargeOut.of(c) for c in rows], page=page,
                                page_size=page_size, total=total)


@router.get("/charges/{charge_id}", response_model=AdminChargeDetailOut, dependencies=[require("billing")])
async def charge_detail(charge_id: UUID, db: Db):
    charge = await _charge(db, charge_id)
    refunds = (await db.execute(select(Refund).where(Refund.charge_id == charge.id)
                                .order_by(Refund.created_at))).scalars().all()
    refundable = charge.amount - charge.refunded_amount if charge.status in PAID_CHARGE_STATUSES else 0
    return AdminChargeDetailOut(charge=AdminChargeOut.of(charge), refunds=[RefundOut.of(r) for r in refunds],
                                refundable_amount=refundable, gateway_response=charge.gateway_response)


@router.post("/charges/{charge_id}/refunds", response_model=RefundOut, status_code=201)
async def refund_charge(charge_id: UUID, body: RefundIn, request: Request, db: Db,
                        service: BillingService = Depends(svc),
                        admin: core_models.AdminUser = require("billing_refunds", write=True)):
    charge = await _charge(db, charge_id, lock=True)
    before = {"status": charge.status, "refunded_amount": charge.refunded_amount}
    await audit(db, request, "billing.refund_requested", "billing_charge", charge.id, before=before,
                after={"amount": body.amount, "reason": body.reason})
    refund = await service.refund(charge, body.amount, body.reason, admin)
    await audit(db, request, "billing.refund", "billing_charge", charge.id, before=before,
                after={"status": charge.status, "refunded_amount": charge.refunded_amount,
                       "refund_type": refund.refund_type, "amount": body.amount, "reason": body.reason})
    return RefundOut.of(refund)


# ---------------------------------------------------------------- contracargos
@router.post("/charges/{charge_id}/chargeback", response_model=ChargebackOut)
async def register_chargeback(charge_id: UUID, body: ChargebackIn, request: Request, db: Db,
                              service: BillingService = Depends(svc),
                              admin: core_models.AdminUser = require("billing_refunds", write=True)):
    """Registra un contracargo informado por Transbank. Idempotente: repetirlo no duplica nada.

    Transbank no notifica contracargos por API (no hay webhooks en Webpay/Oneclick): llegan por el
    portal de comercios. Este endpoint los incorpora: deja el evento `charge.chargeback`, cancela la
    suscripción (motivo fraude) y recalcula las métricas comerciales.
    """
    charge = await _charge(db, charge_id, lock=True)          # serializa registros simultáneos
    if charge.status not in PAID_CHARGE_STATUSES:
        raise conflict("CHARGE_NOT_PAID", "Solo un cobro aprobado puede tener contracargo.")
    amount = body.amount or charge.amount
    if amount > charge.amount:
        raise invalid("CHARGEBACK_EXCEEDS_CHARGE", f"El monto supera lo cobrado ({charge.amount} CLP).")
    sub = await _sub(db, charge.subscription_id)
    existing = (await db.execute(select(BillingEvent.id).where(
        BillingEvent.charge_id == charge.id, BillingEvent.type == "charge.chargeback"))).first()
    if existing:
        return ChargebackOut(charge_id=charge.id, amount=amount, already_registered=True,
                             subscription=AdminSubscriptionOut.of(sub))
    before = _snapshot(sub)
    service.event("charge.chargeback", sub=sub, charge=charge, actor_type="admin", actor_id=admin.id,
                  amount=amount, tbk_reference=body.tbk_reference, buy_order=charge.buy_order)
    if body.cancel_subscription and sub.status in (SubscriptionStatus.incomplete, SubscriptionStatus.active,
                                                   SubscriptionStatus.past_due):
        service.cancel_now(sub, CancelReason.fraud, note=f"Contracargo: {body.note}"[:500],
                           actor_type="admin", actor_id=admin.id)
    await audit(db, request, "billing.chargeback", "billing_charge", charge.id, before=before,
                after={**_snapshot(sub), "amount": amount, "tbk_reference": body.tbk_reference, "note": body.note})
    await db.commit()
    await sync_commercial_metrics(db)                          # el MRR del dashboard baja de inmediato
    await db.commit()
    await db.refresh(sub)
    return ChargebackOut(charge_id=charge.id, amount=amount, already_registered=False,
                         subscription=AdminSubscriptionOut.of(sub))


# ---------------------------------------------------------------- job manual (QA / operación)
@router.post("/jobs/billing-cycle", response_model=CycleOut)
async def run_cycle(request: Request, db: Db, gateway: PaymentGateway = Depends(get_gateway),
                    admin: core_models.AdminUser = require("billing_refunds", write=True)):
    """Ejecuta ahora el ciclo de facturación (en producción corre cada 15 min como job)."""
    stats = await run_billing_cycle(db, gateway)
    await audit(db, request, "billing.cycle_run", "billing_job", None, after=stats.as_dict())
    return CycleOut(**stats.as_dict())


# ---------------------------------------------------------------- usuario de prueba (QA / demo)
@router.post("/dev/app-token", response_model=DevAppTokenOut)
async def dev_app_token(request: Request, db: Db,
                        admin: core_models.AdminUser = require("billing", write=True)):
    """Emite un JWT de usuario de la app para el *Checkout de prueba* (demos en Vercel, QA).

    Crea un usuario ficticio nuevo con una familia y un proveedor propios, así se pueden
    probar los tres planes. Deshabilitado en producción (ADMIN_ENVIRONMENT=prod).
    """
    import secrets as _secrets
    import uuid as _uuid
    from datetime import timezone as _tz

    import jwt as _jwt
    s = get_settings()
    if s.environment == "prod":
        raise not_found()
    tag = _secrets.token_hex(3)
    user_id, family_id, provider_id = _uuid.uuid4(), _uuid.uuid4(), _uuid.uuid4()
    payload = {"sub": str(user_id), "email": f"prueba.{tag}@demo.agecare.cl", "name": f"Usuario de prueba {tag}",
               "family_ids": [str(family_id)], "provider_id": str(provider_id),
               "exp": now_utc().astimezone(_tz.utc) + timedelta(hours=12)}
    if s.app_jwt_audience:
        payload["aud"] = s.app_jwt_audience
    token = _jwt.encode(payload, s.app_jwt_secret, algorithm=s.app_jwt_algorithm)
    await audit(db, request, "billing.dev_app_token", "app_user", user_id, after={"email": payload["email"]})
    return {"token": token, "user_id": str(user_id), "email": payload["email"],
            "family_id": str(family_id), "provider_id": str(provider_id)}
