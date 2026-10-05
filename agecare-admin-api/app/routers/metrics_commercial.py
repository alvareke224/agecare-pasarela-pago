"""Sección 4 — Uso comercial (lectura de tablas agregadas)."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app import models
from app.deps import Db, require
from app.enums import PLAN_NAMES, PeriodKey, PlanCode
from app.errors import invalid
from app.periods import Period, buckets_for, resolve
from app.schemas.metrics import (BucketOut, CommercialDeltas, CommercialSummaryOut, FunnelOut,
                                 FunnelStage, PeriodOut, PlanRow, PlansOut, PlanTotals,
                                 RegistrationsOut)

router = APIRouter(prefix="/metrics/commercial", tags=["Uso comercial"],
                   dependencies=[require("metrics")])

INVALID_PERIOD = ("INVALID_PERIOD", "El periodo indicado no es válido. Usa uno de los valores de PeriodKey.")


def _period_out(p: Period) -> PeriodOut:
    return PeriodOut(key=p.key, start_date=p.start, end_date=p.end)


async def _range_sums(db, start: date, end: date) -> tuple[int, int, int]:
    """(signups, cancellations, downloads) en el rango [start, end]."""
    q = await db.execute(
        select(func.coalesce(func.sum(models.MetricsDailyUsers.signups), 0),
               func.coalesce(func.sum(models.MetricsDailyUsers.cancellations), 0),
               func.coalesce(func.sum(models.MetricsDailyUsers.downloads), 0))
        .where(models.MetricsDailyUsers.day.between(start, end)))
    return tuple(q.one())


async def _latest_row(db, on_or_before: date) -> models.MetricsDailyUsers | None:
    q = await db.execute(select(models.MetricsDailyUsers)
                         .where(models.MetricsDailyUsers.day <= on_or_before)
                         .order_by(models.MetricsDailyUsers.day.desc()).limit(1))
    return q.scalar_one_or_none()


def _pct(cur: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return round((cur - prev) / prev, 4)


# ---------- 4.1 Summary ----------
@router.get("/summary", response_model=CommercialSummaryOut)
async def commercial_summary(db: Db, period: PeriodKey = Query(...)):
    p = resolve(period)
    signups, cancellations, downloads = await _range_sums(db, p.start, p.end)
    latest = await _latest_row(db, p.end)
    if latest is None:
        raise invalid(*INVALID_PERIOD)

    prev = p.previous()
    prev_signups, _, _ = await _range_sums(db, prev.start, prev.end)
    prev_latest = await _latest_row(db, prev.end)
    start_row = await _latest_row(db, p.start - timedelta(days=1))
    active_at_start = start_row.active_users_eod if start_row else latest.active_users_eod

    return CommercialSummaryOut(
        period=_period_out(p),
        downloads=downloads,
        new_users=signups,
        churned_users=cancellations,
        churn_rate=round(cancellations / active_at_start, 4) if active_at_start else 0.0,
        active_users=latest.active_users_eod,
        paying_users=latest.paying_users_eod,
        paying_share=round(latest.paying_users_eod / latest.active_users_eod, 4)
        if latest.active_users_eod else 0.0,
        mrr_clp=latest.mrr_clp_eod,
        deltas=CommercialDeltas(
            new_users_pct=_pct(signups, prev_signups),
            mrr_pct=_pct(latest.mrr_clp_eod, prev_latest.mrr_clp_eod) if prev_latest else None,
            active_users_pct=_pct(latest.active_users_eod, prev_latest.active_users_eod) if prev_latest else None,
        ),
        computed_at=latest.computed_at,
    )


# ---------- 4.2 Registrations ----------
@router.get("/registrations", response_model=RegistrationsOut)
async def registrations(db: Db, period: PeriodKey = Query(...)):
    p = resolve(period)

    if p.granularity == "hour":
        day_start = datetime.combine(p.start, time.min, tzinfo=timezone.utc)
        rows = (await db.execute(select(models.MetricsHourlyUsers)
                                 .where(models.MetricsHourlyUsers.ts_hour >= day_start)
                                 .order_by(models.MetricsHourlyUsers.ts_hour))).scalars().all()
        buckets = [BucketOut(label=f"{r.ts_hour.hour:02d} h", start=r.ts_hour,
                             end=r.ts_hour + timedelta(hours=3)) for r in rows]
        return RegistrationsOut(period=_period_out(p), granularity="hour", buckets=buckets,
                                signups=[r.signups for r in rows],
                                cancellations=[r.cancellations for r in rows],
                                computed_at=max((r.computed_at for r in rows), default=day_start))

    rows = (await db.execute(select(models.MetricsDailyUsers)
                             .where(models.MetricsDailyUsers.day.between(p.start, p.end))
                             .order_by(models.MetricsDailyUsers.day))).scalars().all()
    by_day = {r.day: r for r in rows}
    buckets, signups, cancellations = [], [], []
    for b_start, b_end, label in buckets_for(p):
        buckets.append(BucketOut(label=label,
                                 start=datetime.combine(b_start, time.min, tzinfo=timezone.utc),
                                 end=datetime.combine(b_end, time.max, tzinfo=timezone.utc)))
        days = [by_day[b_start + timedelta(days=i)]
                for i in range((b_end - b_start).days + 1)
                if (b_start + timedelta(days=i)) in by_day]
        signups.append(sum(d.signups for d in days))
        cancellations.append(sum(d.cancellations for d in days))
    computed = max((r.computed_at for r in rows), default=datetime.now(timezone.utc))
    return RegistrationsOut(period=_period_out(p), granularity=p.granularity, buckets=buckets,
                            signups=signups, cancellations=cancellations, computed_at=computed)


# ---------- 4.3 Plans ----------
@router.get("/plans", response_model=PlansOut)
async def plans(db: Db):
    last_date = (await db.execute(select(func.max(models.MetricsPlanSnapshot.as_of)))).scalar_one_or_none()
    if last_date is None:
        raise invalid("NO_DATA", "Aún no hay snapshots de planes calculados.")
    rows = (await db.execute(select(models.MetricsPlanSnapshot)
                             .where(models.MetricsPlanSnapshot.as_of == last_date))).scalars().all()
    order = [PlanCode.free, PlanCode.gold, PlanCode.platinum, PlanCode.provider]
    rows.sort(key=lambda r: order.index(PlanCode(r.plan_code)))
    total_users = sum(r.users for r in rows)
    total_mrr = sum(r.mrr_clp for r in rows)
    # churn total ponderado por usuarios
    total_churn = round(sum(r.monthly_churn * r.users for r in rows) / total_users, 4) if total_users else 0.0
    return PlansOut(
        as_of=last_date,
        plans=[PlanRow(plan_code=PlanCode(r.plan_code), name=PLAN_NAMES[PlanCode(r.plan_code)],
                       users=r.users, share=round(r.users / total_users, 4) if total_users else 0.0,
                       price_clp=r.price_clp, mrr_clp=r.mrr_clp, monthly_churn=r.monthly_churn)
               for r in rows],
        totals=PlanTotals(users=total_users, mrr_clp=total_mrr, monthly_churn=total_churn),
        computed_at=max(r.computed_at for r in rows),
    )


# ---------- 4.4 Funnel ----------
@router.get("/funnel", response_model=FunnelOut)
async def funnel(db: Db):
    today = date.today()
    total_downloads = (await db.execute(
        select(func.coalesce(func.sum(models.MetricsDailyUsers.downloads), 0)))).scalar_one()
    total_signups = (await db.execute(
        select(func.coalesce(func.sum(models.MetricsDailyUsers.signups), 0)))).scalar_one()
    latest = await _latest_row(db, today)
    if latest is None:
        raise invalid("NO_DATA", "Aún no hay métricas diarias calculadas.")

    stages_raw = [("downloads", "Descargas", total_downloads),
                  ("accounts", "Cuentas creadas", total_signups),
                  ("active_30d", "Activos últimos 30 días", latest.active_users_eod),
                  ("paying", "En plan de pago", latest.paying_users_eod)]
    first = stages_raw[0][2] or 1
    return FunnelOut(as_of=latest.day,
                     stages=[FunnelStage(stage=k, name=n, users=v,
                                         rate_vs_first=round(v / first, 4)) for k, n, v in stages_raw],
                     computed_at=latest.computed_at)
