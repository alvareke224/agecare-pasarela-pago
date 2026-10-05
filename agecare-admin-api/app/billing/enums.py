"""Enumeraciones y catálogos de facturación."""
from enum import StrEnum

from app.enums import PlanCode


class BillingUnit(StrEnum):
    user = "user"
    family = "family"
    provider = "provider"


# Unidad facturable de cada plan de pago (= admin.plans.billing_unit)
PLAN_UNITS: dict[PlanCode, BillingUnit] = {
    PlanCode.gold: BillingUnit.user,
    PlanCode.platinum: BillingUnit.family,
    PlanCode.provider: BillingUnit.provider,
}
PAID_PLANS = tuple(PLAN_UNITS)


class SubscriptionStatus(StrEnum):
    incomplete = "incomplete"   # creada, esperando tarjeta / primer pago
    active = "active"
    past_due = "past_due"       # cobro rechazado, en reintentos (mantiene acceso)
    canceled = "canceled"       # terminal
    expired = "expired"         # terminal: nunca se pagó


LIVE_STATUSES = (SubscriptionStatus.incomplete, SubscriptionStatus.active, SubscriptionStatus.past_due)
PREMIUM_STATUSES = (SubscriptionStatus.active, SubscriptionStatus.past_due)

SUBSCRIPTION_TRANSITIONS = {
    SubscriptionStatus.incomplete: {SubscriptionStatus.active, SubscriptionStatus.expired,
                                    SubscriptionStatus.canceled},
    SubscriptionStatus.active: {SubscriptionStatus.past_due, SubscriptionStatus.canceled},
    SubscriptionStatus.past_due: {SubscriptionStatus.active, SubscriptionStatus.canceled},
    SubscriptionStatus.canceled: set(),
    SubscriptionStatus.expired: set(),
}


class CancelReason(StrEnum):
    user_request = "user_request"
    payment_failed = "payment_failed"
    admin = "admin"
    fraud = "fraud"
    plan_change = "plan_change"


class PaymentMethodStatus(StrEnum):
    pending = "pending"     # inscripción iniciada en Webpay
    active = "active"
    failed = "failed"       # Webpay rechazó la inscripción
    aborted = "aborted"     # el usuario canceló o expiró el formulario
    removed = "removed"


class ChargeKind(StrEnum):
    initial = "initial"
    renewal = "renewal"
    retry = "retry"
    manual = "manual"       # reintento lanzado desde la consola


class ChargeStatus(StrEnum):
    pending = "pending"     # insertado antes de llamar a Transbank
    authorized = "authorized"
    failed = "failed"
    unknown = "unknown"     # Transbank no respondió: se concilia con GET status
    refunded = "refunded"
    partially_refunded = "partially_refunded"


PAID_CHARGE_STATUSES = (ChargeStatus.authorized, ChargeStatus.partially_refunded, ChargeStatus.refunded)


class RefundType(StrEnum):
    reversed = "REVERSED"     # reversa: mismo día, monto total
    nullified = "NULLIFIED"   # anulación: parcial o días posteriores


SUBSCRIPTION_STATUS_NAMES = {
    SubscriptionStatus.incomplete: "Pendiente de pago", SubscriptionStatus.active: "Activa",
    SubscriptionStatus.past_due: "En mora", SubscriptionStatus.canceled: "Cancelada",
    SubscriptionStatus.expired: "Expirada",
}

# Códigos de rechazo de Oneclick Mall (detalle de la autorización)
TBK_RESPONSE_MESSAGES = {
    -96: ("TBK_INSCRIPTION_NOT_FOUND", "La tarjeta inscrita ya no existe en Transbank. Inscribe una nueva."),
    -97: ("TBK_DAILY_AMOUNT_LIMIT", "Se alcanzó el monto máximo diario de pago de la tarjeta."),
    -98: ("TBK_AMOUNT_LIMIT", "El monto supera el máximo permitido por transacción."),
    -99: ("TBK_DAILY_COUNT_LIMIT", "Se alcanzó la cantidad máxima diaria de pagos con la tarjeta."),
}
GENERIC_REJECTION = ("TBK_REJECTED", "El emisor de la tarjeta rechazó el pago.")


def rejection_for(response_code: int | None) -> tuple[str, str]:
    return TBK_RESPONSE_MESSAGES.get(response_code or 0, GENERIC_REJECTION)
