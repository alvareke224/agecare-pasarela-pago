"""Modo Transbank con datos demo: lo nuevo va a Transbank, lo creado con el simulador sigue en él.

Transbank se reemplaza por un servidor falso (httpx.MockTransport) que responde como el ambiente
de integración, así el flujo completo (inscripción → retorno → cobro → reembolso) pasa por
TransbankOneclickGateway real.
"""
import json
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.billing.gateway import FakeGateway, RoutingGateway, TransbankOneclickGateway, set_gateway
from tests.billing_helpers import BILLING, subscribe_with_card

pytestmark = pytest.mark.asyncio
PREFIX = "/rswebpaytransaction/api/oneclick/v1.2"
ADMIN = "/api/v1/admin/billing"


class FakeTransbank:
    """Responde como webpay3gint.transbank.cl para Oneclick Mall v1.2."""

    def __init__(self):
        self.seen: list[tuple[str, str, dict]] = []
        self.tx: dict[str, dict] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix(PREFIX)
        body = json.loads(req.content) if req.content else {}
        self.seen.append((req.method, path, body))
        assert req.headers["Tbk-Api-Key-Id"] == "597055555541"
        if req.method == "POST" and path == "/inscriptions":
            return httpx.Response(200, json={"token": uuid.uuid4().hex + uuid.uuid4().hex,
                                             "url_webpay": "https://webpay3gint.transbank.cl/webpayserver/bp_multicode_inscription.cgi"})
        if req.method == "PUT" and path.startswith("/inscriptions/"):
            return httpx.Response(200, json={"response_code": 0, "tbk_user": str(uuid.uuid4()),
                                             "authorization_code": "1213", "card_type": "Visa",
                                             "card_number": "XXXXXXXXXXXX6623"})
        if req.method == "DELETE" and path == "/inscriptions":
            return httpx.Response(204)
        if req.method == "POST" and path == "/transactions":
            d = body["details"][0]
            tx = {"buy_order": body["buy_order"], "card_detail": {"card_number": "6623"},
                  "accounting_date": "1017", "transaction_date": "2026-10-17T21:30:00.000Z",
                  "details": [{"amount": d["amount"], "status": "AUTHORIZED", "authorization_code": "1213",
                               "payment_type_code": "VN", "response_code": 0, "installments_number": 0,
                               "commerce_code": d["commerce_code"], "buy_order": d["buy_order"]}]}
            self.tx[body["buy_order"]] = tx
            return httpx.Response(200, json=tx)
        if req.method == "POST" and path.endswith("/refunds"):
            return httpx.Response(200, json={"type": "REVERSED"})
        return httpx.Response(404, json={"error_message": "not found"})

    def paths(self) -> list[str]:
        return [f"{m} {p.split('/')[1]}" for m, p, _ in self.seen]


@pytest.fixture
def tbk():
    server = FakeTransbank()
    fake = FakeGateway()
    set_gateway(fake)            # primero: datos «demo» creados con el simulador
    yield server, fake
    set_gateway(None)


def use_transbank(server: FakeTransbank, fake: FakeGateway) -> None:
    primary = TransbankOneclickGateway("https://webpay3gint.transbank.cl", "597055555541", "secret",
                                       transport=httpx.MockTransport(server))
    set_gateway(RoutingGateway(primary, fake))


async def test_new_checkout_goes_to_transbank_and_demo_stays_on_simulator(client, app_headers, admin_headers, tbk,
                                                                          session):
    server, fake = tbk
    # 1. Usuario demo creado con el simulador (como los del seed)
    demo = app_headers()
    data, res = await subscribe_with_card(client, demo, "gold")
    assert res["result"] == "success"
    demo_sub = data["subscription"]["id"]

    # 2. Se cambia la API a Transbank
    use_transbank(server, fake)
    r = await client.get(f"{ADMIN}/summary", headers=admin_headers)
    assert r.json()["gateway"] == "transbank_oneclick"

    # 3. Renovación de la suscripción demo (lo que hace el cron) → simulador, Transbank no recibe nada
    from app.billing.enums import ChargeKind
    from app.billing.gateway import get_gateway
    from app.billing.models import Subscription
    from app.billing.service import BillingService
    sub = await session.get(Subscription, uuid.UUID(demo_sub))
    charge = await BillingService(session, get_gateway()).charge(sub, ChargeKind.renewal)
    assert charge.status == "authorized", charge.failure_message
    assert server.seen == []

    # 4. Usuario nuevo → Webpay real (form POST con TBK_TOKEN) → retorno por POST
    new = app_headers()
    r = await client.post(f"{BILLING}/subscriptions", headers=new, json={"plan_code": "gold"})
    assert r.status_code == 201, r.text
    ins = r.json()["inscription"]
    assert ins["url_webpay"].startswith("https://webpay3gint.transbank.cl/")
    r = await client.post(f"{BILLING}/oneclick/return", data={"TBK_TOKEN": ins["token"]})
    assert r.status_code == 303
    result = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert result["result"] == "success" and result["card"] == "6623"
    assert server.paths() == ["POST inscriptions", "PUT inscriptions", "POST transactions"]
    sub_id = result["subscription_id"]

    me = (await client.get(f"{BILLING}/me", headers=new)).json()
    assert me["payment_method"]["card_last4"] == "6623"

    # 5. Reembolso del cobro real → endpoint de Transbank
    charges = (await client.get(f"{ADMIN}/charges", headers=admin_headers,
                                params={"subscription_id": sub_id})).json()["items"]
    charge = next(c for c in charges if c["subscription_id"] == sub_id)
    r = await client.post(f"{ADMIN}/charges/{charge['id']}/refunds", headers=admin_headers,
                          json={"amount": charge["amount"], "reason": "Prueba de reembolso Transbank"})
    assert r.status_code == 201, r.text
    assert server.paths()[-1] == "POST transactions" and server.seen[-1][1].endswith("/refunds")


async def test_aborted_webpay_does_not_call_transbank_confirm(client, app_headers, tbk):
    server, fake = tbk
    use_transbank(server, fake)
    r = await client.post(f"{BILLING}/subscriptions", headers=app_headers(), json={"plan_code": "gold"})
    token = r.json()["inscription"]["token"]
    r = await client.post(f"{BILLING}/oneclick/return",
                          data={"TBK_TOKEN": token, "TBK_ORDEN_COMPRA": "x", "TBK_ID_SESION": "y"})
    assert parse_qs(urlparse(r.headers["location"]).query)["result"] == ["aborted"]
    assert server.paths() == ["POST inscriptions"]
