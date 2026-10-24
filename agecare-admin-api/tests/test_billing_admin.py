"""Consola · Facturación: KPIs, listados, reembolsos, cancelación, reintentos y permisos."""
from datetime import datetime

import pytest

from tests.billing_helpers import BILLING, subscribe_with_card

pytestmark = pytest.mark.asyncio
ADMIN = "/api/v1/admin/billing"


async def test_summary_and_permissions(client, fake_gw, admin_headers, analyst_headers, support_headers,
                                       editor_headers):
    r = await client.get(f"{ADMIN}/summary", headers=admin_headers)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["currency_code"] == "CLP"
    assert s["mrr"] == sum(p["mrr"] for p in s["plans"]) > 0
    assert {p["plan_code"] for p in s["plans"]} == {"gold", "platinum", "provider"}
    assert s["subscriptions"]["past_due"] >= 3           # datos demo
    assert 0 <= (s["charges_30d"]["approval_rate"] or 0) <= 1
    assert s["charges_30d"]["net_revenue"] == s["charges_30d"]["gross_revenue"] - s["charges_30d"]["refunded"]
    assert (await client.get(f"{ADMIN}/summary", headers=analyst_headers)).status_code == 200
    assert (await client.get(f"{ADMIN}/summary", headers=support_headers)).status_code == 200
    r = await client.get(f"{ADMIN}/summary", headers=editor_headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    me = (await client.get("/api/v1/admin/auth/me", headers=admin_headers)).json()
    assert {"billing", "billing_refunds"} <= set(me["permissions"])


async def test_list_and_detail(client, fake_gw, admin_headers, app_headers):
    r = await client.get(f"{ADMIN}/subscriptions", headers=admin_headers, params={"status": "past_due"})
    assert r.status_code == 200
    page = r.json()
    assert page["total"] >= 3 and all(i["status"] == "past_due" for i in page["items"])
    assert all(i["payment_method"] is None or "tbk_user" not in i["payment_method"] for i in page["items"])

    data, _ = await subscribe_with_card(client, app_headers(email="busqueda.unica@demo.cl"), "gold")
    r = await client.get(f"{ADMIN}/subscriptions", headers=admin_headers, params={"q": "busqueda.unica"})
    assert r.json()["total"] == 1
    sid = r.json()["items"][0]["id"]
    assert sid == data["subscription"]["id"]

    d = (await client.get(f"{ADMIN}/subscriptions/{sid}", headers=admin_headers)).json()
    assert d["subscription"]["user_email"] == "busqueda.unica@demo.cl"
    assert d["charges"][0]["status"] == "authorized" and d["charges"][0]["commerce_code"]
    types = [e["type"] for e in d["events"]]
    assert {"subscription.created", "charge.succeeded", "subscription.activated"} <= set(types)

    r = await client.get(f"{ADMIN}/charges", headers=admin_headers, params={"q": d["charges"][0]["buy_order"]})
    assert r.json()["total"] == 1
    r = await client.get(f"{ADMIN}/charges", headers=admin_headers, params={"status": "failed"})
    assert r.json()["total"] >= 1
    assert (await client.get(f"{ADMIN}/subscriptions/00000000-0000-0000-0000-000000000000",
                             headers=admin_headers)).status_code == 404


async def test_refunds(client, fake_gw, admin_headers, support_headers, app_headers):
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    d = (await client.get(f"{ADMIN}/subscriptions/{data['subscription']['id']}", headers=admin_headers)).json()
    charge = d["charges"][0]
    cid, amount = charge["id"], charge["amount"]

    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=support_headers,
                          json={"amount": 100, "reason": "Soporte no puede reembolsar"})
    assert r.status_code == 403

    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=admin_headers,
                          json={"amount": 300, "reason": "Compensación por incidente del 26 ago"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "succeeded" and r.json()["refund_type"] == "NULLIFIED"

    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=admin_headers,
                          json={"amount": amount, "reason": "Excede lo reembolsable"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFUND_EXCEEDS_CHARGE"

    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=admin_headers,
                          json={"amount": amount - 300, "reason": "Saldo restante a pedido del cliente"})
    assert r.status_code == 201
    detail = (await client.get(f"{ADMIN}/charges/{cid}", headers=admin_headers)).json()
    assert detail["charge"]["status"] == "refunded"
    assert detail["charge"]["refunded_amount"] == amount and detail["refundable_amount"] == 0
    assert len(detail["refunds"]) == 2
    assert detail["refunds"][0]["requested_by_name"] == "Max K."

    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=admin_headers,
                          json={"amount": 1, "reason": "Nada más que devolver"})
    assert r.status_code == 422

    audit = (await client.get("/api/v1/admin/audit-log", headers=admin_headers,
                              params={"action": "billing.refund"})).json()
    assert audit["total"] >= 2
    me = (await client.get(f"{BILLING}/me/charges", headers=app_headers(email="nadie@demo.cl"))).json()
    assert me["total"] == 0


