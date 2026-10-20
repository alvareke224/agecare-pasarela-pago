"""Engine de logs: formato JSON, request_id, eventos de facturación y datos sensibles ocultos."""
import json
import logging

import pytest

from app.logs import JsonFormatter, log_event, redact, safe_path
from tests.billing_helpers import subscribe_with_card

pytestmark = pytest.mark.asyncio


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter("test"))
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured():
    h = Capture()
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.INFO)
    yield h.lines
    root.removeHandler(h)
    root.setLevel(old)


def test_redact_nested_and_paths():
    data = {"tbk_user": "abc", "token": "x", "amount": 1000,
            "nested": {"Authorization": "Bearer y", "card_number": "4051885600446623"}}
    assert redact(data) == {"tbk_user": "***", "token": "***", "amount": 1000,
                            "nested": {"Authorization": "***", "card_number": "***"}}
    assert safe_path("/inscriptions/" + "e" * 64) == "/inscriptions/eeeeee…"
    assert safe_path("/api/v1/billing/plans") == "/api/v1/billing/plans"


def test_line_has_fixed_fields(captured):
    log_event(logging.getLogger("agecare.test"), "demo.event", "hola", amount=5, tbk_user="secreto")
    line = captured[-1]
    assert {"ts", "level", "logger", "event", "msg", "service", "env"} <= line.keys()
    assert line["event"] == "demo.event" and line["amount"] == 5 and line["tbk_user"] == "***"
    assert line["ts"].endswith("Z") and line["service"] == "agecare-admin-api"


async def test_checkout_logs_are_correlated_and_safe(client, app_headers, fake_gw, captured):
    r = await client.post("/api/v1/billing/subscriptions", headers={**app_headers(), "X-Request-Id": "req-test-123"},
                          json={"plan_code": "gold"})
    assert r.headers["X-Request-Id"] == "req-test-123"
    same = [ln for ln in captured if ln.get("request_id") == "req-test-123"]
    events = [ln["event"] for ln in same]
    assert "billing.payment_method.inscription_started" in events
    access = next(ln for ln in same if ln["event"] == "http.request")
    assert access["status"] == 201 and access["method"] == "POST" and access["duration_ms"] >= 0
    assert access["actor"].startswith("user:")

    captured.clear()
    await subscribe_with_card(client, app_headers(), "gold")
    events = {ln["event"] for ln in captured}
    assert {"billing.payment_method.activated", "billing.charge.succeeded", "http.request"} <= events
    dump = json.dumps(captured)
    assert "tbkuser-" not in dump            # el tbk_user nunca aparece en los logs
    assert "Bearer " not in dump


async def test_errors_are_logged_with_code(client, captured):
    r = await client.get("/api/v1/admin/billing/summary")
    assert r.status_code == 401
    rid = r.headers["X-Request-Id"]
    err = next(ln for ln in captured if ln["event"] == "http.error" and ln.get("request_id") == rid)
    assert err["code"] == "UNAUTHORIZED" and err["status"] == 401
    access = next(ln for ln in captured if ln["event"] == "http.request" and ln.get("request_id") == rid)
    assert access["level"] == "WARNING"
