"""Reglas de negocio de pagos y suscripciones.

Principios que guían el código:
  1. El monto lo calcula siempre el servidor (plan_prices), nunca el cliente.
  2. Cada cobro se persiste en `pending` y se hace COMMIT antes de llamar a
     Transbank. Si la llamada no responde, el cobro queda `unknown` y el job lo
     concilia con GET /transactions/{buy_order}: nunca se cobra dos veces.
  3. Como máximo un cobro en vuelo por suscripción (índice único parcial).
  4. Todo cambio de estado deja un BillingEvent (línea de tiempo y notificaciones);
     las acciones de staff además quedan en audit_log (las registra el router).
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as core_models
from app.billing import crypto
from app.billing.app_auth import AppUser
from app.billing.enums import (LIVE_STATUSES, PAID_CHARGE_STATUSES, PLAN_UNITS, SUBSCRIPTION_TRANSITIONS,
                               BillingUnit, CancelReason, ChargeKind, ChargeStatus, PaymentMethodStatus,
                               SubscriptionStatus, rejection_for)
from app.logs import log_event
from app.billing.gateway import (AuthorizationResult, GatewayError, GatewayNotFound, GatewayUnavailable,
                                 InscriptionStart, PaymentGateway)
from app.billing.models import BillingEvent, Charge, PaymentMethod, Refund, Subscription
from app.billing.periods import add_months
from app.config import get_settings
from app.enums import PlanCode
from app.errors import ApiError, conflict, invalid, not_found
from app.security import as_utc, now_utc

log = logging.getLogger("agecare.billing")
# Nivel de log de cada evento de negocio (el resto es INFO)
ERROR_EVENTS = {"charge.amount_mismatch", "charge.succeeded_on_closed_subscription", "refund.unconfirmed"}
WARNING_SUFFIXES = (".failed", ".unknown", ".past_due", ".aborted", ".expired", ".skipped",
                    ".plan_change_skipped")

DEFAULT_RETRY_DAYS = [1, 3, 5]
INSCRIPTION_TTL = {"integration": timedelta(minutes=10), "production": timedelta(minutes=5)}


def provider_unavailable() -> ApiError:
    return ApiError(502, "PAYMENT_PROVIDER_UNAVAILABLE",
                    "No pudimos comunicarnos con Transbank. Intenta nuevamente en unos minutos.")


def new_buy_order(now: datetime) -> str:
    """Orden de compra única ≤ 26 caracteres: AG + aammdd + 10 hex (18)."""
    return f"AG{now:%y%m%d}{secrets.token_hex(5).upper()}"


def tbk_username(user_id: UUID) -> str:
    return "u" + user_id.hex   # 33 caracteres (máx. 40)


@dataclass
class InscriptionOutcome:
    result: str                       # success | rejected | aborted | error
    payment_method: PaymentMethod | None
    subscription: Subscription | None = None
    charge: Charge | None = None


class BillingService:
    def __init__(self, db: AsyncSession, gateway: PaymentGateway, now: datetime | None = None):
        self.db = db
        self.gw = gateway
        self._now = now
        self.settings = get_settings()

    def now(self) -> datetime:
        return self._now or now_utc()

    def gw_for(self, provider: str | None) -> PaymentGateway:
        """Pasarela que corresponde a un medio de pago (datos demo del seed → simulador)."""
        for_provider = getattr(self.gw, "for_provider", None)
        return for_provider(provider) if for_provider else self.gw

    async def gw_for_charge(self, charge: Charge) -> PaymentGateway:
        pm = await self.db.get(PaymentMethod, charge.payment_method_id)
        return self.gw_for(pm.provider if pm else None)

    # ================================================================ utilidades
    async def plan_price(self, plan: PlanCode) -> int:
        setting = await self.db.get(core_models.SystemSetting, "plan_prices")
        price = (setting.value or {}).get(plan.value) if setting else None
        if not isinstance(price, int) or price <= 0:
            raise ApiError(503, "PLAN_PRICE_MISSING",
                           "El precio del plan no está configurado. Avísale al equipo de AgeCare.")
        return price

    async def retry_days(self) -> list[int]:
        setting = await self.db.get(core_models.SystemSetting, "billing_dunning")
        days = (setting.value or {}).get("retry_days") if setting else None
        return list(days) if isinstance(days, list) else DEFAULT_RETRY_DAYS

    def event(self, type_: str, *, sub: Subscription | None = None, charge: Charge | None = None,
              user_id: UUID | None = None, actor_type: str = "system", actor_id: UUID | None = None,
              **payload) -> None:
        clean = {k: v for k, v in payload.items() if v is not None}
        uid = user_id or (sub.user_id if sub else None)
        self.db.add(BillingEvent(subscription_id=sub.id if sub else None,
                                 charge_id=charge.id if charge else None,
                                 user_id=uid, type=type_, actor_type=actor_type, actor_id=actor_id,
                                 payload=clean))
        level = (logging.ERROR if type_ in ERROR_EVENTS
                 else logging.WARNING if type_.endswith(WARNING_SUFFIXES) else logging.INFO)
        log_event(log, f"billing.{type_}", type_, level,
                  subscription_id=str(sub.id) if sub else None, charge_id=str(charge.id) if charge else None,
                  user_id=str(uid) if uid else None, actor_type=actor_type, **clean)

    @staticmethod
    def transition(sub: Subscription, to: SubscriptionStatus) -> None:
        current = SubscriptionStatus(sub.status)
        if to != current and to not in SUBSCRIPTION_TRANSITIONS[current]:
            raise conflict("INVALID_TRANSITION",
                           f"La suscripción no puede pasar de {current.value} a {to.value}.")
        sub.status = to.value
        if to in (SubscriptionStatus.canceled, SubscriptionStatus.expired):
            sub.next_charge_at = None
            sub.retry_count = 0
            sub.pending_plan_code = sub.pending_beneficiary_type = sub.pending_beneficiary_id = None
        if to == SubscriptionStatus.active:
            sub.retry_count = 0

    async def active_payment_method(self, user_id: UUID) -> PaymentMethod | None:
        return (await self.db.execute(
            select(PaymentMethod).where(PaymentMethod.user_id == user_id,
                                        PaymentMethod.status == PaymentMethodStatus.active)
        )).scalars().first()

    async def live_subscription_for(self, unit: str, beneficiary_id: UUID) -> Subscription | None:
        return (await self.db.execute(
            select(Subscription).where(Subscription.beneficiary_type == unit,
                                       Subscription.beneficiary_id == beneficiary_id,
                                       Subscription.status.in_(LIVE_STATUSES))
        )).scalars().first()

    @staticmethod
    def resolve_beneficiary(user: AppUser, plan: PlanCode, beneficiary_id: UUID | None) -> tuple[str, UUID]:
        unit = PLAN_UNITS[plan]
        if unit == BillingUnit.user:
            if beneficiary_id and beneficiary_id != user.id:
                raise invalid("INVALID_BENEFICIARY", "El plan Dorado se contrata para tu propia cuenta.")
            return unit.value, user.id
        if unit == BillingUnit.family:
            if beneficiary_id is None:
                if len(user.family_ids) != 1:
                    raise invalid("BENEFICIARY_REQUIRED", "Indica para qué familia contratas el plan Platino.")
                return unit.value, user.family_ids[0]
            if beneficiary_id not in user.family_ids:
                raise ApiError(403, "FORBIDDEN_BENEFICIARY", "Solo puedes contratar planes para tus familias.")
            return unit.value, beneficiary_id
        if user.provider_id is None or (beneficiary_id and beneficiary_id != user.provider_id):
            raise ApiError(403, "FORBIDDEN_BENEFICIARY",
                           "El plan Proveedor solo lo contrata una cuidadora o agencia registrada.")
        return unit.value, user.provider_id

    # ================================================================ inscripción de tarjeta
    async def start_inscription(self, user: AppUser) -> tuple[PaymentMethod, InscriptionStart]:
        s = self.settings
        username = tbk_username(user.id)
        response_url = s.public_base_url.rstrip("/") + "/api/v1/billing/oneclick/return"
        try:
            started = await self.gw.start_inscription(username, user.email[:100], response_url)
        except (GatewayError, GatewayUnavailable) as exc:
            log.warning("start_inscription falló: %s", exc)
            raise provider_unavailable()
        pm = PaymentMethod(user_id=user.id, user_email=user.email, provider=self.gw.name,
                           tbk_username=username, status=PaymentMethodStatus.pending.value,
                           inscription_token_hash=crypto.sha256_hex(started.token),
                           inscription_expires_at=self.now() + INSCRIPTION_TTL.get(s.tbk_environment,
                                                                                    timedelta(minutes=10)))
        self.db.add(pm)
        await self.db.flush()
        self.event("payment_method.inscription_started", user_id=user.id, actor_type="user", actor_id=user.id,
                   payment_method_id=str(pm.id))
        return pm, started

    async def finish_inscription(self, token: str, aborted: bool) -> InscriptionOutcome:
        pm = (await self.db.execute(select(PaymentMethod).where(
            PaymentMethod.inscription_token_hash == crypto.sha256_hex(token)))).scalars().first()
        if pm is None:
            raise not_found("La inscripción no existe o ya expiró.")
        latest = await self._latest_user_subscription(pm.user_id)
        if pm.status != PaymentMethodStatus.pending:   # retorno repetido: idempotente
            return InscriptionOutcome({"active": "success", "removed": "success", "aborted": "aborted"}
                                      .get(pm.status, "rejected"), pm, latest)
        if aborted:
            self._fail_inscription(pm, PaymentMethodStatus.aborted, "TBK_ABORTED")
            return InscriptionOutcome("aborted", pm, latest)
        try:
            result = await self.gw_for(pm.provider).finish_inscription(token)
        except GatewayUnavailable:
            self._fail_inscription(pm, PaymentMethodStatus.failed, "TBK_UNAVAILABLE")
            return InscriptionOutcome("error", pm, latest)
        except GatewayError:
            # Transbank responde error al confirmar un formulario expirado o anulado
            self._fail_inscription(pm, PaymentMethodStatus.aborted, "TBK_INSCRIPTION_EXPIRED")
            return InscriptionOutcome("aborted", pm, latest)
        if not result.approved:
            self._fail_inscription(pm, PaymentMethodStatus.failed, f"TBK_INSCRIPTION_REJECTED_{result.response_code}")
            return InscriptionOutcome("rejected", pm, latest)

        # Tarjeta inscrita: reemplaza la anterior y queda como medio de pago de sus suscripciones vivas
        previous = await self.active_payment_method(pm.user_id)
        if previous is not None:
            await self._remove_payment_method(previous, delete_remote=True)
            await self.db.flush()
        pm.status = PaymentMethodStatus.active.value
        pm.tbk_user_enc = crypto.encrypt(result.tbk_user)  # type: ignore[arg-type]
        pm.card_type, pm.card_last4 = result.card_type, result.card_last4
        pm.authorization_code = result.authorization_code
        pm.activated_at = self.now()
        self.event("payment_method.activated", user_id=pm.user_id, actor_type="user", actor_id=pm.user_id,
                   payment_method_id=str(pm.id), card_type=pm.card_type, card_last4=pm.card_last4)
        live = (await self.db.execute(select(Subscription).where(
            Subscription.user_id == pm.user_id, Subscription.status.in_(LIVE_STATUSES)))).scalars().all()
        for sub in live:
            sub.payment_method_id = pm.id
        await self.db.commit()

        # Cobra lo pendiente: primer pago de suscripciones nuevas y reintento inmediato de las morosas
        outcome = InscriptionOutcome("success", pm, latest)
        for sub in live:
            await self.db.refresh(sub)
            if sub.status == SubscriptionStatus.incomplete:
                charge = await self.charge(sub, ChargeKind.initial)
            elif sub.status == SubscriptionStatus.past_due and not await self._has_inflight(sub.id):
                charge = await self.charge(sub, ChargeKind.retry, count_failure=False)
            else:
                continue
            outcome.subscription, outcome.charge = sub, charge
            if charge.status == ChargeStatus.failed:
                outcome.result = "charge_failed"
            elif charge.status == ChargeStatus.unknown:
                outcome.result = "charge_pending"
        return outcome

    def _fail_inscription(self, pm: PaymentMethod, status: PaymentMethodStatus, code: str) -> None:
        pm.status, pm.failure_code = status.value, code
        self.event(f"payment_method.{status.value}", user_id=pm.user_id, payment_method_id=str(pm.id),
                   failure_code=code)

    async def _remove_payment_method(self, pm: PaymentMethod, delete_remote: bool) -> None:
        if delete_remote and pm.tbk_user_enc:
            try:
                await self.gw_for(pm.provider).delete_inscription(crypto.decrypt(pm.tbk_user_enc), pm.tbk_username)
            except (GatewayError, GatewayUnavailable) as exc:
                # La inscripción remota queda huérfana pero inutilizable para nosotros; se registra.
                log.warning("No se pudo eliminar la inscripción %s en Transbank: %s", pm.id, exc)
                pm.failure_code = "TBK_DELETE_FAILED"
        pm.status, pm.removed_at = PaymentMethodStatus.removed.value, self.now()
        self.event("payment_method.removed", user_id=pm.user_id, payment_method_id=str(pm.id))

    async def remove_payment_method(self, user: AppUser, pm_id: UUID) -> PaymentMethod:
        pm = await self.db.get(PaymentMethod, pm_id)
        if pm is None or pm.user_id != user.id or pm.status != PaymentMethodStatus.active:
            raise not_found("No encontramos esa tarjeta.")
        in_use = (await self.db.execute(select(Subscription.id).where(
            Subscription.payment_method_id == pm.id,
            Subscription.status.in_((SubscriptionStatus.active, SubscriptionStatus.past_due)),
            Subscription.cancel_at_period_end.is_(False)))).first()
        if in_use:
            raise conflict("PAYMENT_METHOD_IN_USE",
                           "Esta tarjeta paga una suscripción activa. Inscribe otra tarjeta o cancela la suscripción.")
        await self._remove_payment_method(pm, delete_remote=True)
        return pm

    async def _latest_user_subscription(self, user_id: UUID) -> Subscription | None:
        return (await self.db.execute(select(Subscription).where(Subscription.user_id == user_id)
                                      .order_by(Subscription.created_at.desc()).limit(1))).scalars().first()

    # ================================================================ suscribirse
    async def subscribe(self, user: AppUser, plan: PlanCode,
                        beneficiary_id: UUID | None) -> tuple[Subscription, Charge | None, InscriptionStart | None]:
        if plan not in PLAN_UNITS:
            raise invalid("PLAN_NOT_PURCHASABLE", "El plan Gratuito no requiere suscripción.")
        unit, beneficiary = self.resolve_beneficiary(user, plan, beneficiary_id)
        existing = await self.live_subscription_for(unit, beneficiary)
        if existing is not None:
            reusable = (existing.status == SubscriptionStatus.incomplete and existing.user_id == user.id
                        and existing.plan_code == plan)
            if not reusable:
                raise conflict("SUBSCRIPTION_EXISTS",
                               "Ya existe una suscripción vigente para este beneficiario.")
            sub = existing   # reintento del checkout: reutiliza la suscripción incompleta
        else:
            sub = Subscription(user_id=user.id, user_email=user.email, user_name=user.name,
                               plan_code=plan.value, beneficiary_type=unit, beneficiary_id=beneficiary,
                               status=SubscriptionStatus.incomplete.value,
                               price_amount=await self.plan_price(plan),
                               currency_code=self.settings.billing_currency)
            self.db.add(sub)
            try:
                await self.db.flush()
            except IntegrityError:   # otro checkout simultáneo creó la suscripción primero
                await self.db.rollback()
                raise conflict("SUBSCRIPTION_EXISTS", "Ya existe una suscripción vigente para este beneficiario.")
            self.event("subscription.created", sub=sub, actor_type="user", actor_id=user.id,
                       plan_code=plan.value, price_amount=sub.price_amount)

        pm = await self.active_payment_method(user.id)
        if pm is None:
            _, started = await self.start_inscription(user)
            await self.db.commit()
            return sub, None, started
        if await self._has_inflight(sub.id):
            raise conflict("CHARGE_IN_PROGRESS", "Estamos procesando un pago de esta suscripción.")
        sub.payment_method_id = pm.id
        await self.db.commit()
        charge = await self.charge(sub, ChargeKind.initial)
        return sub, charge, None

    # ================================================================ cobro
    async def _has_inflight(self, sub_id: UUID) -> bool:
        return bool((await self.db.execute(select(exists().where(
            Charge.subscription_id == sub_id,
            Charge.status.in_((ChargeStatus.pending, ChargeStatus.unknown)))))).scalar())

    async def charge(self, sub: Subscription, kind: ChargeKind, requested_by: UUID | None = None,
                     count_failure: bool = True) -> Charge:
        """Cobra el siguiente periodo de la suscripción. Hace COMMIT antes y después de Transbank."""
        pm = await self.db.get(PaymentMethod, sub.payment_method_id) if sub.payment_method_id else None
        if pm is None or pm.status != PaymentMethodStatus.active or not pm.tbk_user_enc:
            raise conflict("NO_PAYMENT_METHOD", "La suscripción no tiene una tarjeta activa para cobrar.")
        now = self.now()
        if kind == ChargeKind.initial:
            start, anchor = now, now.day
        else:
            start = as_utc(sub.current_period_end) or now
            anchor = sub.billing_anchor_day or start.day
        end = add_months(start, 1, anchor)
        buy_order = new_buy_order(now)
        charge = Charge(subscription_id=sub.id, payment_method_id=pm.id, user_id=sub.user_id,
                        plan_code=sub.plan_code, kind=kind.value,
                        attempt=sub.retry_count + 1 if kind != ChargeKind.initial else 1,
                        buy_order=buy_order, detail_buy_order=f"{buy_order}-S",
                        commerce_code=self.settings.tbk_child_commerce_code,
                        amount=sub.price_amount, currency_code=sub.currency_code,
                        status=ChargeStatus.pending.value, period_start=start, period_end=end,
                        requested_by=requested_by)
        self.db.add(charge)
        await self.db.commit()                       # ← persistido ANTES de llamar a Transbank

        try:
            result = await self.gw_for(pm.provider).authorize(pm.tbk_username, crypto.decrypt(pm.tbk_user_enc), charge.buy_order,
                                             charge.commerce_code, charge.detail_buy_order, charge.amount)
        except GatewayUnavailable as exc:
            log.warning("Cobro %s sin respuesta de Transbank: %s", charge.buy_order, exc)
            charge.status = ChargeStatus.unknown.value
            charge.failure_message = "Transbank no respondió; se conciliará automáticamente."
            self.event("charge.unknown", sub=sub, charge=charge, buy_order=charge.buy_order)
        except GatewayError as exc:
            await self._apply_failure(sub, charge, f"TBK_HTTP_{exc.status_code or 'ERROR'}", str(exc)[:300],
                                      None, count_failure)
        else:
            await self.apply_authorization(sub, charge, result, count_failure)
        await self.db.commit()
        return charge

    async def apply_authorization(self, sub: Subscription, charge: Charge, result: AuthorizationResult,
                                  count_failure: bool = True) -> None:
        charge.response_code = result.response_code
        charge.payment_type_code = result.payment_type_code
        charge.installments_number = result.installments_number
        charge.card_last4 = result.card_last4
        charge.transaction_date = result.transaction_date
        charge.accounting_date = result.accounting_date
        charge.gateway_response = result.raw
        if result.approved and result.amount == charge.amount:
            charge.status = ChargeStatus.authorized.value
            charge.authorization_code = result.authorization_code
            charge.failure_code = charge.failure_message = None
            charge.resolved_at = self.now()
            await self._on_success(sub, charge)
        elif result.approved:
            # Defensa: Transbank aprobó un monto distinto al solicitado (no debería ocurrir)
            charge.status = ChargeStatus.authorized.value
            charge.authorization_code = result.authorization_code
            charge.resolved_at = self.now()
            self.event("charge.amount_mismatch", sub=sub, charge=charge, expected=charge.amount,
                       received=result.amount)
            await self._on_success(sub, charge)
        else:
            code, message = rejection_for(result.response_code)
            if result.response_code == -96 and sub.payment_method_id:
                pm = await self.db.get(PaymentMethod, sub.payment_method_id)
                if pm and pm.status == PaymentMethodStatus.active:
                    pm.failure_code = code
                    await self._remove_payment_method(pm, delete_remote=False)
            await self._apply_failure(sub, charge, code, message, result.response_code, count_failure)

    async def _on_success(self, sub: Subscription, charge: Charge) -> None:
        previous = SubscriptionStatus(sub.status)
        sub.current_period_start, sub.current_period_end = charge.period_start, charge.period_end
        sub.next_charge_at = charge.period_end
        if previous == SubscriptionStatus.incomplete:
            sub.started_at, sub.billing_anchor_day = self.now(), as_utc(charge.period_start).day
            self.transition(sub, SubscriptionStatus.active)
            kind = "subscription.activated"
        elif previous == SubscriptionStatus.past_due:
            self.transition(sub, SubscriptionStatus.active)
            kind = "subscription.recovered"
        elif previous == SubscriptionStatus.active:
            kind = "subscription.renewed"
        else:   # cobro conciliado tarde sobre una suscripción ya cerrada: se informa para reembolsar
            self.event("charge.succeeded_on_closed_subscription", sub=sub, charge=charge,
                       subscription_status=sub.status)
            return
        actor = {"actor_type": "admin", "actor_id": charge.requested_by} if charge.requested_by else {}
        self.event("charge.succeeded", sub=sub, charge=charge, amount=charge.amount, buy_order=charge.buy_order,
                   **actor)
        self.event(kind, sub=sub, charge=charge, period_end=charge.period_end.isoformat())

    async def _apply_failure(self, sub: Subscription, charge: Charge, code: str, message: str,
                             response_code: int | None, count_failure: bool) -> None:
        charge.status = ChargeStatus.failed.value
        charge.failure_code, charge.failure_message = code, message
        charge.response_code = response_code if response_code is not None else charge.response_code
        charge.resolved_at = self.now()
        actor = {"actor_type": "admin", "actor_id": charge.requested_by} if charge.requested_by else {}
        self.event("charge.failed", sub=sub, charge=charge, failure_code=code, amount=charge.amount, **actor)
        if count_failure:
            await self.register_failure(sub)

    async def register_failure(self, sub: Subscription) -> None:
        """Avanza la política de reintentos (dunning) tras un cobro fallido de renovación."""
        if sub.status not in (SubscriptionStatus.active, SubscriptionStatus.past_due):
            return   # una suscripción incompleta sigue esperando que el usuario pague
        days = await self.retry_days()
        if sub.retry_count < len(days):
            if sub.status == SubscriptionStatus.active:
                self.transition(sub, SubscriptionStatus.past_due)
            sub.retry_count += 1
            sub.next_charge_at = self.now() + timedelta(days=days[sub.retry_count - 1])
            self.event("subscription.past_due", sub=sub, retry_count=sub.retry_count,
                       next_charge_at=sub.next_charge_at.isoformat())
        else:
            self.cancel_now(sub, CancelReason.payment_failed, note="Reintentos de cobro agotados.")

    # ================================================================ cancelación y cambios
    def cancel_now(self, sub: Subscription, reason: CancelReason, note: str | None = None,
                   actor_type: str = "system", actor_id: UUID | None = None) -> None:
        self.transition(sub, SubscriptionStatus.canceled)
        sub.canceled_at, sub.cancel_reason, sub.cancel_note = self.now(), reason.value, note
        sub.cancel_at_period_end = False
        self.event("subscription.canceled", sub=sub, actor_type=actor_type, actor_id=actor_id,
                   reason=reason.value, note=note)

    def schedule_cancel(self, sub: Subscription, actor_type: str, actor_id: UUID | None,
                        reason: CancelReason, note: str | None = None) -> None:
        if sub.status != SubscriptionStatus.active:
            raise conflict("INVALID_TRANSITION", "Solo una suscripción activa se cancela al fin del periodo.")
        sub.cancel_at_period_end = True
        sub.cancel_reason, sub.cancel_note = reason.value, note   # se hace efectiva al cerrar el periodo
        sub.pending_plan_code = sub.pending_beneficiary_type = sub.pending_beneficiary_id = None
        self.event("subscription.cancel_scheduled", sub=sub, actor_type=actor_type, actor_id=actor_id,
                   reason=reason.value, effective_at=as_utc(sub.current_period_end).isoformat())

    async def user_cancel(self, user: AppUser, sub: Subscription) -> Subscription:
        if sub.status == SubscriptionStatus.incomplete:
            self.cancel_now(sub, CancelReason.user_request, actor_type="user", actor_id=user.id)
        elif sub.status == SubscriptionStatus.past_due:
            self.cancel_now(sub, CancelReason.user_request, actor_type="user", actor_id=user.id)
        elif sub.status == SubscriptionStatus.active:
            if sub.cancel_at_period_end:
                return sub
            self.schedule_cancel(sub, "user", user.id, CancelReason.user_request)
        else:
            raise conflict("INVALID_TRANSITION", "La suscripción ya terminó.")
        return sub

    def resume(self, user: AppUser, sub: Subscription) -> Subscription:
        if sub.status != SubscriptionStatus.active or not sub.cancel_at_period_end:
            raise conflict("NOT_SCHEDULED_TO_CANCEL", "La suscripción no tiene una cancelación programada.")
        sub.cancel_at_period_end, sub.cancel_reason, sub.cancel_note = False, None, None
        self.event("subscription.resumed", sub=sub, actor_type="user", actor_id=user.id)
        return sub

    async def schedule_plan_change(self, user: AppUser, sub: Subscription, plan: PlanCode,
                                   beneficiary_id: UUID | None) -> Subscription:
        if sub.status != SubscriptionStatus.active:
            raise conflict("INVALID_TRANSITION", "Solo se puede cambiar el plan de una suscripción activa.")
        if plan not in PLAN_UNITS:
            raise invalid("PLAN_NOT_PURCHASABLE",
                          "Para volver al plan Gratuito cancela la suscripción; seguirá activa hasta el fin del periodo.")
        if plan == sub.plan_code and sub.pending_plan_code is None:
            raise conflict("SAME_PLAN", "Ya estás en ese plan.")
        unit, beneficiary = self.resolve_beneficiary(user, plan, beneficiary_id)
        other = await self.live_subscription_for(unit, beneficiary)
        if other is not None and other.id != sub.id:
            raise conflict("SUBSCRIPTION_EXISTS", "Ese beneficiario ya tiene una suscripción vigente.")
        if plan == sub.plan_code:   # deshace un cambio programado
            sub.pending_plan_code = sub.pending_beneficiary_type = sub.pending_beneficiary_id = None
        else:
            sub.pending_plan_code, sub.pending_beneficiary_type, sub.pending_beneficiary_id = plan.value, unit, beneficiary
        sub.cancel_at_period_end, sub.cancel_reason, sub.cancel_note = False, None, None
        self.event("subscription.plan_change_scheduled", sub=sub, actor_type="user", actor_id=user.id,
                   from_plan=sub.plan_code, to_plan=sub.pending_plan_code or sub.plan_code,
                   effective_at=as_utc(sub.current_period_end).isoformat())
        return sub

    async def apply_pending_plan(self, sub: Subscription) -> None:
        if not sub.pending_plan_code:
            return
        plan = PlanCode(sub.pending_plan_code)
        other = await self.live_subscription_for(sub.pending_beneficiary_type, sub.pending_beneficiary_id)
        if other is not None and other.id != sub.id:
            self.event("subscription.plan_change_skipped", sub=sub, reason="beneficiary_has_subscription")
        else:
            old = sub.plan_code
            sub.plan_code, sub.beneficiary_type = plan.value, sub.pending_beneficiary_type
            sub.beneficiary_id, sub.price_amount = sub.pending_beneficiary_id, await self.plan_price(plan)
            self.event("subscription.plan_changed", sub=sub, from_plan=old, to_plan=plan.value,
                       price_amount=sub.price_amount)
        sub.pending_plan_code = sub.pending_beneficiary_type = sub.pending_beneficiary_id = None

    # ================================================================ staff
    def extend(self, sub: Subscription, days: int, admin_id: UUID, reason: str) -> None:
        if sub.status != SubscriptionStatus.active:
            raise conflict("INVALID_TRANSITION", "Solo se puede extender una suscripción activa.")
        sub.current_period_end = as_utc(sub.current_period_end) + timedelta(days=days)
        sub.next_charge_at = sub.current_period_end
        self.event("subscription.extended", sub=sub, actor_type="admin", actor_id=admin_id, days=days,
                   reason=reason, period_end=sub.current_period_end.isoformat())

    async def refund(self, charge: Charge, amount: int, reason: str,
                     admin: core_models.AdminUser) -> Refund:
        if charge.status not in PAID_CHARGE_STATUSES:
            raise conflict("CHARGE_NOT_REFUNDABLE", "Solo se reembolsan cobros aprobados.")
        refundable = charge.amount - charge.refunded_amount
        if amount > refundable:
            raise invalid("REFUND_EXCEEDS_CHARGE",
                          f"El monto supera lo reembolsable ({refundable} {charge.currency_code}).")
        refund = Refund(charge_id=charge.id, amount=amount, reason=reason, requested_by=admin.id,
                        requested_by_name=admin.full_name, status="failed")
        try:
            result = await (await self.gw_for_charge(charge)).refund(charge.buy_order, charge.commerce_code, charge.detail_buy_order, amount)
        except GatewayUnavailable:
            refund.failure_message = ("Transbank no respondió. Verifica en el portal de Transbank "
                                      "si el reembolso se aplicó antes de reintentar.")
            self.db.add(refund)
            self.event("refund.unconfirmed", sub=charge.subscription, charge=charge, actor_type="admin",
                       actor_id=admin.id, amount=amount)
            await self.db.commit()
            raise ApiError(504, "REFUND_UNCONFIRMED", refund.failure_message)
        except GatewayError as exc:
            refund.failure_message = f"Transbank rechazó el reembolso: {exc}"[:300]
            refund.gateway_response = exc.body
            self.db.add(refund)
            self.event("refund.failed", sub=charge.subscription, charge=charge, actor_type="admin",
                       actor_id=admin.id, amount=amount)
            await self.db.commit()
            raise ApiError(502, "REFUND_REJECTED", refund.failure_message)
        refund.status, refund.refund_type = "succeeded", result.type
        refund.response_code, refund.authorization_code = result.response_code, result.authorization_code
        refund.gateway_response = result.raw
        self.db.add(refund)
        charge.refunded_amount += amount
        charge.status = (ChargeStatus.refunded if charge.refunded_amount == charge.amount
                         else ChargeStatus.partially_refunded).value
        self.event("charge.refunded", sub=charge.subscription, charge=charge, actor_type="admin",
                   actor_id=admin.id, amount=amount, refund_type=result.type)
        await self.db.flush()
        await self.db.refresh(refund)
        return refund

    # ================================================================ conciliación
    async def reconcile(self, charge: Charge) -> str:
        """Resuelve un cobro pending/unknown consultando su estado en Transbank."""
        sub = await self.db.get(Subscription, charge.subscription_id)
        try:
            result = await (await self.gw_for_charge(charge)).status(charge.buy_order)
        except GatewayNotFound:
            # La transacción nunca llegó a Transbank: no cuenta como rechazo del usuario
            await self._apply_failure(sub, charge, "TBK_NOT_PROCESSED",
                                      "La transacción no llegó a Transbank; se volverá a intentar.", None,
                                      count_failure=False)
            return "not_processed"
        except (GatewayUnavailable, GatewayError):
            return "still_unknown"
        await self.apply_authorization(sub, charge, result,
                                       count_failure=charge.kind != ChargeKind.manual)
        return charge.status
