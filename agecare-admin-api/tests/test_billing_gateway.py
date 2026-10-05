"""Contrato HTTP del cliente Transbank Oneclick Mall (sin red: httpx.MockTransport).

Verifica rutas, método, cabeceras y cuerpos según la referencia oficial
https://www.transbankdevelopers.cl/referencia/oneclick (API v1.2).
"""
import json

import httpx
import pytest

from app.billing.gateway import (GatewayError, GatewayNotFound, GatewayUnavailable,
                                 TransbankOneclickGateway)

pytestmark = pytest.mark.asyncio
BASE = "https://webpay3gint.transbank.cl"
PREFIX = "/rswebpaytransaction/api/oneclick/v1.2"
COMMERCE, CHILD = "597055555541", "597055555542"
SECRET = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"


def gateway(handler) -> tuple[TransbankOneclickGateway, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _h(request: httpx.Request):
        seen.append(request)
        return handler(request)
    return TransbankOneclickGateway(BASE, COMMERCE, SECRET, transport=httpx.MockTransport(_h)), seen


def body(req: httpx.Request) -> dict:
    return json.loads(req.content) if req.content else {}


async def test_start_inscription_contract():
    gw, seen = gateway(lambda r: httpx.Response(200, json={"token": "e" * 64,
                                                           "url_webpay": f"{BASE}/webpayserver/bp_multicode_inscription.cgi"}))
    started = await gw.start_inscription("u123", "ana@demo.cl", "https://api.agecare.cl/api/v1/billing/oneclick/return")
    req = seen[0]
    assert req.method == "POST" and req.url.path == f"{PREFIX}/inscriptions"
    assert req.headers["Tbk-Api-Key-Id"] == COMMERCE and req.headers["Tbk-Api-Key-Secret"] == SECRET
    assert req.headers["Content-Type"] == "application/json"
    assert body(req) == {"username": "u123", "email": "ana@demo.cl",
                         "response_url": "https://api.agecare.cl/api/v1/billing/oneclick/return"}
    assert started.token == "e" * 64 and started.url_webpay.endswith("inscription.cgi")


async def test_finish_inscription_contract():
    gw, seen = gateway(lambda r: httpx.Response(200, json={
        "response_code": 0, "tbk_user": "b6bd6ba3-e718-4107-9386-d2b099a8dd42", "authorization_code": "123456",
        "card_type": "Visa", "card_number": "XXXXXXXXXXXX6623"}))
    result = await gw.finish_inscription("e" * 64)
    assert seen[0].method == "PUT" and seen[0].url.path == f"{PREFIX}/inscriptions/{'e' * 64}"
    assert result.approved and result.card_last4 == "6623" and result.card_type == "Visa"
    assert "tbk_user" not in result.raw     # nunca se persiste en la respuesta guardada


async def test_authorize_contract_and_parse():
    response = {"buy_order": "AG261004ABCDEF1234", "card_detail": {"card_number": "6623"},
                "accounting_date": "1004", "transaction_date": "2026-10-04T21:30:00.000Z",
                "details": [{"amount": 1000, "status": "AUTHORIZED", "authorization_code": "1213",
                             "payment_type_code": "VN", "response_code": 0, "installments_number": 0,
                             "commerce_code": CHILD, "buy_order": "AG261004ABCDEF1234-S"}]}
    gw, seen = gateway(lambda r: httpx.Response(200, json=response))
    result = await gw.authorize("u123", "tbk-user", "AG261004ABCDEF1234", CHILD, "AG261004ABCDEF1234-S", 1000)
    req = seen[0]
    assert req.method == "POST" and req.url.path == f"{PREFIX}/transactions"
    assert body(req) == {"username": "u123", "tbk_user": "tbk-user", "buy_order": "AG261004ABCDEF1234",
                         "details": [{"commerce_code": CHILD, "buy_order": "AG261004ABCDEF1234-S",
                                      "amount": 1000, "installments_number": 1}]}
    assert result.approved and result.amount == 1000 and result.authorization_code == "1213"
    assert result.card_last4 == "6623" and result.transaction_date.year == 2026


async def test_rejected_detail_is_not_approved():
    gw, _ = gateway(lambda r: httpx.Response(200, json={"buy_order": "X", "details": [
        {"amount": 1000, "status": "FAILED", "response_code": -97, "commerce_code": CHILD, "buy_order": "X-S"}]}))
    result = await gw.authorize("u", "t", "X", CHILD, "X-S", 1000)
    assert not result.approved and result.response_code == -97


async def test_status_refund_and_delete_contract():
    def handler(r: httpx.Request):
        if r.method == "GET":
            return httpx.Response(200, json={"buy_order": "B1", "details": [
                {"amount": 1000, "status": "AUTHORIZED", "response_code": 0, "authorization_code": "1"}]})
        if r.url.path.endswith("/refunds"):
            return httpx.Response(200, json={"type": "REVERSED"})
        return httpx.Response(204)
    gw, seen = gateway(handler)
    assert (await gw.status("B1")).approved
    refund = await gw.refund("B1", CHILD, "B1-S", 1000)
    await gw.delete_inscription("tbk-user", "u123")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", f"{PREFIX}/transactions/B1"),
        ("POST", f"{PREFIX}/transactions/B1/refunds"),
        ("DELETE", f"{PREFIX}/inscriptions")]
    assert body(seen[1]) == {"commerce_code": CHILD, "detail_buy_order": "B1-S", "amount": 1000}
    assert body(seen[2]) == {"tbk_user": "tbk-user", "username": "u123"}
    assert refund.type == "REVERSED"


async def test_error_classification():
    gw, _ = gateway(lambda r: httpx.Response(503, text="unavailable"))
    with pytest.raises(GatewayUnavailable):
        await gw.status("B1")
    gw, _ = gateway(lambda r: httpx.Response(404, json={"error_message": "Transaction not found"}))
    with pytest.raises(GatewayNotFound):
        await gw.status("B1")
    gw, _ = gateway(lambda r: httpx.Response(422, json={"error_message": "Invalid amount"}))
    with pytest.raises(GatewayError) as exc:
        await gw.refund("B1", CHILD, "B1-S", 99999)
    assert exc.value.status_code == 422 and "Invalid amount" in str(exc.value)

    def boom(r):
        raise httpx.ReadTimeout("timeout", request=r)
    gw, _ = gateway(boom)
    with pytest.raises(GatewayUnavailable):
        await gw.authorize("u", "t", "X", CHILD, "X-S", 1000)


async def test_production_config_guard():
    from app.config import Settings
    Settings(environment="dev").validate_billing()   # dev: sin restricciones
    with pytest.raises(RuntimeError) as exc:
        Settings(environment="prod").validate_billing()
    msg = str(exc.value)
    assert "ADMIN_BILLING_GATEWAY" in msg and "ADMIN_TBK_ENVIRONMENT" in msg and "ENCRYPTION_KEY" in msg
    Settings(environment="prod", billing_gateway="transbank", tbk_environment="production",
             tbk_commerce_code="597012345678", billing_encryption_key="x" * 44).validate_billing()