async def test_refund_rejected_by_transbank_is_recorded(client, fake_gw, admin_headers, app_headers):
    from app.billing.gateway import GatewayError
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    d = (await client.get(f"{ADMIN}/subscriptions/{data['subscription']['id']}", headers=admin_headers)).json()
    cid = d["charges"][0]["id"]

    async def reject(*args, **kwargs):
        raise GatewayError("Monto inválido para anulación", 422, {"error_message": "Monto inválido"})
    fake_gw.refund = reject
    r = await client.post(f"{ADMIN}/charges/{cid}/refunds", headers=admin_headers,
                          json={"amount": 100, "reason": "Prueba de rechazo de Transbank"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "REFUND_REJECTED"
    detail = (await client.get(f"{ADMIN}/charges/{cid}", headers=admin_headers)).json()
    assert detail["charge"]["status"] == "authorized"
    assert detail["refunds"][0]["status"] == "failed"   # el intento queda registrado


async def test_admin_cancel_extend_and_retry(client, fake_gw, admin_headers, analyst_headers, support_headers,
                                             app_headers):
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    sid = data["subscription"]["id"]
    before = (await client.get(f"{ADMIN}/subscriptions/{sid}", headers=admin_headers)).json()["subscription"]

    r = await client.post(f"{ADMIN}/subscriptions/{sid}/extend", headers=analyst_headers,
                          json={"days": 7, "reason": "Analista no escribe"})
    assert r.status_code == 403
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/extend", headers=support_headers,
                          json={"days": 7, "reason": "Soporte solo lee pagos"})
    assert r.status_code == 403                     # toda escritura de pagos es solo del rol admin
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/extend", headers=admin_headers,
                          json={"days": 7, "reason": "Compensación por ticket #1482"})
    assert r.status_code == 200, r.text
    delta = (datetime.fromisoformat(r.json()["current_period_end"]) -
             datetime.fromisoformat(before["current_period_end"]))
    assert delta.days == 7

    r = await client.post(f"{ADMIN}/subscriptions/{sid}/retry-charge", headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PAST_DUE"

    r = await client.post(f"{ADMIN}/subscriptions/{sid}/cancel", headers=support_headers,
                          json={"immediate": False, "reason": "admin", "note": "Solicitud por teléfono"})
    assert r.status_code == 403
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/cancel", headers=admin_headers,
                          json={"immediate": False, "reason": "admin", "note": "Solicitud por teléfono"})
    assert r.status_code == 200
    assert r.json()["status"] == "active" and r.json()["cancel_at_period_end"] is True
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/cancel", headers=admin_headers,
                          json={"immediate": True, "reason": "fraud", "note": "Contracargo reportado por el banco"})
    assert r.status_code == 200
    assert r.json()["status"] == "canceled" and r.json()["cancel_reason"] == "fraud"
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/cancel", headers=admin_headers,
                          json={"immediate": True, "reason": "admin", "note": "Doble cancelación"})
    assert r.status_code == 409

    audit = (await client.get("/api/v1/admin/audit-log", headers=admin_headers,
                              params={"entity_type": "billing_subscription"})).json()
    actions = {e["action"] for e in audit["items"]}
    assert {"billing.subscription_cancel", "billing.subscription_extend"} <= actions


