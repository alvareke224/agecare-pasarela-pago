import pytest

BASE = "/api/v1/admin"

pytestmark = pytest.mark.asyncio


async def test_support_summary(client, admin_headers):
    r = await client.get(f"{BASE}/support/summary", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["resolved_30d"] > 0
    assert data["csat_avg"] is None or 1 <= data["csat_avg"] <= 5


async def test_ticket_flow(client, admin_headers):
    # crear (correo con histórico en el seed)
    r = await client.post(f"{BASE}/support/tickets", headers=admin_headers,
                          json={"subject": "Prueba de flujo completo",
                                "description": "Creado por la suite de tests.",
                                "user_email": "usuario1@demo.cl",
                                "category": "other", "channel": "phone"})
    assert r.status_code == 201, r.text
    ticket = r.json()
    tid = ticket["id"]
    assert ticket["status"] == "open"

    # responder fija first_response_at
    r = await client.post(f"{BASE}/support/tickets/{tid}/replies", headers=admin_headers,
                          json={"body": "Hola, estamos revisando tu caso."})
    assert r.status_code == 201
    detail = (await client.get(f"{BASE}/support/tickets/{tid}", headers=admin_headers)).json()
    assert detail["first_response_at"] is not None
    assert detail["replies_count"] == 1

    # transición válida open -> in_progress -> resolved
    r = await client.patch(f"{BASE}/support/tickets/{tid}", headers=admin_headers,
                           json={"status": "in_progress"})
    assert r.status_code == 200
    r = await client.patch(f"{BASE}/support/tickets/{tid}", headers=admin_headers,
                           json={"status": "resolved"})
    assert r.status_code == 200

    # cerrar y verificar que no se puede reabrir ni responder
    r = await client.patch(f"{BASE}/support/tickets/{tid}", headers=admin_headers,
                           json={"status": "closed"})
    assert r.status_code == 200
    r = await client.patch(f"{BASE}/support/tickets/{tid}", headers=admin_headers,
                           json={"status": "open"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"
    r = await client.post(f"{BASE}/support/tickets/{tid}/replies", headers=admin_headers,
                          json={"body": "¿Sigues ahí?"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_CLOSED"


async def test_create_unknown_user_needs_confirm(client, admin_headers):
    payload = {"subject": "Usuario sin cuenta", "description": "Llamada telefónica.",
               "user_email": "desconocido@example.com", "category": "other"}
    r = await client.post(f"{BASE}/support/tickets", headers=admin_headers, json=payload)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"
    r = await client.post(f"{BASE}/support/tickets", headers=admin_headers,
                          json={**payload, "confirm_unlinked": True})
    assert r.status_code == 201


async def test_ticket_lookup_by_number(client, admin_headers):
    r = await client.get(f"{BASE}/support/tickets/%231482", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["number"] == 1482


async def test_settings_optimistic_lock(client, admin_headers):
    r = await client.get(f"{BASE}/settings", params={"key": "plan_prices"}, headers=admin_headers)
    setting = r.json()["settings"][0]
    ok = await client.put(f"{BASE}/settings/plan_prices", headers=admin_headers,
                          json={"value": {"gold": 1200, "platinum": 10000, "provider": 5000},
                                "version": setting["version"],
                                "change_note": "Ajuste de precio Dorado por test."})
    assert ok.status_code == 200
    stale = await client.put(f"{BASE}/settings/plan_prices", headers=admin_headers,
                             json={"value": {"gold": 1300, "platinum": 10000, "provider": 5000},
                                   "version": setting["version"],
                                   "change_note": "Intento con versión vieja."})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    bad = await client.put(f"{BASE}/settings/plan_prices", headers=admin_headers,
                           json={"value": {"gold": -5, "platinum": 10000, "provider": 5000},
                                 "version": setting["version"] + 1,
                                 "change_note": "Valor inválido a propósito."})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_VALUE"


async def test_audit_log_records_actions(client, admin_headers):
    r = await client.get(f"{BASE}/audit-log", params={"action": "settings.update"},
                         headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1
