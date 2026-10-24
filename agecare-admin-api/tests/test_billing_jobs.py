"""Ciclo de facturación: renovaciones, reintentos (dunning), cancelaciones y expiración."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.billing.jobs import run_billing_cycle
from app.billing.models import BillingEvent, Charge, PaymentMethod, Subscription
from app.billing.periods import add_months
from app.billing.service import tbk_username
from app.security import as_utc, now_utc
from tests.billing_helpers import BILLING, subscribe_with_card

pytestmark = pytest.mark.asyncio


def test_add_months_keeps_anchor_day():
    jan31 = datetime(2027, 1, 31, 12, tzinfo=timezone.utc)
    feb = add_months(jan31, 1, 31)
    assert feb.day == 28
    assert add_months(feb, 1, 31).day == 31
    assert add_months(datetime(2028, 1, 31, tzinfo=timezone.utc), 1, 31).day == 29   # bisiesto
    assert add_months(datetime(2026, 12, 15, tzinfo=timezone.utc), 1, 15) == datetime(2027, 1, 15, tzinfo=timezone.utc)


async def _sub(session, sid: str) -> Subscription:
    session.expire_all()
    return await session.get(Subscription, uuid.UUID(sid))


async def _charges(session, sub: Subscription) -> list[Charge]:
    return (await session.execute(select(Charge).where(Charge.subscription_id == sub.id)
                                  .order_by(Charge.created_at, Charge.attempt))).unique().scalars().all()


async def _events(session, sub: Subscription) -> list[str]:
    return [e.type for e in (await session.execute(select(BillingEvent).where(BillingEvent.subscription_id == sub.id)
                                                   .order_by(BillingEvent.id))).scalars().all()]


def _set_card(fake_gw, sub: Subscription, card: str) -> None:
    username = tbk_username(sub.user_id)
    for data in fake_gw.tbk_users.values():
        if data["username"] == username:
            data["card"] = card


async def _active_sub(client, app_headers, plan="gold", **kw):
    h = app_headers(**kw)
    data, result = await subscribe_with_card(client, h, plan)
    assert result.get("result", "success") == "success"
    return h, data["subscription"]["id"]


async def test_renewal_success(client, fake_gw, app_headers, session):
    _, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    first_end, anchor = as_utc(sub.current_period_end), sub.billing_anchor_day
    stats = await run_billing_cycle(session, fake_gw, now=first_end + timedelta(minutes=1))
    assert stats.renewed >= 1 and not stats.errors
    sub = await _sub(session, sid)
    assert sub.status == "active"
    assert as_utc(sub.current_period_start) == first_end
    assert as_utc(sub.current_period_end) == add_months(first_end, 1, anchor)
    charges = await _charges(session, sub)
    assert [c.kind for c in charges] == ["initial", "renewal"]
    assert all(c.status == "authorized" for c in charges)
    assert "subscription.renewed" in await _events(session, sub)


async def test_dunning_retries_then_cancels(client, fake_gw, app_headers, session):
    h, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    _set_card(fake_gw, sub, "mastercard")     # desde ahora el emisor rechaza
    due = as_utc(sub.current_period_end)

    now = due + timedelta(minutes=1)
    expected_gaps = [1, 3, 5]
    for attempt, gap in enumerate(expected_gaps, start=1):
        await run_billing_cycle(session, fake_gw, now=now)
        sub = await _sub(session, sid)
        assert sub.status == "past_due"
        assert sub.retry_count == attempt
        assert as_utc(sub.next_charge_at) == now + timedelta(days=gap)
        me = (await client.get(f"{BILLING}/me", headers=h)).json()
        assert me["entitlement"]["plan_code"] == "gold"     # periodo de gracia: mantiene el acceso
        now = as_utc(sub.next_charge_at) + timedelta(minutes=1)

    await run_billing_cycle(session, fake_gw, now=now)
    sub = await _sub(session, sid)
    assert sub.status == "canceled" and sub.cancel_reason == "payment_failed"
    assert sub.next_charge_at is None
    charges = await _charges(session, sub)
    assert [c.kind for c in charges] == ["initial", "renewal", "retry", "retry", "retry"]
    assert [c.status for c in charges[1:]] == ["failed"] * 4
    assert [c.attempt for c in charges[1:]] == [1, 2, 3, 4]
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["entitlement"]["plan_code"] == "free"
    events = await _events(session, sub)
    assert events.count("subscription.past_due") == 3 and events[-1] == "subscription.canceled"


async def test_recovery_after_failed_renewal(client, fake_gw, app_headers, session):
    _, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    due = as_utc(sub.current_period_end)
    _set_card(fake_gw, sub, "mastercard")
    await run_billing_cycle(session, fake_gw, now=due + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.status == "past_due"
    _set_card(fake_gw, sub, "visa")
    stats = await run_billing_cycle(session, fake_gw, now=as_utc(sub.next_charge_at) + timedelta(minutes=1))
    assert stats.recovered >= 1
    sub = await _sub(session, sid)
    assert sub.status == "active" and sub.retry_count == 0
    # El periodo pagado parte en la fecha de vencimiento original: el día ancla se conserva
    assert as_utc(sub.current_period_start) == due
    assert "subscription.recovered" in await _events(session, sub)


async def test_scheduled_cancel_closes_without_charging(client, fake_gw, app_headers, session):
    h, sid = await _active_sub(client, app_headers)
    await client.post(f"{BILLING}/subscriptions/{sid}/cancel", headers=h)
    sub = await _sub(session, sid)
    await run_billing_cycle(session, fake_gw, now=as_utc(sub.current_period_end) + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.status == "canceled" and sub.cancel_reason == "user_request"
    assert len(await _charges(session, sub)) == 1


async def test_plan_change_applies_at_renewal(client, fake_gw, app_headers, session):
    family = str(uuid.uuid4())
    h, sid = await _active_sub(client, app_headers, family_ids=[family])
    r = await client.post(f"{BILLING}/subscriptions/{sid}/change-plan", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 200
    prices = {p["plan_code"]: p["price_amount"] for p in (await client.get(f"{BILLING}/plans")).json()["plans"]}
    sub = await _sub(session, sid)
    await run_billing_cycle(session, fake_gw, now=as_utc(sub.current_period_end) + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.plan_code == "platinum" and sub.beneficiary_type == "family"
    assert str(sub.beneficiary_id) == family
    assert sub.price_amount == prices["platinum"]
    assert (await _charges(session, sub))[-1].amount == prices["platinum"]
    assert "subscription.plan_changed" in await _events(session, sub)


async def test_incomplete_subscription_expires(client, fake_gw, app_headers, session):
    h = app_headers()
    data, result = await subscribe_with_card(client, h, "gold", decision="abort")
    assert result["result"] == "aborted"
    stats = await run_billing_cycle(session, fake_gw, now=now_utc() + timedelta(hours=25))
    assert stats.expired_subscriptions >= 1
    sub = await _sub(session, data["subscription"]["id"])
    assert sub.status == "expired" and sub.next_charge_at is None
    # El usuario puede volver a contratar después de la expiración
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})
    assert r.status_code == 201


async def test_inscription_deleted_in_transbank(client, fake_gw, app_headers, session):
    """Código -96: la inscripción ya no existe en Transbank → se da de baja la tarjeta y entra en mora."""
    _, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    username = tbk_username(sub.user_id)
    for key in [k for k, v in fake_gw.tbk_users.items() if v["username"] == username]:
        fake_gw.revoke(key)
    await run_billing_cycle(session, fake_gw, now=as_utc(sub.current_period_end) + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.status == "past_due"
    charge = (await _charges(session, sub))[-1]
    assert charge.response_code == -96 and charge.failure_code == "TBK_INSCRIPTION_NOT_FOUND"
    pm = await session.get(PaymentMethod, sub.payment_method_id)
    assert pm.status == "removed"
    # Siguiente reintento sin tarjeta: no se llama a Transbank pero avanza la política de reintentos
    n_charges = len(await _charges(session, sub))
    await run_billing_cycle(session, fake_gw, now=as_utc(sub.next_charge_at) + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.retry_count == 2
    assert "charge.skipped" in await _events(session, sub)
    assert len(await _charges(session, sub)) == n_charges   # no hubo llamada a Transbank


async def test_cycle_is_idempotent(client, fake_gw, app_headers, session):
    _, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    at = as_utc(sub.current_period_end) + timedelta(minutes=1)
    await run_billing_cycle(session, fake_gw, now=at)
    await run_billing_cycle(session, fake_gw, now=at)   # segunda corrida en el mismo instante
    sub = await _sub(session, sid)
    assert len(await _charges(session, sub)) == 2


async def test_job_in_another_process_renews(client, fake_gw, app_headers, session):
    """El job corre en otro proceso (otra instancia del simulador, sin memoria compartida)."""
    from app.billing.gateway import FakeGateway
    _, sid = await _active_sub(client, app_headers)
    sub = await _sub(session, sid)
    other_process = FakeGateway()
    await run_billing_cycle(session, other_process, now=as_utc(sub.current_period_end) + timedelta(minutes=1))
    sub = await _sub(session, sid)
    assert sub.status == "active"
    assert (await _charges(session, sub))[-1].status == "authorized"


async def test_old_encryption_key_still_charges_and_is_rotated(client, fake_gw, app_headers, session, monkeypatch):
    """Tarjetas cifradas con una llave anterior (p. ej. el seed cargado en Azure) se cobran y se re-cifran."""
    import uuid

    from cryptography.fernet import Fernet

    from app.billing import crypto
    from app.billing.enums import ChargeKind
    from app.billing.gateway import get_gateway
    from app.billing.models import PaymentMethod, Subscription
    from app.billing.service import BillingService
    from app.config import get_settings

    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    s = get_settings()
    monkeypatch.setattr(s, "environment", "prod")              # sin la llave de desarrollo de respaldo
    monkeypatch.setattr(s, "billing_encryption_key", old_key)
    crypto._fernet.cache_clear()
    try:
        data, _ = await subscribe_with_card(client, app_headers(), "gold")   # cifrada con la llave antigua
        sub = await session.get(Subscription, uuid.UUID(data["subscription"]["id"]))
        pm = await session.get(PaymentMethod, sub.payment_method_id)
        old_cipher = bytes(pm.tbk_user_enc)

        monkeypatch.setattr(s, "billing_encryption_key", new_key)         # se rota la llave
        crypto._fernet.cache_clear()
        with pytest.raises(crypto.DecryptionError):                        # sin declarar la anterior: ilegible
            crypto.decrypt(old_cipher)

        monkeypatch.setattr(s, "billing_encryption_keys_old", old_key)    # se declara la anterior
        crypto._fernet.cache_clear()
        charge = await BillingService(session, get_gateway()).charge(sub, ChargeKind.renewal)
        assert charge.status == "authorized"
        await session.refresh(pm)
        assert bytes(pm.tbk_user_enc) != old_cipher and crypto.is_current(pm.tbk_user_enc)
    finally:
        crypto._fernet.cache_clear()

async def test_unreadable_card_does_not_leave_pending_charge(client, fake_gw, app_headers, session, monkeypatch):
    from sqlalchemy import func, select

    from app.billing import crypto
    from app.billing.enums import ChargeKind
    from app.billing.gateway import get_gateway
    from app.billing.models import Charge, Subscription
    from app.billing.service import BillingService
    from app.errors import ApiError
    from tests.billing_helpers import subscribe_with_card

    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    sid = __import__("uuid").UUID(data["subscription"]["id"])
    before = (await session.execute(select(func.count()).select_from(Charge).where(Charge.subscription_id == sid))).scalar_one()

    def boom(_):
        raise crypto.DecryptionError("llave distinta")
    monkeypatch.setattr(crypto, "decrypt", boom)
    sub = await session.get(Subscription, sid)
    with pytest.raises(ApiError) as err:
        await BillingService(session, get_gateway()).charge(sub, ChargeKind.renewal)
    assert err.value.code == "PAYMENT_METHOD_UNREADABLE"
    after = (await session.execute(select(func.count()).select_from(Charge).where(Charge.subscription_id == sid))).scalar_one()
    assert after == before          # no quedó un cobro pending huérfano
