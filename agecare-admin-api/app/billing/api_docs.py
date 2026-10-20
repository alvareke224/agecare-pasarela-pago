"""Catálogo de endpoints de facturación: resumen, acceso y errores posibles de cada uno.

Es la fuente única de:
  · los resúmenes en español y las respuestas de error que muestra Swagger (/api/v1/admin/docs),
  · la referencia `docs/ENDPOINTS.md` (scripts/gen_api_reference.py).
`tests/test_api_docs.py` falla si se agrega un endpoint de facturación sin documentarlo aquí.
"""
from __future__ import annotations

from typing import Any

# Errores comunes a todos los endpoints
APP_AUTH = [(401, "UNAUTHORIZED", "Falta el JWT de la app, es inválido o expiró.")]
ADMIN_AUTH = [(401, "UNAUTHORIZED", "Falta el token de la consola o expiró."),
              (403, "FORBIDDEN", "El rol del usuario no tiene permiso sobre este módulo.")]
VALIDATION = [(422, "VALIDATION_ERROR", "Algún parámetro o campo del cuerpo no cumple su tipo o rango.")]
TBK_DOWN = [(502, "PAYMENT_PROVIDER_UNAVAILABLE", "Transbank no respondió al iniciar la inscripción.")]
SUB_NOT_FOUND_APP = [(404, "NOT_FOUND", "La suscripción no existe o no pertenece al usuario.")]
SUB_NOT_FOUND_ADMIN = [(404, "NOT_FOUND", "La suscripción no existe.")]
BENEFICIARY = [
    (422, "INVALID_BENEFICIARY", "Plan Dorado con un beneficiary_id distinto del propio usuario."),
    (422, "BENEFICIARY_REQUIRED", "Plan Platino sin beneficiary_id y el usuario no tiene exactamente una familia."),
    (403, "FORBIDDEN_BENEFICIARY", "La familia no es del usuario, o pide plan Proveedor sin ser proveedor (o con otro provider_id)."),
]

READ_ROLES = "Consola · admin, analyst, support"
WRITE_ROLES = "Consola · admin, support"
REFUND_ROLES = "Consola · admin"
APP = "App · JWT de usuario (Bearer)"

