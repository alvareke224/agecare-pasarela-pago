"""Ciclo de facturación (job `billing_cycle`, cada 15 min en producción).

Orden de cada corrida:
  1. Conciliar cobros pending/unknown con más de N segundos (GET status en Transbank).
  2. Expirar suscripciones `incomplete` viejas e inscripciones abandonadas.
  3. Cerrar suscripciones con cancelación programada cuyo periodo terminó.
  4. Cobrar renovaciones y reintentos vencidos (next_charge_at <= ahora).

Seguro para varios workers: cada suscripción se toma con
SELECT … FOR UPDATE SKIP LOCKED y el índice único parcial impide dos cobros en
vuelo para la misma suscripción. Un error en una suscripción no detiene el resto.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.enums import (CancelReason, ChargeKind, ChargeStatus, PaymentMethodStatus,
                               SubscriptionStatus)
from app.billing.gateway import PaymentGateway
from app.logs import log_event
from app.billing.models import Charge, PaymentMethod, Subscription
from app.billing.service import BillingService
from app.errors import ApiError

log = logging.getLogger("agecare.billing.jobs")
MAX_PER_RUN = 500


@dataclass
class CycleStats:
    reconciled: int = 0
    renewed: int = 0
    recovered: int = 0
    failed: int = 0
    unknown: int = 0
    canceled: int = 0
    expired_subscriptions: int = 0
    expired_inscriptions: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


async def run_billing_cycle(db: AsyncSession, gateway: PaymentGateway, now=None) -> CycleStats:
    started = time.perf_counter()
    svc = BillingService(db, gateway, now=now)
    now = svc.now()
    stats = CycleStats()

    # 1 · Conciliación
    cutoff = now - timedelta(seconds=svc.settings.billing_reconcile_after_seconds)
    pending = (await db.execute(select(Charge).where(
        Charge.status.in_((ChargeStatus.pending, ChargeStatus.unknown)), Charge.created_at <= cutoff)
        .order_by(Charge.created_at).limit(MAX_PER_RUN))).scalars().all()
    for charge in pending:
        try:
            outcome = await svc.reconcile(charge)
            if outcome != "still_unknown":
                stats.reconciled += 1
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            stats.errors.append(f"reconcile {charge.buy_order}: {exc}")
            log.exception("Error conciliando %s", charge.buy_order)

    # 2 · Expiración
    ttl = timedelta(hours=svc.settings.billing_incomplete_ttl_hours)
    stale = (await db.execute(select(Subscription).where(
        Subscription.status == SubscriptionStatus.incomplete, Subscription.created_at <= now - ttl,
        ~exists().where(Charge.subscription_id == Subscription.id,
                        Charge.status.in_((ChargeStatus.pending, ChargeStatus.unknown)))))).scalars().all()
    for sub in stale:
        svc.transition(sub, SubscriptionStatus.expired)
        svc.event("subscription.expired", sub=sub)
        stats.expired_subscriptions += 1
    abandoned = (await db.execute(select(PaymentMethod).where(
        PaymentMethod.status == PaymentMethodStatus.pending,
        PaymentMethod.inscription_expires_at <= now - timedelta(minutes=5)))).scalars().all()
    for pm in abandoned:
        pm.status, pm.failure_code = PaymentMethodStatus.aborted.value, "TBK_TIMEOUT"
        svc.event("payment_method.aborted", user_id=pm.user_id, payment_method_id=str(pm.id),
                  failure_code="TBK_TIMEOUT")
        stats.expired_inscriptions += 1
    await db.commit()

    # 3 y 4 · Vencimientos: una suscripción por vez, bloqueada
    processed: set[UUID] = set()
    while len(processed) < MAX_PER_RUN:
        stmt = (select(Subscription)
                .where(Subscription.status.in_((SubscriptionStatus.active, SubscriptionStatus.past_due)),
                       Subscription.next_charge_at.is_not(None), Subscription.next_charge_at <= now,
                       ~exists().where(Charge.subscription_id == Subscription.id,
                                       Charge.status.in_((ChargeStatus.pending, ChargeStatus.unknown))))
                .order_by(Subscription.next_charge_at).limit(1)
                .with_for_update(skip_locked=True, of=Subscription))
        if processed:
            stmt = stmt.where(Subscription.id.not_in(processed))
        sub = (await db.execute(stmt)).unique().scalars().first()
        if sub is None:
            break
        processed.add(sub.id)
        try:
            await _process_due(svc, sub, stats)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            stats.errors.append(f"subscription {sub.id}: {exc}")
            log.exception("Error procesando la suscripción %s", sub.id)
    log_event(log, "job.billing_cycle", "Ciclo de facturación terminado",
              logging.ERROR if stats.errors else logging.INFO,
              duration_ms=round((time.perf_counter() - started) * 1000, 1), **stats.as_dict())
    return stats


async def _process_due(svc: BillingService, sub: Subscription, stats: CycleStats) -> None:
    if sub.cancel_at_period_end:
        svc.cancel_now(sub, CancelReason(sub.cancel_reason or CancelReason.user_request), note=sub.cancel_note)
        stats.canceled += 1
        return
    was_past_due = sub.status == SubscriptionStatus.past_due
    if not was_past_due:
        await svc.apply_pending_plan(sub)
    pm = await svc.db.get(PaymentMethod, sub.payment_method_id) if sub.payment_method_id else None
    if pm is None or pm.status != PaymentMethodStatus.active:
        # Sin tarjeta no hay cobro posible: cuenta como intento fallido
        svc.event("charge.skipped", sub=sub, reason="no_active_payment_method")
        await svc.register_failure(sub)
        stats.failed += 1
        stats.canceled += sub.status == SubscriptionStatus.canceled
        return
    try:
        charge = await svc.charge(sub, ChargeKind.retry if was_past_due else ChargeKind.renewal)
    except ApiError as exc:
        stats.errors.append(f"subscription {sub.id}: {exc.code}")
        return
    if charge.status == ChargeStatus.authorized:
        if was_past_due:
            stats.recovered += 1
        else:
            stats.renewed += 1
    elif charge.status == ChargeStatus.unknown:
        stats.unknown += 1
    else:
        stats.failed += 1
        stats.canceled += sub.status == SubscriptionStatus.canceled
