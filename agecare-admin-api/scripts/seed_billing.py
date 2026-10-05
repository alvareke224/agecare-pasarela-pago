"""Datos demo de facturación: suscripciones en todos los estados con su historial de cobros.

Lo invoca scripts/seed.py. Las tarjetas son del simulador (no hay datos reales):
Visa ···· 6623 aprobada y Mastercard ···· 0568 rechazada, como en el ambiente
de integración de Transbank.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.billing import crypto
from app.billing.models import BillingEvent, Charge, PaymentMethod, Refund, Subscription
from app.billing.periods import add_months
from app.billing.service import new_buy_order, tbk_username

PRICES = {"gold": 1000, "platinum": 10000, "provider": 5000}
UNITS = {"gold": "user", "platinum": "family", "provider": "provider"}
FIRST = ["Ana", "Rosa", "Luis", "Carmen", "Jorge", "Paula", "Felipe", "Javiera", "Tomás", "Isidora",
         "Matías", "Valentina", "Benjamín", "Catalina", "Vicente", "Antonia", "Ignacio", "Florencia"]
LAST = ["Pérez", "Díaz", "Soto", "Muñoz", "Rojas", "González", "Contreras", "Silva", "Morales", "Fuentes"]
CHILD = "597055555542"

# (plan, estado, meses pagados, extras)
SCENARIOS = (
    [("gold", "active", m, {}) for m in (1, 2, 3, 4, 5, 6, 8, 2, 3, 1, 7, 4)]
    + [("platinum", "active", m, {}) for m in (2, 5, 1, 3, 6, 4)]
    + [("provider", "active", m, {}) for m in (3, 1, 2)]
    + [("gold", "active", 3, {"cancel_at_period_end": True}), ("platinum", "active", 2, {"pending": "gold"})]
    + [("gold", "past_due", 2, {"retry_count": 1}), ("platinum", "past_due", 4, {"retry_count": 2}),
       ("provider", "past_due", 1, {"retry_count": 1})]
    + [("gold", "canceled", 2, {"reason": "user_request"}), ("gold", "canceled", 3, {"reason": "payment_failed"}),
       ("platinum", "canceled", 1, {"reason": "user_request", "refund": True})]
    + [("gold", "incomplete", 0, {}), ("platinum", "expired", 0, {})]
)


async def seed_billing(db: AsyncSession, admin: models.AdminUser) -> int:
    for table in (BillingEvent, Refund, Charge, Subscription, PaymentMethod):
        await db.execute(delete(table))
    rnd = random.Random(20261004)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if not await db.get(models.SystemSetting, "billing_dunning"):
        db.add(models.SystemSetting(
            key="billing_dunning",
            description="Reintentos de cobro tras un rechazo (días desde el fallo anterior) antes de cancelar.",
            value={"retry_days": [1, 3, 5]},
            value_schema={"type": "object",
                          "properties": {"retry_days": {"type": "array", "maxItems": 6,
                                                        "items": {"type": "integer", "minimum": 1, "maximum": 15}}},
                          "required": ["retry_days"], "additionalProperties": False}))

    for i, (plan, status, months, extra) in enumerate(SCENARIOS):
        user_id = uuid.uuid5(uuid.NAMESPACE_URL, f"agecare-demo-user-{i}")
        name = f"{FIRST[i % len(FIRST)]} {LAST[(i * 7) % len(LAST)]}"
        email = f"{name.split()[0].lower()}.{i}@demo.cl".replace("á", "a").replace("é", "e").replace("í", "i") \
            .replace("ó", "o").replace("ú", "u")
        rejected_card = status == "past_due" or extra.get("reason") == "payment_failed"
        pm = PaymentMethod(user_id=user_id, user_email=email, provider="fake", tbk_username=tbk_username(user_id),
                           status="active" if status != "expired" else "aborted",
                           tbk_user_enc=crypto.encrypt(f"tbkuser-demo-{i}-{'mastercard' if rejected_card else 'visa'}")
                           if status != "expired" else None,
                           card_type="Mastercard" if rejected_card else "Visa",
                           card_last4="0568" if rejected_card else "6623",
                           failure_code="TBK_ABORTED" if status == "expired" else None,
                           activated_at=now - timedelta(days=31 * max(months, 1)) if status != "expired" else None)
        if status == "expired":
            pm.card_type = pm.card_last4 = None
        db.add(pm)
        await db.flush()

        beneficiary = user_id if UNITS[plan] == "user" else uuid.uuid5(uuid.NAMESPACE_URL, f"agecare-demo-{plan}-{i}")
        anchor_back = rnd.randint(0, 27)
        started = add_months(now - timedelta(days=anchor_back), -months, (now - timedelta(days=anchor_back)).day) \
            if months else None
        sub = Subscription(user_id=user_id, user_email=email, user_name=name, plan_code=plan,
                           beneficiary_type=UNITS[plan], beneficiary_id=beneficiary,
                           status=status if status not in ("canceled",) else "canceled",
                           payment_method_id=pm.id if status != "expired" else None,
                           price_amount=PRICES[plan], currency_code="CLP",
                           created_at=(started or now - timedelta(hours=30 if status == "expired" else 2)) - timedelta(minutes=4))
        db.add(sub)
        await db.flush()
        db.add(BillingEvent(subscription_id=sub.id, user_id=user_id, type="subscription.created", actor_type="user",
                            actor_id=user_id, payload={"plan_code": plan, "price_amount": PRICES[plan]},
                            created_at=sub.created_at))

        period_start, last_charge, m = started, None, 0
        # Las activas se cobran mes a mes hasta que el periodo vigente cubre el presente
        while months and (m < months or (status == "active" and as_aware(period_start) <= now)):
            period_end = add_months(period_start, 1, started.day)
            bo = new_buy_order(period_start)
            charge = Charge(subscription_id=sub.id, payment_method_id=pm.id, user_id=user_id, plan_code=plan,
                            kind="initial" if m == 0 else "renewal", attempt=1, buy_order=bo,
                            detail_buy_order=f"{bo}-S", commerce_code=CHILD, amount=PRICES[plan],
                            status="authorized", period_start=period_start, period_end=period_end,
                            response_code=0, authorization_code=f"{rnd.randint(100000, 999999)}",
                            payment_type_code="VN", installments_number=0, card_last4="6623",
                            transaction_date=period_start, accounting_date=f"{period_start:%m%d}",
                            resolved_at=period_start, created_at=period_start,
                            gateway_response={"buy_order": bo, "details": [{"status": "AUTHORIZED", "response_code": 0}]})
            db.add(charge)
            await db.flush()
            db.add(BillingEvent(subscription_id=sub.id, charge_id=charge.id, user_id=user_id,
                                type="charge.succeeded", actor_type="system",
                                payload={"amount": PRICES[plan], "buy_order": bo}, created_at=period_start))
            last_charge = charge
            period_start = period_end
            m += 1

        if months:
            sub.started_at, sub.billing_anchor_day = started, started.day
            sub.current_period_start, sub.current_period_end = last_charge.period_start, last_charge.period_end
            sub.next_charge_at = last_charge.period_end

        if status == "active":
            # Los periodos deben cubrir el presente: si el último terminó, se considera renovado ya
            sub.next_charge_at = sub.current_period_end
            if extra.get("cancel_at_period_end"):
                sub.cancel_at_period_end, sub.cancel_reason = True, "user_request"
            if extra.get("pending"):
                sub.pending_plan_code, sub.pending_beneficiary_type, sub.pending_beneficiary_id = "gold", "user", user_id
        elif status == "past_due":
            retries = extra["retry_count"]
            due = now - timedelta(days=sum([1, 3, 5][:retries]) - 0.5)
            sub.current_period_end = due
            sub.current_period_start = add_months(due, -1, due.day)
            sub.retry_count = retries
            sub.next_charge_at = now + timedelta(days=[1, 3, 5][retries], hours=-6)
            for attempt in range(retries):
                bo = new_buy_order(due)
                failed = Charge(subscription_id=sub.id, payment_method_id=pm.id, user_id=user_id, plan_code=plan,
                                kind="renewal" if attempt == 0 else "retry", attempt=attempt + 1, buy_order=bo,
                                detail_buy_order=f"{bo}-S", commerce_code=CHILD, amount=PRICES[plan], status="failed",
                                period_start=due, period_end=add_months(due, 1, due.day), response_code=-1,
                                failure_code="TBK_REJECTED", failure_message="El emisor de la tarjeta rechazó el pago.",
                                card_last4="0568", resolved_at=due + timedelta(days=sum([1, 3, 5][:attempt])),
                                created_at=due + timedelta(days=sum([1, 3, 5][:attempt])))
                db.add(failed)
                await db.flush()
                db.add(BillingEvent(subscription_id=sub.id, charge_id=failed.id, user_id=user_id, type="charge.failed",
                                    actor_type="system", payload={"failure_code": "TBK_REJECTED"},
                                    created_at=failed.created_at))
            db.add(BillingEvent(subscription_id=sub.id, user_id=user_id, type="subscription.past_due",
                                actor_type="system", payload={"retry_count": retries}, created_at=now - timedelta(hours=6)))
        elif status == "canceled":
            sub.canceled_at = min(as_aware(sub.current_period_end), now - timedelta(days=rnd.randint(1, 20)))
            sub.cancel_reason = extra["reason"]
            sub.cancel_note = "Reintentos de cobro agotados." if extra["reason"] == "payment_failed" else None
            sub.next_charge_at = None
            db.add(BillingEvent(subscription_id=sub.id, user_id=user_id, type="subscription.canceled",
                                actor_type="system" if extra["reason"] == "payment_failed" else "user",
                                actor_id=None if extra["reason"] == "payment_failed" else user_id,
                                payload={"reason": extra["reason"]}, created_at=sub.canceled_at))
            if extra.get("refund") and last_charge is not None:
                db.add(Refund(charge_id=last_charge.id, amount=PRICES[plan], status="succeeded", refund_type="NULLIFIED",
                              response_code=0, authorization_code="123456",
                              reason="Cliente canceló el mismo día del cobro; se devuelve el mes.",
                              requested_by=admin.id, requested_by_name=admin.full_name,
                              created_at=sub.canceled_at))
                last_charge.refunded_amount, last_charge.status = PRICES[plan], "refunded"
                db.add(BillingEvent(subscription_id=sub.id, charge_id=last_charge.id, user_id=user_id,
                                    type="charge.refunded", actor_type="admin", actor_id=admin.id,
                                    payload={"amount": PRICES[plan], "refund_type": "NULLIFIED"},
                                    created_at=sub.canceled_at))
        elif status == "expired":
            db.add(BillingEvent(subscription_id=sub.id, user_id=user_id, type="subscription.expired",
                                actor_type="system", payload={}, created_at=now - timedelta(hours=5)))
    await db.flush()
    return len(SCENARIOS)


def as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
