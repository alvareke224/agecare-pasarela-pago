"""Todo endpoint de facturación debe estar en el catálogo (Swagger y docs/ENDPOINTS.md)."""
from app.billing.api_docs import ENDPOINTS
from app.main import app


def documented_public() -> set[tuple[str, str]]:
    return {k for k, v in ENDPOINTS.items() if not v.get("internal")}


def billing_operations() -> set[tuple[str, str]]:
    spec = app.openapi()
    return {(m.upper(), p) for p, ops in spec["paths"].items() if "/billing" in p for m in ops}


def test_every_billing_endpoint_is_documented():
    assert billing_operations() == documented_public()


def test_openapi_has_spanish_summary_and_errors():
    spec = app.openapi()
    op = spec["paths"]["/api/v1/admin/billing/charges/{charge_id}/refunds"]["post"]
    assert op["summary"].startswith("Reembolsar")
    assert "REFUND_EXCEEDS_CHARGE" in op["responses"]["422"]["description"]
    assert op["responses"]["504"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
