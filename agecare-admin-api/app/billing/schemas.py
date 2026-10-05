"""Esquemas Pydantic de la pasarela de pago (app y consola)."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.billing.enums import (SUBSCRIPTION_STATUS_NAMES, CancelReason, ChargeKind, ChargeStatus,
                               PaymentMethodStatus, SubscriptionStatus)
from app.billing.models import BillingEvent, Charge, PaymentMethod, Refund, Subscription
from app.enums import PLAN_NAMES, PlanCode
from app.security import as_utc


# ---------------------------------------------------------------- comunes
class PlanOut(BaseModel):
    plan_code: PlanCode
    name: str
    billing_unit: str
    price_amount: int
    currency_code: str


class PlansOut(BaseModel):
    plans: list[PlanOut]


class PaymentMethodOut(BaseModel):
    id: UUID
    card_type: str | None
    card_last4: str | None
    status: PaymentMethodStatus
    activated_at: datetime | None

    @classmethod
    def of(cls, pm: PaymentMethod | None) -> "PaymentMethodOut | None":
        if pm is None:
            return None
        return cls(id=pm.id, card_type=pm.card_type, card_last4=pm.card_last4,
                   status=PaymentMethodStatus(pm.status), activated_at=as_utc(pm.activated_at))


class SubscriptionOut(BaseModel):
    id: UUID
    plan_code: PlanCode
    plan_name: str
    status: SubscriptionStatus
    status_name: str
    beneficiary_type: str
    beneficiary_id: UUID
    price_amount: int
    currency_code: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    next_charge_at: datetime | None
    cancel_at_period_end: bool
    pending_plan_code: PlanCode | None
    retry_count: int
    started_at: datetime | None
    canceled_at: datetime | None
    cancel_reason: str | None
    payment_method: PaymentMethodOut | None
    created_at: datetime

    @classmethod
    def of(cls, s: Subscription) -> "SubscriptionOut":
        status = SubscriptionStatus(s.status)
        return cls(id=s.id, plan_code=PlanCode(s.plan_code), plan_name=PLAN_NAMES[PlanCode(s.plan_code)],
                   status=status, status_name=SUBSCRIPTION_STATUS_NAMES[status],
                   beneficiary_type=s.beneficiary_type, beneficiary_id=s.beneficiary_id,
                   price_amount=s.price_amount, currency_code=s.currency_code,
                   current_period_start=as_utc(s.current_period_start),
                   current_period_end=as_utc(s.current_period_end), next_charge_at=as_utc(s.next_charge_at),
                   cancel_at_period_end=s.cancel_at_period_end,
                   pending_plan_code=PlanCode(s.pending_plan_code) if s.pending_plan_code else None,
                   retry_count=s.retry_count, started_at=as_utc(s.started_at),
                   canceled_at=as_utc(s.canceled_at),
                   cancel_reason=s.cancel_reason if s.status == SubscriptionStatus.canceled or s.cancel_at_period_end else None,
                   payment_method=PaymentMethodOut.of(s.payment_method), created_at=as_utc(s.created_at))


class ChargeOut(BaseModel):
    id: UUID
    subscription_id: UUID
    plan_code: PlanCode
    kind: ChargeKind
    buy_order: str
    amount: int
    refunded_amount: int
    currency_code: str
    status: ChargeStatus
    period_start: datetime
    period_end: datetime
    card_last4: str | None
    authorization_code: str | None
    transaction_date: datetime | None
    failure_message: str | None
    created_at: datetime

    @classmethod
    def of(cls, c: Charge) -> "ChargeOut":
        return cls(id=c.id, subscription_id=c.subscription_id, plan_code=PlanCode(c.plan_code),
                   kind=ChargeKind(c.kind), buy_order=c.buy_order, amount=c.amount,
                   refunded_amount=c.refunded_amount, currency_code=c.currency_code,
                   status=ChargeStatus(c.status), period_start=as_utc(c.period_start),
                   period_end=as_utc(c.period_end), card_last4=c.card_last4,
                   authorization_code=c.authorization_code, transaction_date=as_utc(c.transaction_date),
                   failure_message=c.failure_message, created_at=as_utc(c.created_at))


# ---------------------------------------------------------------- app
class SubscribeIn(BaseModel):
    plan_code: PlanCode
    beneficiary_id: UUID | None = Field(default=None, description="Familia (Platino) o proveedor (Proveedor). "
                                                                  "Opcional si el usuario tiene una sola.")


class InscriptionOut(BaseModel):
    """El cliente debe enviar un formulario POST a `url_webpay` con el campo TBK_TOKEN = token."""
    url_webpay: str
    token: str
    method: Literal["POST"] = "POST"
    token_field: Literal["TBK_TOKEN"] = "TBK_TOKEN"


class SubscribeOut(BaseModel):
    subscription: SubscriptionOut
    next_action: Literal["redirect_to_webpay", "none"]
    inscription: InscriptionOut | None = None
    charge: ChargeOut | None = None


class EntitlementOut(BaseModel):
    plan_code: PlanCode
    plan_name: str
    subscription_id: UUID | None
    valid_until: datetime | None


class MeOut(BaseModel):
    entitlement: EntitlementOut
    subscriptions: list[SubscriptionOut]
    payment_method: PaymentMethodOut | None


class ChangePlanIn(BaseModel):
    plan_code: PlanCode
    beneficiary_id: UUID | None = None


# ---------------------------------------------------------------- consola
class AdminPaymentMethodOut(PaymentMethodOut):
    provider: str
    failure_code: str | None
    removed_at: datetime | None

    @classmethod
    def of(cls, pm: PaymentMethod | None) -> "AdminPaymentMethodOut | None":
        if pm is None:
            return None
        return cls(id=pm.id, card_type=pm.card_type, card_last4=pm.card_last4,
                   status=PaymentMethodStatus(pm.status), activated_at=as_utc(pm.activated_at),
                   provider=pm.provider, failure_code=pm.failure_code, removed_at=as_utc(pm.removed_at))


class AdminSubscriptionOut(SubscriptionOut):
    user_id: UUID
    user_email: str
    user_name: str | None
    cancel_note: str | None
    payment_method: AdminPaymentMethodOut | None  # type: ignore[assignment]

    @classmethod
    def of(cls, s: Subscription) -> "AdminSubscriptionOut":
        base = SubscriptionOut.of(s).model_dump(exclude={"payment_method"})
        return cls(**base, user_id=s.user_id, user_email=s.user_email, user_name=s.user_name,
                   cancel_note=s.cancel_note, payment_method=AdminPaymentMethodOut.of(s.payment_method))


class AdminChargeOut(ChargeOut):
    user_id: UUID
    user_email: str
    attempt: int
    response_code: int | None
    failure_code: str | None
    commerce_code: str
    detail_buy_order: str
    resolved_at: datetime | None
    requested_by: UUID | None

    @classmethod
    def of(cls, c: Charge) -> "AdminChargeOut":
        return cls(**ChargeOut.of(c).model_dump(), user_id=c.user_id,
                   user_email=c.subscription.user_email if c.subscription else "",
                   attempt=c.attempt, response_code=c.response_code, failure_code=c.failure_code,
                   commerce_code=c.commerce_code, detail_buy_order=c.detail_buy_order,
                   resolved_at=as_utc(c.resolved_at), requested_by=c.requested_by)


class RefundOut(BaseModel):
    id: UUID
    charge_id: UUID
    amount: int
    status: str
    refund_type: str | None
    authorization_code: str | None
    reason: str
    failure_message: str | None
    requested_by: UUID
    requested_by_name: str | None
    created_at: datetime

    @classmethod
    def of(cls, r: Refund) -> "RefundOut":
        return cls(id=r.id, charge_id=r.charge_id, amount=r.amount, status=r.status, refund_type=r.refund_type,
                   authorization_code=r.authorization_code, reason=r.reason, failure_message=r.failure_message,
                   requested_by=r.requested_by, requested_by_name=r.requested_by_name,
                   created_at=as_utc(r.created_at))


class EventOut(BaseModel):
    id: int
    type: str
    actor_type: str
    actor_id: UUID | None
    charge_id: UUID | None
    payload: dict
    created_at: datetime

    @classmethod
    def of(cls, e: BillingEvent) -> "EventOut":
        return cls(id=e.id, type=e.type, actor_type=e.actor_type, actor_id=e.actor_id, charge_id=e.charge_id,
                   payload=e.payload or {}, created_at=as_utc(e.created_at))


class AdminSubscriptionDetailOut(BaseModel):
    subscription: AdminSubscriptionOut
    charges: list[AdminChargeOut]
    events: list[EventOut]


class AdminChargeDetailOut(BaseModel):
    charge: AdminChargeOut
    refunds: list[RefundOut]
    refundable_amount: int
    gateway_response: dict | None


class RefundIn(BaseModel):
    amount: int = Field(gt=0, description="Monto a reembolsar en la moneda del cobro (CLP sin decimales).")
    reason: str = Field(min_length=5, max_length=500)


class AdminCancelIn(BaseModel):
    immediate: bool = Field(default=False, description="true = cancela ya; false = al fin del periodo pagado.")
    reason: Literal[CancelReason.admin, CancelReason.fraud, CancelReason.user_request] = CancelReason.admin
    note: str = Field(min_length=5, max_length=500)


class ExtendIn(BaseModel):
    days: int = Field(ge=1, le=90)
    reason: str = Field(min_length=5, max_length=500)


class PlanRow(BaseModel):
    plan_code: PlanCode
    plan_name: str
    active: int
    past_due: int
    mrr: int


class SubscriptionCounts(BaseModel):
    active: int
    past_due: int
    incomplete: int
    cancel_scheduled: int
    canceled_30d: int
    started_30d: int


class ChargeStats(BaseModel):
    approved: int
    failed: int
    approval_rate: float | None
    gross_revenue: int
    refunded: int
    net_revenue: int


class Upcoming(BaseModel):
    count: int
    amount: int


class BillingSummaryOut(BaseModel):
    currency_code: str
    mrr: int
    plans: list[PlanRow]
    subscriptions: SubscriptionCounts
    charges_30d: ChargeStats
    upcoming_7d: Upcoming
    unresolved_charges: int
    gateway: str
    computed_at: datetime


class CycleOut(BaseModel):
    reconciled: int
    renewed: int
    recovered: int
    failed: int
    unknown: int
    canceled: int
    expired_subscriptions: int
    expired_inscriptions: int
    errors: list[str]
