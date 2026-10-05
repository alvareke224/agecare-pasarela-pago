import pytest

BASE = "/api/v1/admin"

pytestmark = pytest.mark.asyncio


async def test_login_ok(client):
    r = await client.post(f"{BASE}/auth/login",
                          json={"email": "admin@wellq.co.uk", "password": "Admin123!"})
    assert r.status_code == 200
    data = r.json()
    assert data["admin"]["role"] == "admin"
    assert data["access_token"] and data["refresh_token"]


async def test_login_bad_password(client):
    r = await client.post(f"{BASE}/auth/login",
                          json={"email": "admin@wellq.co.uk", "password": "Incorrecta1!"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_email_same_error(client):
    r = await client.post(f"{BASE}/auth/login",
                          json={"email": "nadie@wellq.co.uk", "password": "Incorrecta1!"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_me_and_permissions(client, admin_headers, analyst_headers):
    r = await client.get(f"{BASE}/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert "settings" in r.json()["permissions"]

    r2 = await client.get(f"{BASE}/auth/me", headers=analyst_headers)
    assert "settings" not in r2.json()["permissions"]


async def test_refresh_rotation(client):
    login = (await client.post(f"{BASE}/auth/login",
                               json={"email": "admin@wellq.co.uk", "password": "Admin123!"})).json()
    r1 = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r1.status_code == 200
    # reutilizar el token rotado debe fallar
    r2 = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "INVALID_REFRESH"


async def test_role_forbidden_on_settings(client, analyst_headers):
    r = await client.get(f"{BASE}/settings", headers=analyst_headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_requires_token(client):
    r = await client.get(f"{BASE}/metrics/commercial/plans")
    assert r.status_code == 401
