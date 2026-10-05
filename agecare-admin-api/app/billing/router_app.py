"""Endpoints de pago para la app de AgeCare: /api/v1/billing/…

Flujo de checkout:
  1. POST /subscriptions {plan_code}
       · con tarjeta inscrita → cobra al instante → next_action = none
       · sin tarjeta → next_action = redirect_to_webpay: la app envía un form
         POST a inscription.url_webpay con TBK_TOKEN
  2. Webpay vuelve a GET|POST /oneclick/return?TBK_TOKEN=… → se confirma la
     inscripción, se cobra el primer mes y se redirige a ADMIN_BILLING_RETURN_URL
     con ?result=success|rejected|aborted|charge_failed|charge_pending|error
"""
import hmac
from html import escape
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app import models as core_models
from app.billing.app_auth import AppUser, CurrentAppUser
from app.billing.enums import PAID_PLANS, PLAN_UNITS, PREMIUM_STATUSES, SubscriptionStatus
from app.billing.gateway import FakeGateway, PaymentGateway, get_gateway
from app.billing.models import Charge, Subscription
from app.billing.schemas import (ChangePlanIn, ChargeOut, EntitlementOut, InscriptionOut, MeOut,
                                 PaymentMethodOut, PlanOut, PlansOut, SubscribeIn, SubscribeOut,
                                 SubscriptionOut)
from app.billing.service import BillingService
from app.config import get_settings
from app.deps import Db
from app.enums import PLAN_NAMES, PlanCode
from app.errors import ApiError, not_found
from app.schemas.common import Page
from app.security import as_utc

router = APIRouter(prefix="/api/v1/billing", tags=["Pagos · app"])

PLAN_RANK = {PlanCode.free: 0, PlanCode.gold: 1, PlanCode.provider: 2, PlanCode.platinum: 3}


def svc(db: Db, gateway: PaymentGateway = Depends(get_gateway)) -> BillingService:
    return BillingService(db, gateway)