ENDPOINTS: dict[tuple[str, str], dict[str, Any]] = {
    # ------------------------------------------------------------------ app (usuarios finales)
    ("GET", "/api/v1/billing/plans"): {
        "summary": "Listar planes y precios vigentes",
        "auth": "Público", "errors": [(503, "PLAN_PRICE_MISSING", "No hay precio configurado para algún plan.")]},
    ("GET", "/api/v1/billing/me"): {
        "summary": "Estado de facturación del usuario (plan efectivo, suscripciones y tarjeta)",
        "auth": APP, "errors": APP_AUTH},
    ("GET", "/api/v1/billing/me/charges"): {
        "summary": "Historial de cobros del usuario (paginado)",
        "auth": APP, "errors": APP_AUTH + VALIDATION},
    ("POST", "/api/v1/billing/subscriptions"): {
        "summary": "Contratar un plan (inicia Webpay si no hay tarjeta o cobra con la inscrita)",
        "auth": APP, "errors": APP_AUTH + VALIDATION + BENEFICIARY + [
            (422, "PLAN_NOT_PURCHASABLE", "Se pidió el plan Gratuito."),
            (409, "SUBSCRIPTION_EXISTS", "El beneficiario ya tiene una suscripción vigente."),
            (409, "CHARGE_IN_PROGRESS", "Ya hay un cobro en curso para esa suscripción."),
            (503, "PLAN_PRICE_MISSING", "El plan no tiene precio configurado."),
        ] + TBK_DOWN},
    ("POST", "/api/v1/billing/subscriptions/{sub_id}/cancel"): {
        "summary": "Cancelar una suscripción (al fin del periodo si está activa; de inmediato si no)",
        "auth": APP, "errors": APP_AUTH + SUB_NOT_FOUND_APP + [
            (409, "INVALID_TRANSITION", "La suscripción ya terminó (cancelada o expirada).")]},
    ("POST", "/api/v1/billing/subscriptions/{sub_id}/resume"): {
        "summary": "Deshacer una cancelación programada",
        "auth": APP, "errors": APP_AUTH + SUB_NOT_FOUND_APP + [
            (409, "NOT_SCHEDULED_TO_CANCEL", "La suscripción no tiene una cancelación programada.")]},
    ("POST", "/api/v1/billing/subscriptions/{sub_id}/change-plan"): {
        "summary": "Programar un cambio de plan para la próxima renovación",
        "auth": APP, "errors": APP_AUTH + VALIDATION + SUB_NOT_FOUND_APP + BENEFICIARY + [
            (409, "INVALID_TRANSITION", "La suscripción no está activa."),
            (422, "PLAN_NOT_PURCHASABLE", "Se pidió el plan Gratuito (para eso se cancela)."),
            (409, "SAME_PLAN", "Ya está en ese plan y no hay cambio pendiente."),
            (409, "SUBSCRIPTION_EXISTS", "El nuevo beneficiario ya tiene otra suscripción vigente."),
        ]},
    ("POST", "/api/v1/billing/payment-methods"): {
        "summary": "Inscribir o cambiar la tarjeta (devuelve url_webpay + token)",
        "auth": APP, "errors": APP_AUTH + TBK_DOWN},
    ("DELETE", "/api/v1/billing/payment-methods/{pm_id}"): {
        "summary": "Eliminar la tarjeta inscrita",
        "auth": APP, "errors": APP_AUTH + [
            (404, "NOT_FOUND", "La tarjeta no existe, no es del usuario o no está activa."),
            (409, "PAYMENT_METHOD_IN_USE", "La tarjeta paga una suscripción activa o en mora."),
        ]},
    ("GET", "/api/v1/billing/oneclick/return"): {
        "summary": "Retorno de Webpay tras la inscripción (response_url)",
        "auth": "Webpay (TBK_TOKEN)",
        "notes": "No responde JSON: redirige (303) a ADMIN_BILLING_RETURN_URL con "
                 "`result` = success | charge_failed | charge_pending | rejected | aborted | error, "
                 "más `subscription_id` y `card` cuando aplican.",
        "errors": []},
    ("POST", "/api/v1/billing/oneclick/return"): {
        "summary": "Retorno de Webpay tras la inscripción (response_url, formulario POST)",
        "auth": "Webpay (TBK_TOKEN)", "notes": "Igual que la variante GET.", "errors": []},
    ("GET", "/api/v1/billing/internal/cron/billing-cycle"): {
        "summary": "Ciclo de facturación invocado por Vercel Cron",
        "auth": "Authorization: Bearer CRON_SECRET", "internal": True,
        "errors": [(401, "UNAUTHORIZED", "El secreto no coincide."),
                   (404, "NOT_FOUND", "CRON_SECRET no está configurado (endpoint deshabilitado).")]},
    # ------------------------------------------------------------------ consola de administración
    ("GET", "/api/v1/admin/billing/summary"): {
        "summary": "KPIs de facturación (MRR, activas, mora, aprobación, ingresos)",
        "auth": READ_ROLES, "errors": ADMIN_AUTH},
    ("GET", "/api/v1/admin/billing/subscriptions"): {
        "summary": "Buscar y filtrar suscripciones (paginado)",
        "auth": READ_ROLES, "errors": ADMIN_AUTH + VALIDATION},
    ("GET", "/api/v1/admin/billing/subscriptions/{sub_id}"): {
        "summary": "Detalle de una suscripción con sus cobros y eventos",
        "auth": READ_ROLES, "errors": ADMIN_AUTH + SUB_NOT_FOUND_ADMIN},
    ("POST", "/api/v1/admin/billing/subscriptions/{sub_id}/cancel"): {
        "summary": "Cancelar una suscripción desde la consola (inmediata o al fin del periodo)",
        "auth": WRITE_ROLES, "errors": ADMIN_AUTH + VALIDATION + SUB_NOT_FOUND_ADMIN + [
            (409, "INVALID_TRANSITION", "La suscripción ya terminó o no admite cancelación programada.")]},
    ("POST", "/api/v1/admin/billing/subscriptions/{sub_id}/retry-charge"): {
        "summary": "Reintentar ahora el cobro de una suscripción en mora",
        "auth": WRITE_ROLES, "errors": ADMIN_AUTH + SUB_NOT_FOUND_ADMIN + [
            (409, "NOT_PAST_DUE", "La suscripción no está en mora."),
            (409, "CHARGE_IN_PROGRESS", "Ya hay un cobro en curso."),
            (409, "NO_PAYMENT_METHOD", "No tiene una tarjeta activa."),
        ]},
    ("POST", "/api/v1/admin/billing/subscriptions/{sub_id}/extend"): {
        "summary": "Extender el periodo pagado sin cobrar (compensación)",
        "auth": WRITE_ROLES, "errors": ADMIN_AUTH + VALIDATION + SUB_NOT_FOUND_ADMIN + [
            (409, "INVALID_TRANSITION", "Solo se extienden suscripciones activas.")]},
    ("GET", "/api/v1/admin/billing/charges"): {
        "summary": "Buscar y filtrar cobros (paginado)",
        "auth": READ_ROLES, "errors": ADMIN_AUTH + VALIDATION + [
            (422, "INVALID_RANGE", "date_to es anterior a date_from.")]},
    ("GET", "/api/v1/admin/billing/charges/{charge_id}"): {
        "summary": "Detalle de un cobro con reembolsos y respuesta de Transbank",
        "auth": READ_ROLES, "errors": ADMIN_AUTH + [(404, "NOT_FOUND", "El cobro no existe.")]},
    ("POST", "/api/v1/admin/billing/charges/{charge_id}/refunds"): {
        "summary": "Reembolsar total o parcialmente un cobro aprobado",
        "auth": REFUND_ROLES, "errors": ADMIN_AUTH + VALIDATION + [
            (404, "NOT_FOUND", "El cobro no existe."),
            (409, "CHARGE_NOT_REFUNDABLE", "El cobro no está aprobado (o ya se reembolsó entero)."),
            (422, "REFUND_EXCEEDS_CHARGE", "El monto supera lo que queda por reembolsar."),
            (502, "REFUND_REJECTED", "Transbank rechazó el reembolso."),
            (504, "REFUND_UNCONFIRMED", "Transbank no respondió; hay que verificar antes de reintentar."),
        ]},
    ("POST", "/api/v1/admin/billing/jobs/billing-cycle"): {
        "summary": "Ejecutar ahora el ciclo de facturación",
        "auth": REFUND_ROLES, "errors": ADMIN_AUTH},
    ("POST", "/api/v1/admin/billing/dev/app-token"): {
        "summary": "Crear un usuario de prueba de la app (solo dev/staging)",
        "auth": WRITE_ROLES, "errors": ADMIN_AUTH + [
            (404, "NOT_FOUND", "Deshabilitado con ADMIN_ENVIRONMENT=prod.")]},
}

_ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}


def apply_to_openapi(spec: dict) -> dict:
    """Agrega resúmenes, acceso y errores del catálogo al esquema OpenAPI."""
    spec.setdefault("components", {}).setdefault("schemas", {})["ErrorResponse"] = {
        "title": "ErrorResponse", "type": "object",
        "properties": {"error": {"type": "object", "properties": {
            "code": {"type": "string", "example": "SUBSCRIPTION_EXISTS"},
            "message": {"type": "string", "example": "Ya existe una suscripción vigente para este beneficiario."},
            "details": {"type": "array", "nullable": True, "items": {"type": "object"}},
            "request_id": {"type": "string", "example": "9f2c41d0a1b2c3d4"}}}}}
    for (method, path), info in ENDPOINTS.items():
        op = spec.get("paths", {}).get(path, {}).get(method.lower())
        if op is None:
            continue
        op["summary"] = info["summary"]
        extra = f"**Acceso:** {info['auth']}"
        if info.get("notes"):
            extra += f"\n\n{info['notes']}"
        op["description"] = (op.get("description", "") + "\n\n" + extra).strip()
        responses = op.setdefault("responses", {})
        by_status: dict[int, list[tuple[str, str]]] = {}
        for status, code, when in info["errors"]:
            by_status.setdefault(status, []).append((code, when))
        for status, items in sorted(by_status.items()):
            responses[str(status)] = {
                "description": " · ".join(f"`{c}`: {w}" for c, w in items),
                "content": {"application/json": {"schema": _ERROR_SCHEMA}}}
    return spec