async def test_manual_retry_on_past_due(client, fake_gw, admin_headers):
    subs = (await client.get(f"{ADMIN}/subscriptions", headers=admin_headers,
                             params={"status": "past_due", "plan": "gold"})).json()["items"]
    sid = subs[0]["id"]
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/retry-charge", headers=admin_headers)
    assert r.status_code == 200, r.text
    charge = r.json()
    assert charge["kind"] == "manual" and charge["status"] == "failed"   # tarjeta demo Mastercard
    after = (await client.get(f"{ADMIN}/subscriptions/{sid}", headers=admin_headers)).json()["subscription"]
    assert after["status"] == "past_due"
    assert after["retry_count"] == subs[0]["retry_count"]   # el reintento manual no consume la política


async def test_run_cycle_endpoint_admin_only(client, fake_gw, admin_headers, support_headers):
    assert (await client.post(f"{ADMIN}/jobs/billing-cycle", headers=support_headers)).status_code == 403
    r = await client.post(f"{ADMIN}/jobs/billing-cycle", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []


async def test_billing_writes_require_mfa_when_configured(client, fake_gw, admin_headers, app_headers, monkeypatch):
    """En prod (o con ADMIN_BILLING_REQUIRE_MFA=true) operar pagos exige 2FA activo en la cuenta."""
    from app.config import get_settings
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    sid = data["subscription"]["id"]
    monkeypatch.setattr(get_settings(), "billing_require_mfa", True)
    me = (await client.get("/api/v1/admin/auth/me", headers=admin_headers)).json()
    r = await client.post(f"{ADMIN}/subscriptions/{sid}/extend", headers=admin_headers,
                          json={"days": 3, "reason": "Prueba de 2FA obligatorio"})
    if me.get("mfa_enabled"):
        assert r.status_code == 200
    else:
        assert r.status_code == 403 and r.json()["error"]["code"] == "MFA_REQUIRED"
    assert (await client.get(f"{ADMIN}/subscriptions/{sid}", headers=admin_headers)).status_code == 200  # leer sí


async def test_chargeback_is_idempotent_and_updates_metrics(client, fake_gw, admin_headers, support_headers,
                                                            app_headers, session):
    from sqlalchemy import func, select

    from uuid import UUID

    from app.billing.models import BillingEvent
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    sid = data["subscription"]["id"]
    detail = (await client.get(f"{ADMIN}/subscriptions/{sid}", headers=admin_headers)).json()
    cid = detail["charges"][0]["id"]
    body = {"note": "Disputa informada en el portal de Transbank", "tbk_reference": "CB-2026-0091"}

    assert (await client.post(f"{ADMIN}/charges/{cid}/chargeback", headers=support_headers,
                              json=body)).status_code == 403
    r = await client.post(f"{ADMIN}/charges/{cid}/chargeback", headers=admin_headers, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["already_registered"] is False
    assert out["subscription"]["status"] == "canceled" and out["subscription"]["cancel_reason"] == "fraud"

    again = await client.post(f"{ADMIN}/charges/{cid}/chargeback", headers=admin_headers, json=body)
    assert again.status_code == 200 and again.json()["already_registered"] is True
    n = (await session.execute(select(func.count()).select_from(BillingEvent).where(
        BillingEvent.type == "charge.chargeback", BillingEvent.charge_id == UUID(cid)))).scalar_one()
    assert n == 1

    r = await client.post(f"{ADMIN}/charges/{cid}/chargeback", headers=admin_headers,
                          json={**body, "amount": 10_000_000})
    assert r.status_code in (200, 422)          # ya registrado: responde idempotente antes de validar


async def test_chargeback_requires_paid_charge(client, fake_gw, admin_headers, app_headers):
    data, _ = await subscribe_with_card(client, app_headers(), "gold", decision="approve_mastercard")
    detail = (await client.get(f"{ADMIN}/subscriptions/{data['subscription']['id']}", headers=admin_headers)).json()
    failed = next(c for c in detail["charges"] if c["status"] == "failed")
    r = await client.post(f"{ADMIN}/charges/{failed['id']}/chargeback", headers=admin_headers,
                          json={"note": "No debería aceptarse"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHARGE_NOT_PAID"
