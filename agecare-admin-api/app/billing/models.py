"""Modelos SQLAlchemy de facturación (tablas billing_*).

Mismas convenciones que app/models.py: PK UUID, timestamps con zona y JSON
portable. El modelo físico canónico (tenant_id, RLS, triggers de estado,
historial y grants) está en db/agecare_billing_ddl.sql.
"""
import uuid
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Uuid,
                        func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models import PortableJSON


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentMethod(Base):
    """Tarjeta inscrita en Transbank Oneclick Mall (solo tipo y últimos 4 dígitos)."""
    __tablename__ = "billing_payment_methods"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)   # ref lógica → app.users.id
    user_email: Mapped[str] = mapped_column(String(254))
    provider: Mapped[str] = mapped_column(String(24), default="transbank_oneclick")
    tbk_username: Mapped[str] = mapped_column(String(40))
    inscription_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    inscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tbk_user_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    authorization_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (   # una tarjeta activa por usuario
        Index("billing_pm_one_active_uq", "user_id", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
    )


class Subscription(Base):
    __tablename__ = "billing_subscriptions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)   # pagador
    user_email: Mapped[str] = mapped_column(String(254), index=True)
    user_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan_code: Mapped[str] = mapped_column(String(12), index=True)
    beneficiary_type: Mapped[str] = mapped_column(String(12))
    beneficiary_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(12), default="incomplete", index=True)
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("billing_payment_methods.id"),
                                                                nullable=True)
    price_amount: Mapped[int] = mapped_column(Integer)
    currency_code: Mapped[str] = mapped_column(String(3), default="CLP")
    billing_anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_charge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_plan_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    pending_beneficiary_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    pending_beneficiary_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancel_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    payment_method: Mapped[PaymentMethod | None] = relationship(lazy="joined")

    __table_args__ = (   # una suscripción viva por beneficiario (protege contra checkouts simultáneos)
        Index("billing_sub_one_live_uq", "beneficiary_type", "beneficiary_id", unique=True,
              sqlite_where=text("status IN ('incomplete','active','past_due')"),
              postgresql_where=text("status IN ('incomplete','active','past_due')")),
    )


class Charge(Base):
    """Un intento de cobro a Transbank (transacción Oneclick Mall con un detalle)."""
    __tablename__ = "billing_charges"
    id: Mapped[uuid.UUID] = _pk()
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_subscriptions.id"), index=True)
    payment_method_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_payment_methods.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    plan_code: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(10))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    buy_order: Mapped[str] = mapped_column(String(26), unique=True)
    detail_buy_order: Mapped[str] = mapped_column(String(26), unique=True)
    commerce_code: Mapped[str] = mapped_column(String(12))
    amount: Mapped[int] = mapped_column(Integer)
    currency_code: Mapped[str] = mapped_column(String(3), default="CLP")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    refunded_amount: Mapped[int] = mapped_column(Integer, default=0)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authorization_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    payment_type_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    installments_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    transaction_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accounting_date: Mapped[str | None] = mapped_column(String(4), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gateway_response: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    subscription: Mapped[Subscription] = relationship(lazy="joined")

    # Como máximo un cobro en vuelo (pending/unknown) por suscripción: evita doble cobro
    # si dos workers procesan la misma renovación.
    __table_args__ = (
        Index("billing_charges_one_inflight_uq", "subscription_id", unique=True,
              sqlite_where=text("status IN ('pending','unknown')"),
              postgresql_where=text("status IN ('pending','unknown')")),
    )


class Refund(Base):
    """Reembolso solicitado desde la consola. Inmutable."""
    __tablename__ = "billing_refunds"
    id: Mapped[uuid.UUID] = _pk()
    charge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_charges.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))          # succeeded | failed
    refund_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # REVERSED | NULLIFIED
    authorization_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    failure_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gateway_response: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    requested_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = _created()


class BillingEvent(Base):
    """Bitácora inmutable del ciclo de vida; la app la consume para notificar."""
    __tablename__ = "billing_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("billing_subscriptions.id"),
                                                              nullable=True, index=True)
    charge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("billing_charges.id"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    actor_type: Mapped[str] = mapped_column(String(10))    # user | admin | system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_at: Mapped[datetime] = _created()


BILLING_TABLES = [PaymentMethod.__table__, Subscription.__table__, Charge.__table__,
                  Refund.__table__, BillingEvent.__table__]
