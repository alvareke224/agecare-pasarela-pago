"""Conciliación de la facturación real con las métricas comerciales del dashboard.

Reemplaza los valores simulados de los planes de pago por datos transaccionales:

  · metrics_plan_snapshot (as_of = hoy): por cada plan de pago, unidades que pagan
    (suscripciones active + past_due), precio vigente, MRR (suma de los precios congelados
    de esas suscripciones) y churn de 30 días. La fila del plan Gratuito no es de facturación:
    se conserva la última calculada por la ingesta de uso de la app.
  · metrics_daily_users (day = hoy): paying_users_eod y mrr_clp_eod. El resto de las
    columnas (altas, descargas, activos) son de la ingesta de la app y no se tocan.

Es idempotente (upsert por fecha y plan): correrlo varias veces en el día deja el mismo
resultado. Lo ejecuta el ciclo de facturación (cron diario en Vercel, cada 15 min en Docker,
botón «Ejecutar ahora») y `python -m scripts.billing_cycle`.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as core_models
from app.billing.enums import PAID_PLANS, PREMIUM_STATUSES, CancelReason, SubscriptionStatus
from app.billing.models import Subscription
from app.enums import PlanCode
from app.logs import log_event
from app.security import now_utc

log = logging.getLogger("agecare.billing.metrics")


@dataclass
class MetricsSyncResult:
    as_of: date
    paying_units: int = 0
    mrr_clp: int = 0
    plans: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


async def _plan_prices(db: AsyncSession) -> dict[str, int]:
    setting = await db.get(core_models.SystemSetting, "plan_prices")
    value = (setting.value or {}) if setting else {}
    return {k: v for k, v in value.items() if isinstance(v, int)}


async def sync_commercial_metrics(db: AsyncSession, now: datetime | None = None) -> MetricsSyncResult:
    """Escribe en las tablas de métricas el estado real de la facturación (sin commit)."""
    now = now or now_utc()
    today = now.date()
    since = now - timedelta(days=30)
    result = MetricsSyncResult(as_of=today)
    prices = await _plan_prices(db)

    live = dict((await db.execute(
        select(Subscription.plan_code, func.count()).where(Subscription.status.in_(PREMIUM_STATUSES))
        .group_by(Subscription.plan_code))).all())
    mrr = dict((await db.execute(
        select(Subscription.plan_code, func.coalesce(func.sum(Subscription.price_amount), 0))
        .where(Subscription.status.in_(PREMIUM_STATUSES)).group_by(Subscription.plan_code))).all())
    churned = dict((await db.execute(
        select(Subscription.plan_code, func.count()).where(
            Subscription.status == SubscriptionStatus.canceled, Subscription.canceled_at >= since,
            Subscription.cancel_reason != CancelReason.plan_change)
        .group_by(Subscription.plan_code))).all())

    existing = {r.plan_code: r for r in (await db.execute(
        select(core_models.MetricsPlanSnapshot).where(core_models.MetricsPlanSnapshot.as_of == today)
    )).scalars().all()}

    for plan in PAID_PLANS:
        code = plan.value
        units, plan_mrr, lost = int(live.get(code, 0)), int(mrr.get(code, 0)), int(churned.get(code, 0))
        base = units + lost   # unidades al inicio de la ventana ≈ las que siguen + las que se fueron
        churn = round(lost / base, 4) if base else 0.0
        row = existing.get(code) or core_models.MetricsPlanSnapshot(as_of=today, plan_code=code)
        row.users, row.price_clp, row.mrr_clp, row.monthly_churn = units, prices.get(code), plan_mrr, churn
        row.computed_at = now
        db.add(row)
        result.plans[code] = {"units": units, "mrr_clp": plan_mrr, "monthly_churn": churn}
        result.paying_units += units
        result.mrr_clp += plan_mrr

    # Fila del plan Gratuito: la calcula la ingesta de uso; si hoy no existe se arrastra la última
    if PlanCode.free.value not in existing:
        last_free = (await db.execute(
            select(core_models.MetricsPlanSnapshot).where(core_models.MetricsPlanSnapshot.plan_code == PlanCode.free.value)
            .order_by(core_models.MetricsPlanSnapshot.as_of.desc()).limit(1))).scalar_one_or_none()
        db.add(core_models.MetricsPlanSnapshot(
            as_of=today, plan_code=PlanCode.free.value, users=last_free.users if last_free else 0,
            price_clp=None, mrr_clp=0, monthly_churn=last_free.monthly_churn if last_free else 0.0,
            computed_at=now))

    day = await db.get(core_models.MetricsDailyUsers, today)
    if day is None:
        prev = (await db.execute(select(core_models.MetricsDailyUsers).where(core_models.MetricsDailyUsers.day < today)
                                 .order_by(core_models.MetricsDailyUsers.day.desc()).limit(1))).scalar_one_or_none()
        day = core_models.MetricsDailyUsers(day=today, signups=0, cancellations=0, downloads=0,
                                            active_users_eod=prev.active_users_eod if prev else 0)
    day.paying_users_eod, day.mrr_clp_eod, day.computed_at = result.paying_units, result.mrr_clp, now
    db.add(day)
    await db.flush()
    log_event(log, "job.metrics_sync", "Métricas comerciales conciliadas con la facturación",
              as_of=today.isoformat(), paying_units=result.paying_units, mrr_clp=result.mrr_clp)
    return result
