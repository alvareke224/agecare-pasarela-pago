"""Helpers para recorrer el checkout con el simulador de Webpay."""
from urllib.parse import parse_qs, urlparse

BILLING = "/api/v1/billing"


async def complete_webpay(client, inscription: dict, decision: str = "approve_visa") -> dict:
    """Simula al usuario en Webpay y el retorno a response_url. Devuelve los parámetros del redirect final."""
    page = await client.post(inscription["url_webpay"].replace("http://localhost:8000", ""),
                             data={"TBK_TOKEN": inscription["token"]})
    assert page.status_code == 200, page.text
    r = await client.post(f"{BILLING}/fake-webpay/decision",
                          data={"TBK_TOKEN": inscription["token"], "decision": decision})
    assert r.status_code == 303
    back = urlparse(r.headers["location"])
    assert back.path == f"{BILLING}/oneclick/return"
    r = await client.get(f"{back.path}?{back.query}")
    assert r.status_code == 303, r.text
    final = urlparse(r.headers["location"])
    return {k: v[0] for k, v in parse_qs(final.query).items()}


async def subscribe_with_card(client, headers: dict, plan: str = "gold", decision: str = "approve_visa",
                              **body) -> tuple[dict, dict]:
    r = await client.post(f"{BILLING}/subscriptions", headers=headers, json={"plan_code": plan, **body})
    assert r.status_code == 201, r.text
    data = r.json()
    result = {}
    if data["next_action"] == "redirect_to_webpay":
        result = await complete_webpay(client, data["inscription"], decision)
    return data, result
