"""El dashboard comercial muestra la facturación real (metrics_plan_snapshot y metrics_daily_users)."""
import pytest
from sqlalchemy import func, select

from app import models
from app.billing.enums import PREMIUM_STATUSES
from app.billing.metrics_sync import sync_commercial_metrics
from app.billing.models import Subscription
from tests.billing_helpers import subscribe_with_card

pytestmark = pytest.mark.asyncio
ADMIN = "/api/v1/admin"


async def billing_truth(session) -> dict[str, tuple[int, int]]:
    rows = (await session.execute(
        select(Subscription.plan_code, func.count(), func.coalesce(func.sum(Subscription.price_amount), 0))
        .where(Subscription.status.in_(PREMIUM_STATUSES)).group_by(Subscription.plan_code))).all()
    return {code: (int(n), int(mrr)) for code, n, mrr in rows}


async def test_cycle_feeds_commercial_dashboard(client, admin_headers, app_headers, fake_gw, session):
    # Otros tests corren el ciclo con fechas simuladas a futuro: se limpian para leer el snapshot de hoy
    from datetime import date
    from sqlalchemy import delete
    await session.execute(delete(models.MetricsPlanSnapshot).where(models.MetricsPlanSnapshot.as_of > date.today()))
    await session.execute(delete(models.MetricsDailyUsers).where(models.MetricsDailyUsers.day > date.today()))
    await session.commit()
    await subscribe_with_card(client, app_headers(), "gold")
    await subscribe_with_card(client, app_headers(), "gold", decision="approve_mastercard")  # rechazada: no paga

    r = await client.post(f"{ADMIN}/billing/jobs/billing-cycle", headers=admin_headers)
    assert r.status_code == 200, r.text
    cycle = r.json()
    assert cycle["errors"] == []

    truth = await billing_truth(session)
    plans = (await client.get(f"{ADMIN}/metrics/commercial/plans", headers=admin_headers)).json()
    by_code = {p["plan_code"]: p for p in plans["plans"]}
    for code in ("gold", "platinum", "provider"):
        units, mrr = truth.get(code, (0, 0))
        assert by_code[code]["users"] == units and by_code[code]["mrr_clp"] == mrr
    assert "free" in by_code                                  # la fila Gratuito se conserva

    paying = sum(u for u, _ in truth.values())
    total_mrr = sum(m for _, m in truth.values())
    assert cycle["paying_units"] == paying and cycle["mrr_clp"] == total_mrr
    summary = (await client.get(f"{ADMIN}/metrics/commercial/summary", headers=admin_headers,
                                params={"period": "today"})).json()
    assert summary["paying_users"] == paying and summary["mrr_clp"] == total_mrr

    funnel = (await client.get(f"{ADMIN}/metrics/commercial/funnel", headers=admin_headers)).json()
    assert funnel["stages"][-1]["users"] == paying

    # La consola de facturación y el dashboard comercial dicen lo mismo
    billing = (await client.get(f"{ADMIN}/billing/summary", headers=admin_headers)).json()
    assert billing["mrr"] == total_mrr


async def test_sync_is_idempotent(session):
    a = await sync_commercial_metrics(session)
    await session.commit()
    b = await sync_commercial_metrics(session)
    await session.commit()
    assert a.as_dict() == b.as_dict()
    per_plan = (await session.execute(
        select(models.MetricsPlanSnapshot.plan_code, func.count())
        .where(models.MetricsPlanSnapshot.as_of == a.as_of)
        .group_by(models.MetricsPlanSnapshot.plan_code))).all()
    assert all(n == 1 for _, n in per_plan) and len(per_plan) == 4
