"""Pasarela de pago · flujo de la app (checkout con el simulador de Webpay)."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.billing.gateway import FakeGateway, GatewayUnavailable
from app.billing.jobs import run_billing_cycle
from app.billing.models import Charge, PaymentMethod, Subscription
from app.security import as_utc, now_utc
from tests.billing_helpers import BILLING, complete_webpay, subscribe_with_card

pytestmark = pytest.mark.asyncio


async def test_plans_catalog_is_public(client, fake_gw):
    r = await client.get(f"{BILLING}/plans")
    assert r.status_code == 200
    plans = {p["plan_code"]: p for p in r.json()["plans"]}
    assert set(plans) == {"gold", "platinum", "provider"}
    assert plans["platinum"]["billing_unit"] == "family"
    assert all(p["price_amount"] > 0 and p["currency_code"] == "CLP" for p in plans.values())


async def test_requires_app_user_token(client, fake_gw, admin_headers):
    assert (await client.get(f"{BILLING}/me")).status_code == 401
    # un token de staff de la consola no sirve como usuario de la app
    r = await client.get(f"{BILLING}/me", headers=admin_headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_full_checkout_new_card(client, fake_gw, app_headers):
    h = app_headers()
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["next_action"] == "redirect_to_webpay"
    assert data["subscription"]["status"] == "incomplete"
    ins = data["inscription"]
    assert ins["method"] == "POST" and ins["token_field"] == "TBK_TOKEN" and len(ins["token"]) == 64

    result = await complete_webpay(client, ins)
    assert result["result"] == "success"
    assert result["card"] == "6623"
    assert result["subscription_id"] == data["subscription"]["id"]

    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["entitlement"]["plan_code"] == "gold"
    sub = me["subscriptions"][0]
    assert sub["status"] == "active" and sub["status_name"] == "Activa"
    assert me["payment_method"]["card_last4"] == "6623"
    days = (as_utc_iso(sub["current_period_end"]) - as_utc_iso(sub["current_period_start"])).days
    assert 28 <= days <= 31
    charges = (await client.get(f"{BILLING}/me/charges", headers=h)).json()
    assert charges["total"] == 1
    assert charges["items"][0]["status"] == "authorized"
    assert charges["items"][0]["kind"] == "initial"
    assert charges["items"][0]["amount"] == sub["price_amount"]
    assert len(charges["items"][0]["buy_order"]) <= 26

    # Repetir el retorno de Webpay es idempotente (no cobra dos veces)
    r = await client.get(f"{BILLING}/oneclick/return", params={"TBK_TOKEN": ins["token"]})
    assert "result=success" in r.headers["location"]
    assert (await client.get(f"{BILLING}/me/charges", headers=h)).json()["total"] == 1

    # No se puede contratar dos veces el mismo plan para el mismo beneficiario
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SUBSCRIPTION_EXISTS"


async def test_second_plan_uses_stored_card(client, fake_gw, app_headers):
    family = str(uuid.uuid4())
    h = app_headers(family_ids=[family])
    await subscribe_with_card(client, h, "gold")
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["next_action"] == "none"
    assert data["charge"]["status"] == "authorized"
    assert data["subscription"]["status"] == "active"
    assert data["subscription"]["beneficiary_id"] == family
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["entitlement"]["plan_code"] == "platinum"   # el mejor plan vigente


async def test_family_member_inherits_platinum(client, fake_gw, app_headers):
    family = str(uuid.uuid4())
    await subscribe_with_card(client, app_headers(family_ids=[family]), "platinum")
    member = app_headers(family_ids=[family])
    me = (await client.get(f"{BILLING}/me", headers=member)).json()
    assert me["entitlement"]["plan_code"] == "platinum"
    assert me["subscriptions"] == []   # no es quien paga


async def test_beneficiary_rules(client, fake_gw, app_headers):
    h = app_headers()
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BENEFICIARY_REQUIRED"
    r = await client.post(f"{BILLING}/subscriptions", headers=app_headers(family_ids=[str(uuid.uuid4())]),
                          json={"plan_code": "platinum", "beneficiary_id": str(uuid.uuid4())})
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN_BENEFICIARY"
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "provider"})
    assert r.status_code == 403
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "free"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PLAN_NOT_PURCHASABLE"
    r = await client.post(f"{BILLING}/subscriptions", headers=app_headers(provider_id=str(uuid.uuid4())),
                          json={"plan_code": "provider"})
    assert r.status_code == 201


async def test_rejected_card_then_new_card(client, fake_gw, app_headers):
    h = app_headers()
    data, result = await subscribe_with_card(client, h, "gold", decision="approve_mastercard")
    assert result["result"] == "charge_failed"
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["subscriptions"][0]["status"] == "incomplete"   # sigue esperando el pago
    assert me["entitlement"]["plan_code"] == "free"
    charges = (await client.get(f"{BILLING}/me/charges", headers=h)).json()["items"]
    assert charges[0]["status"] == "failed"
    assert "rechaz" in charges[0]["failure_message"]

    # Reintentar el checkout reutiliza la suscripción incompleta y cobra con la tarjeta guardada
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})
    assert r.status_code == 201
    assert r.json()["subscription"]["id"] == data["subscription"]["id"]
    assert r.json()["charge"]["status"] == "failed"

    # Inscribe otra tarjeta: reemplaza la anterior y cobra la suscripción pendiente
    r = await client.post(f"{BILLING}/payment-methods", headers=h)
    assert r.status_code == 201
    result = await complete_webpay(client, r.json(), "approve_visa")
    assert result["result"] == "success"
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["subscriptions"][0]["status"] == "active"
    assert me["payment_method"]["card_last4"] == "6623"
    assert len(fake_gw.deleted) == 1   # la Mastercard se eliminó en Transbank


async def test_aborted_and_rejected_inscription(client, fake_gw, app_headers):
    h = app_headers()
    _, result = await subscribe_with_card(client, h, "gold", decision="abort")
    assert result["result"] == "aborted"
    _, result = await subscribe_with_card(client, h, "gold", decision="reject")
    assert result["result"] == "rejected"
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["payment_method"] is None
    assert me["subscriptions"][0]["status"] == "incomplete"
    # Token desconocido o ausente → resultado error, sin 500
    r = await client.get(f"{BILLING}/oneclick/return", params={"TBK_TOKEN": "x" * 64})
    assert r.status_code == 303 and "result=error" in r.headers["location"]
    r = await client.post(f"{BILLING}/oneclick/return")
    assert "result=error" in r.headers["location"]


async def test_cancel_resume_and_change_plan(client, fake_gw, app_headers):
    family = str(uuid.uuid4())
    h = app_headers(family_ids=[family])
    data, _ = await subscribe_with_card(client, h, "gold")
    sid = data["subscription"]["id"]

    r = await client.post(f"{BILLING}/subscriptions/{sid}/cancel", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "active" and r.json()["cancel_at_period_end"] is True
    r = await client.post(f"{BILLING}/subscriptions/{sid}/resume", headers=h)
    assert r.json()["cancel_at_period_end"] is False
    r = await client.post(f"{BILLING}/subscriptions/{sid}/resume", headers=h)
    assert r.status_code == 409

    r = await client.post(f"{BILLING}/subscriptions/{sid}/change-plan", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 200, r.text
    assert r.json()["pending_plan_code"] == "platinum"
    assert r.json()["plan_code"] == "gold"
    r = await client.post(f"{BILLING}/subscriptions/{sid}/change-plan", headers=h, json={"plan_code": "gold"})
    assert r.json()["pending_plan_code"] is None   # deshace el cambio

    # Otro usuario no ve ni opera esta suscripción
    r = await client.post(f"{BILLING}/subscriptions/{sid}/cancel", headers=app_headers())
    assert r.status_code == 404

    # La tarjeta paga una suscripción activa: no se puede eliminar
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    r = await client.delete(f"{BILLING}/payment-methods/{me['payment_method']['id']}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAYMENT_METHOD_IN_USE"


async def test_timeout_is_reconciled_without_double_charge(client, fake_gw, app_headers, session):
    """Transbank cobra pero la respuesta se pierde: el cobro queda unknown y el job lo concilia."""
    h = app_headers()
    await subscribe_with_card(client, h, "gold")
    family = str(uuid.uuid4())
    h2 = app_headers(family_ids=[family])
    await subscribe_with_card(client, h2, "gold")

    original = FakeGateway.authorize

    async def lost_response(self, *args, **kwargs):
        await original(self, *args, **kwargs)          # Transbank sí procesó el cobro…
        raise GatewayUnavailable("timeout")            # …pero no recibimos la respuesta

    fake_gw.authorize = lost_response.__get__(fake_gw)
    r = await client.post(f"{BILLING}/subscriptions", headers=h2, json={"plan_code": "platinum"})
    assert r.status_code == 201
    assert r.json()["charge"]["status"] == "unknown"
    assert r.json()["subscription"]["status"] == "incomplete"
    sid = r.json()["subscription"]["id"]
    fake_gw.authorize = original.__get__(fake_gw)

    stats = await run_billing_cycle(session, fake_gw, now=now_utc() + timedelta(minutes=5))
    assert stats.reconciled >= 1
    sub = await session.get(Subscription, uuid.UUID(sid))
    await session.refresh(sub)
    assert sub.status == "active"
    charges = (await session.execute(select(Charge).where(Charge.subscription_id == sub.id))).scalars().all()
    assert len(charges) == 1 and charges[0].status == "authorized"
    assert len([t for t in fake_gw.transactions if t == charges[0].buy_order]) == 1


async def test_timeout_never_reached_transbank(client, fake_gw, app_headers, session):
    h = app_headers()
    await subscribe_with_card(client, h, "gold")
    fake_gw.queue_authorization(GatewayUnavailable("connect timeout"))   # no llegó a Transbank
    family = str(uuid.uuid4())
    h = app_headers(family_ids=[family])
    await subscribe_with_card(client, h, "gold")
    fake_gw.queue_authorization(GatewayUnavailable("connect timeout"))
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "platinum"})
    assert r.json()["charge"]["status"] == "unknown"
    await run_billing_cycle(session, fake_gw, now=now_utc() + timedelta(minutes=5))
    charge = await session.get(Charge, uuid.UUID(r.json()["charge"]["id"]))
    await session.refresh(charge)
    assert charge.status == "failed" and charge.failure_code == "TBK_NOT_PROCESSED"


async def test_tbk_user_is_encrypted_at_rest(client, fake_gw, app_headers, session):
    h = app_headers()
    await subscribe_with_card(client, h, "gold")
    tbk_user = next(iter(fake_gw.tbk_users))
    pms = (await session.execute(select(PaymentMethod).where(PaymentMethod.status == "active"))).scalars().all()
    assert all(tbk_user.encode() not in (pm.tbk_user_enc or b"") for pm in pms)
    assert all(pm.inscription_token_hash is None or len(pm.inscription_token_hash) == 64 for pm in pms)


def as_utc_iso(value: str):
    from datetime import datetime
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))

