import pytest

BASE = "/api/v1/admin"

pytestmark = pytest.mark.asyncio


async def test_commercial_summary(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/commercial/summary",
                         params={"period": "current_month"}, headers=analyst_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["active_users"] > 0
    assert data["new_users"] >= 0
    assert 0 <= data["paying_share"] <= 1
    assert data["period"]["key"] == "current_month"


async def test_commercial_summary_invalid_period(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/commercial/summary",
                         params={"period": "quincena"}, headers=analyst_headers)
    assert r.status_code == 422


async def test_registrations_contract(client, analyst_headers):
    for period in ("today", "last_7_days", "ytd"):
        r = await client.get(f"{BASE}/metrics/commercial/registrations",
                             params={"period": period}, headers=analyst_headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["buckets"]) == len(data["signups"]) == len(data["cancellations"])


async def test_plans_mix(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/commercial/plans", headers=analyst_headers)
    data = r.json()
    assert [p["plan_code"] for p in data["plans"]] == ["free", "gold", "platinum", "provider"]
    assert data["totals"]["users"] == sum(p["users"] for p in data["plans"])
    assert abs(sum(p["share"] for p in data["plans"]) - 1.0) < 0.01


async def test_funnel_monotonic(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/commercial/funnel", headers=analyst_headers)
    stages = r.json()["stages"]
    users = [s["users"] for s in stages]
    assert users == sorted(users, reverse=True)


async def test_ops_status(client, analyst_headers):
    r = await client.get(f"{BASE}/ops/status", headers=analyst_headers)
    data = r.json()
    assert data["overall"] == "degraded"  # el asistente IA está degradado en el seed
    assert len(data["components"]) == 9


async def test_latency_invalid_window(client, analyst_headers):
    r = await client.get(f"{BASE}/ops/latency", params={"window": "5m"}, headers=analyst_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_WINDOW"


async def test_roles_weekly_series(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/roles/weekly-active",
                         params={"weeks": 8}, headers=analyst_headers)
    data = r.json()
    assert len(data["weeks"]) == 8
    for serie in data["series"]:
        assert len(serie["values"]) == 8


async def test_adoption_matrix_null_vs_zero(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/features/adoption", headers=analyst_headers)
    data = r.json()
    assert data["roles"] == ["family", "caregiver", "elder", "doctor"]
    by_key = {f["feature_key"]: f["adoption"] for f in data["features"]}
    assert by_key["checkin"][0] is None          # no aplica al familiar
    assert by_key["checkin"][1] is not None      # sí aplica a la cuidadora
    assert by_key["entertainment"][2] is not None


async def test_adoption_alerts_expected_low(client, analyst_headers):
    r = await client.get(f"{BASE}/metrics/features/alerts", headers=analyst_headers)
    alerts = r.json()["alerts"]
    keys = [a["feature_key"] for a in alerts]
    assert "marketplace" in keys
    sos = next(a for a in alerts if a["feature_key"] == "sos")
    assert sos["expected_low"] is True
