"""Despliegue serverless (Vercel): URL de BD alojada, cron y simulador sin estado entre instancias."""
import pytest

from app.billing.gateway import FakeGateway, set_gateway
from app.config import Settings, normalize_database_url
from tests.billing_helpers import BILLING

pytestmark = pytest.mark.asyncio


def test_normalize_hosted_database_urls():
    neon = "postgres://u:p@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert normalize_database_url(neon) == "postgresql+asyncpg://u:p@ep-x-123.us-east-2.aws.neon.tech/neondb?ssl=require"
    assert normalize_database_url("postgresql://a:b@h:5432/db").startswith("postgresql+asyncpg://a:b@h:5432/db")
    assert normalize_database_url("sqlite+aiosqlite://") == "sqlite+aiosqlite://"


def test_vercel_postgres_env_fallback(monkeypatch):
    monkeypatch.delenv("ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@db.example.com/x?sslmode=require")
    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://u:p@db.example.com/x?ssl=require"


async def test_cron_endpoint_requires_secret(client, fake_gw, monkeypatch):
    from app.config import get_settings
    url = f"{BILLING}/internal/cron/billing-cycle"
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert (await client.get(url)).status_code == 404          # sin secreto: deshabilitado
    monkeypatch.setattr(get_settings(), "cron_secret", "s3cr3t-cron-0123456789")
    assert (await client.get(url, headers={"Authorization": "Bearer otro"})).status_code == 401
    r = await client.get(url, headers={"Authorization": "Bearer s3cr3t-cron-0123456789"})
    assert r.status_code == 200 and r.json()["errors"] == []


async def test_checkout_across_serverless_instances(client, app_headers):
    """Cada paso del checkout cae en una instancia distinta (simulador recién creado, sin memoria)."""
    h = app_headers()
    set_gateway(FakeGateway())
    data = (await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})).json()
    token = data["inscription"]["token"]
    assert len(token) == 64

    set_gateway(FakeGateway())        # instancia 2: página del simulador
    assert (await client.post(f"{BILLING}/fake-webpay", data={"TBK_TOKEN": token})).status_code == 200
    set_gateway(FakeGateway())        # instancia 3: decisión
    r = await client.post(f"{BILLING}/fake-webpay/decision", data={"TBK_TOKEN": token, "decision": "approve_visa"})
    back = r.headers["location"].replace("http://localhost:8000", "")
    set_gateway(FakeGateway())        # instancia 4: retorno de Webpay + primer cobro
    r = await client.get(back)
    assert "result=success" in r.headers["location"], r.headers["location"]
    set_gateway(FakeGateway())        # instancia 5: consulta
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["entitlement"]["plan_code"] == "gold"
    set_gateway(None)


async def test_forged_simulator_token_is_rejected(client, fake_gw):
    forged = "fk" + "0" * 40 + "f" * 22
    assert (await client.post(f"{BILLING}/fake-webpay", data={"TBK_TOKEN": forged})).status_code == 404
    r = await client.get(f"{BILLING}/oneclick/return", params={"TBK_TOKEN": forged, "fake_decision": "approve_visa"})
    assert "result=error" in r.headers["location"]


async def test_dev_app_token_for_testers(client, fake_gw, admin_headers, analyst_headers, monkeypatch):
    from app.config import get_settings
    url = "/api/v1/admin/billing/dev/app-token"
    assert (await client.post(url, headers=analyst_headers)).status_code == 403
    r = await client.post(url, headers=admin_headers)
    assert r.status_code == 200
    tok = r.json()
    h = {"Authorization": f"Bearer {tok['token']}"}
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 201 and r.json()["subscription"]["beneficiary_id"] == tok["family_id"]
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "provider"})
    assert r.status_code == 201
    monkeypatch.setattr(get_settings(), "environment", "prod")
    assert (await client.post(url, headers=admin_headers)).status_code == 404