async def _own_subscription(db: Db, user: AppUser, sub_id: UUID) -> Subscription:
    sub = await db.get(Subscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise not_found("No encontramos esa suscripción.")
    return sub


# ---------------------------------------------------------------- catálogo
@router.get("/plans", response_model=PlansOut)
async def plans(db: Db):
    """Planes de pago con su precio vigente (público: lo usa la pantalla de planes)."""
    setting = await db.get(core_models.SystemSetting, "plan_prices")
    prices = setting.value if setting else {}
    return PlansOut(plans=[PlanOut(plan_code=p, name=PLAN_NAMES[p], billing_unit=PLAN_UNITS[p].value,
                                   price_amount=prices.get(p.value, 0), currency_code=get_settings().billing_currency)
                           for p in PAID_PLANS if prices.get(p.value)])


# ---------------------------------------------------------------- mi facturación
@router.get("/me", response_model=MeOut)
async def me(user: CurrentAppUser, db: Db, service: BillingService = Depends(svc)):
    """Suscripciones del usuario, tarjeta activa y plan efectivo (derecho a funciones premium).

    El plan efectivo considera las suscripciones que paga el usuario y las de sus familias
    o proveedor (un miembro de una familia Platino tiene Platino aunque no sea quien paga).
    """
    beneficiaries = [user.id, *user.family_ids] + ([user.provider_id] if user.provider_id else [])
    subs = (await db.execute(select(Subscription).where(
        (Subscription.user_id == user.id) | Subscription.beneficiary_id.in_(beneficiaries))
        .order_by(Subscription.created_at.desc()))).unique().scalars().all()
    premium = [s for s in subs if s.status in PREMIUM_STATUSES]
    best = max(premium, key=lambda s: PLAN_RANK[PlanCode(s.plan_code)], default=None)
    entitlement = EntitlementOut(
        plan_code=PlanCode(best.plan_code) if best else PlanCode.free,
        plan_name=PLAN_NAMES[PlanCode(best.plan_code)] if best else PLAN_NAMES[PlanCode.free],
        subscription_id=best.id if best else None,
        valid_until=as_utc(best.current_period_end) if best else None)
    return MeOut(entitlement=entitlement, subscriptions=[SubscriptionOut.of(s) for s in subs if s.user_id == user.id],
                 payment_method=PaymentMethodOut.of(await service.active_payment_method(user.id)))


@router.get("/me/charges", response_model=Page[ChargeOut])
async def my_charges(user: CurrentAppUser, db: Db, page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=20, ge=1, le=100)):
    base = select(Charge).where(Charge.user_id == user.id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(Charge.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).unique().scalars().all()
    return Page[ChargeOut](items=[ChargeOut.of(c) for c in rows], page=page, page_size=page_size, total=total)


# ---------------------------------------------------------------- suscribirse
@router.post("/subscriptions", response_model=SubscribeOut, status_code=status.HTTP_201_CREATED)
async def subscribe(body: SubscribeIn, user: CurrentAppUser, service: BillingService = Depends(svc)):
    sub, charge, started = await service.subscribe(user, body.plan_code, body.beneficiary_id)
    await service.db.refresh(sub)
    if started:
        return SubscribeOut(subscription=SubscriptionOut.of(sub), next_action="redirect_to_webpay",
                            inscription=InscriptionOut(url_webpay=started.url_webpay, token=started.token))
    return SubscribeOut(subscription=SubscriptionOut.of(sub), next_action="none",
                        charge=ChargeOut.of(charge) if charge else None)


@router.post("/subscriptions/{sub_id}/cancel", response_model=SubscriptionOut)
async def cancel(sub_id: UUID, user: CurrentAppUser, db: Db, service: BillingService = Depends(svc)):
    """Cancela al final del periodo pagado (si está activa) o de inmediato (pendiente o en mora)."""
    sub = await service.user_cancel(user, await _own_subscription(db, user, sub_id))
    return SubscriptionOut.of(sub)


@router.post("/subscriptions/{sub_id}/resume", response_model=SubscriptionOut)
async def resume(sub_id: UUID, user: CurrentAppUser, db: Db, service: BillingService = Depends(svc)):
    return SubscriptionOut.of(service.resume(user, await _own_subscription(db, user, sub_id)))


@router.post("/subscriptions/{sub_id}/change-plan", response_model=SubscriptionOut)
async def change_plan(sub_id: UUID, body: ChangePlanIn, user: CurrentAppUser, db: Db,
                      service: BillingService = Depends(svc)):
    """Programa el cambio de plan para la próxima renovación (sin prorrateo en v1)."""
    sub = await service.schedule_plan_change(user, await _own_subscription(db, user, sub_id),
                                             body.plan_code, body.beneficiary_id)
    return SubscriptionOut.of(sub)


# ---------------------------------------------------------------- tarjeta
@router.post("/payment-methods", response_model=InscriptionOut, status_code=status.HTTP_201_CREATED)
async def add_payment_method(user: CurrentAppUser, service: BillingService = Depends(svc)):
    """Inicia la inscripción de una tarjeta nueva (reemplaza la actual al confirmarse)."""
    _, started = await service.start_inscription(user)
    return InscriptionOut(url_webpay=started.url_webpay, token=started.token)


@router.delete("/payment-methods/{pm_id}", response_model=PaymentMethodOut)
async def remove_payment_method(pm_id: UUID, user: CurrentAppUser, service: BillingService = Depends(svc)):
    return PaymentMethodOut.of(await service.remove_payment_method(user, pm_id))


# ---------------------------------------------------------------- retorno de Webpay
async def _tbk_params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", errors="replace")
        params.update({k: v[0] for k, v in parse_qs(body).items() if v})
    return params


@router.api_route("/oneclick/return", methods=["GET", "POST"], include_in_schema=True,
                  summary="Retorno de Webpay tras la inscripción (response_url)")
async def oneclick_return(request: Request, service: BillingService = Depends(svc)):
    params = await _tbk_params(request)
    token = params.get("TBK_TOKEN", "")
    if not (8 <= len(token) <= 128):
        return _redirect_result("error")
    aborted = "TBK_ORDEN_COMPRA" in params or "TBK_ID_SESION" in params
    if isinstance(service.gw, FakeGateway) and params.get("fake_decision"):
        try:   # simulador: la decisión de la página viene en la URL (sin estado entre instancias)
            service.gw.apply_decision(token, params["fake_decision"])
        except Exception:  # noqa: BLE001
            return _redirect_result("error")
    try:
        outcome = await service.finish_inscription(token, aborted=aborted)
    except ApiError:
        return _redirect_result("error")
    return _redirect_result(outcome.result,
                            subscription_id=outcome.subscription.id if outcome.subscription else None,
                            card_last4=outcome.payment_method.card_last4 if outcome.payment_method else None)


def _redirect_result(result: str, subscription_id: UUID | None = None, card_last4: str | None = None):
    query = {"result": result}
    if subscription_id:
        query["subscription_id"] = str(subscription_id)
    if card_last4:
        query["card"] = card_last4
    url = get_settings().billing_return_url
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------- cron (Vercel Cron Jobs)
@router.get("/internal/cron/billing-cycle", include_in_schema=False)
async def cron_billing_cycle(request: Request, db: Db, gateway: PaymentGateway = Depends(get_gateway)):
    """Lo invoca Vercel Cron con `Authorization: Bearer $CRON_SECRET`. Sin secreto configurado: 404."""
    secret = get_settings().cron_secret
    if not secret:
        raise not_found()
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise ApiError(401, "UNAUTHORIZED", "Cron no autorizado.")
    from app.billing.jobs import run_billing_cycle
    stats = await run_billing_cycle(db, gateway)
    return stats.as_dict()


# ---------------------------------------------------------------- simulador de Webpay (solo gateway=fake)
_FAKE_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Webpay (simulador)</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f6f9;margin:0;display:grid;place-items:center;min-height:100vh}}
.c{{background:#fff;border-radius:14px;box-shadow:0 8px 24px rgba(0,0,0,.08);padding:28px;max-width:380px;width:calc(100% - 32px)}}
h1{{font-size:18px;margin:0 0 4px}}p{{color:#5b6573;font-size:13px;margin:0 0 18px}}
button{{width:100%;padding:12px;border-radius:10px;border:1px solid #cfd6df;background:#fff;font-size:14px;margin-bottom:10px;cursor:pointer;text-align:left}}
button.ok{{background:#3b6fd4;color:#fff;border-color:#3b6fd4}}small{{display:block;color:#8b95a3;margin-top:8px}}</style></head>
<body><div class="c"><h1>Webpay · Inscripción Oneclick</h1>
<p>Simulador local de Transbank para desarrollo. Ningún cobro es real.</p>
<form method="post" action="{action}"><input type="hidden" name="TBK_TOKEN" value="{token}">
<button class="ok" name="decision" value="approve_visa">Inscribir Visa ···· 6623 (pagos aprobados)</button>
<button name="decision" value="approve_mastercard">Inscribir Mastercard ···· 0568 (pagos rechazados)</button>
<button name="decision" value="reject">Rechazar la inscripción</button>
<button name="decision" value="abort">Anular y volver al comercio</button></form>
<small>Usuario {username}</small></div></body></html>"""


def _fake_gateway() -> FakeGateway:
    gw = get_gateway()
    if not isinstance(gw, FakeGateway):
        raise not_found()
    return gw


@router.api_route("/fake-webpay", methods=["GET", "POST"], include_in_schema=False)
async def fake_webpay(request: Request):
    gw = _fake_gateway()
    token = (await _tbk_params(request)).get("TBK_TOKEN", "")
    username = gw.username_from_token(token)
    if username is None:
        return HTMLResponse("<p>Token de inscripción inválido.</p>", status_code=404)
    return HTMLResponse(_FAKE_PAGE.format(action="/api/v1/billing/fake-webpay/decision",
                                          token=escape(token), username=escape(username)))


@router.post("/fake-webpay/decision", include_in_schema=False)
async def fake_webpay_decision(request: Request):
    gw = _fake_gateway()
    params = await _tbk_params(request)
    token, decision = params.get("TBK_TOKEN", ""), params.get("decision", "")
    username = gw.username_from_token(token)
    if username is None or decision not in FakeGateway.DECISIONS:
        return HTMLResponse("<p>Token de inscripción inválido.</p>", status_code=404)
    query = {"TBK_TOKEN": token, "fake_decision": decision}
    if decision == "abort":   # Webpay agrega estos parámetros cuando el usuario anula
        query |= {"TBK_ORDEN_COMPRA": token[:20], "TBK_ID_SESION": username}
    return RedirectResponse(f"{gw.response_url()}?{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER)
