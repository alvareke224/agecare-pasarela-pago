"""Pasarela de pago: interfaz + Transbank Oneclick Mall (REST v1.2) + simulador.

Referencia: https://www.transbankdevelopers.cl/referencia/oneclick
    POST   /rswebpaytransaction/api/oneclick/v1.2/inscriptions            iniciar inscripción
    PUT    /rswebpaytransaction/api/oneclick/v1.2/inscriptions/{token}    confirmar inscripción
    DELETE /rswebpaytransaction/api/oneclick/v1.2/inscriptions            eliminar inscripción
    POST   /rswebpaytransaction/api/oneclick/v1.2/transactions            autorizar (cobrar)
    GET    /rswebpaytransaction/api/oneclick/v1.2/transactions/{buy_order} estado
    POST   /rswebpaytransaction/api/oneclick/v1.2/transactions/{buy_order}/refunds  reembolso

El resto del módulo solo conoce `PaymentGateway`; cambiar de proveedor
(p. ej. Mercado Pago para México) es escribir otra implementación.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from app.config import get_settings
from app.logs import log_event, safe_path

tbk_log = logging.getLogger("agecare.billing.transbank")

API_PATH = "/rswebpaytransaction/api/oneclick/v1.2"


# ---------------------------------------------------------------- errores
class GatewayError(Exception):
    """Transbank respondió con un error definitivo (4xx): la operación no ocurrió."""

    def __init__(self, message: str, status_code: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class GatewayUnavailable(Exception):
    """Timeout, error de red o 5xx: el resultado es DESCONOCIDO y debe conciliarse."""


class GatewayNotFound(GatewayError):
    """404: la transacción consultada no existe en Transbank."""


# ---------------------------------------------------------------- resultados
@dataclass
class InscriptionStart:
    token: str
    url_webpay: str


@dataclass
class InscriptionResult:
    response_code: int
    tbk_user: str | None
    authorization_code: str | None
    card_type: str | None
    card_last4: str | None
    raw: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.response_code == 0 and bool(self.tbk_user)


@dataclass
class AuthorizationResult:
    buy_order: str
    status: str                 # AUTHORIZED | FAILED | … (estado del detalle)
    response_code: int
    authorization_code: str | None
    amount: int
    card_last4: str | None
    payment_type_code: str | None
    installments_number: int | None
    transaction_date: datetime | None
    accounting_date: str | None
    raw: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.response_code == 0 and self.status == "AUTHORIZED"


@dataclass
class RefundResult:
    type: str                   # REVERSED | NULLIFIED
    response_code: int | None
    authorization_code: str | None
    raw: dict = field(default_factory=dict)


class PaymentGateway(Protocol):
    name: str

    async def start_inscription(self, username: str, email: str, response_url: str) -> InscriptionStart: ...
    async def finish_inscription(self, token: str) -> InscriptionResult: ...
    async def delete_inscription(self, tbk_user: str, username: str) -> None: ...
    async def authorize(self, username: str, tbk_user: str, buy_order: str, child_commerce_code: str,
                        child_buy_order: str, amount: int) -> AuthorizationResult: ...
    async def status(self, buy_order: str) -> AuthorizationResult: ...
    async def refund(self, buy_order: str, child_commerce_code: str, child_buy_order: str,
                     amount: int) -> RefundResult: ...


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_authorization(body: dict) -> AuthorizationResult:
    """Convierte la respuesta de autorizar/estado (mall con un detalle) a AuthorizationResult."""
    detail = (body.get("details") or [{}])[0]
    return AuthorizationResult(
        buy_order=body.get("buy_order", ""),
        status=str(detail.get("status") or "FAILED"),
        response_code=int(detail.get("response_code", -1)),
        authorization_code=detail.get("authorization_code") or None,
        amount=int(detail.get("amount") or 0),
        card_last4=(body.get("card_detail") or {}).get("card_number"),
        payment_type_code=detail.get("payment_type_code"),
        installments_number=detail.get("installments_number"),
        transaction_date=_parse_dt(body.get("transaction_date")),
        accounting_date=body.get("accounting_date"),
        raw=body,
    )


# ---------------------------------------------------------------- Transbank
class TransbankOneclickGateway:
    """Cliente async de Transbank Oneclick Mall (sin SDK: httpx, testeable con respx)."""

    name = "transbank_oneclick"

    def __init__(self, base_url: str, commerce_code: str, api_secret: str, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + API_PATH,
            timeout=timeout,
            transport=transport,
            headers={"Tbk-Api-Key-Id": commerce_code, "Tbk-Api-Key-Secret": api_secret,
                     "Content-Type": "application/json"},
        )

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        start, shown = time.perf_counter(), safe_path(path)
        try:
            r = await self._client.request(method, path, json=json)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log_event(tbk_log, "tbk.unavailable", f"{method} {shown} sin respuesta", logging.WARNING,
                      method=method, path=shown, error=exc.__class__.__name__,
                      duration_ms=round((time.perf_counter() - start) * 1000, 1))
            raise GatewayUnavailable(f"Transbank no respondió: {exc.__class__.__name__}") from exc
        log_event(tbk_log, "tbk.response", f"{method} {shown} → {r.status_code}",
                  logging.WARNING if r.status_code >= 400 else logging.INFO,
                  method=method, path=shown, status=r.status_code,
                  duration_ms=round((time.perf_counter() - start) * 1000, 1))
        if r.status_code >= 500:
            raise GatewayUnavailable(f"Transbank respondió {r.status_code}")
        body: dict = {}
        if r.content:
            try:
                body = r.json()
            except ValueError:
                body = {"raw": r.text[:500]}
        if r.status_code == 404:
            raise GatewayNotFound(body.get("error_message", "No encontrado"), 404, body)
        if r.status_code >= 400:
            raise GatewayError(body.get("error_message", f"HTTP {r.status_code}"), r.status_code, body)
        return body

    async def start_inscription(self, username: str, email: str, response_url: str) -> InscriptionStart:
        body = await self._request("POST", "/inscriptions",
                                   {"username": username, "email": email, "response_url": response_url})
        return InscriptionStart(token=body["token"], url_webpay=body["url_webpay"])

    async def finish_inscription(self, token: str) -> InscriptionResult:
        body = await self._request("PUT", f"/inscriptions/{token}")
        return InscriptionResult(response_code=int(body.get("response_code", -1)),
                                 tbk_user=body.get("tbk_user"),
                                 authorization_code=body.get("authorization_code"),
                                 card_type=body.get("card_type"),
                                 card_last4=(body.get("card_number") or "")[-4:] or None,
                                 raw={k: v for k, v in body.items() if k != "tbk_user"})

    async def delete_inscription(self, tbk_user: str, username: str) -> None:
        await self._request("DELETE", "/inscriptions", {"tbk_user": tbk_user, "username": username})

    async def authorize(self, username: str, tbk_user: str, buy_order: str, child_commerce_code: str,
                        child_buy_order: str, amount: int) -> AuthorizationResult:
        body = await self._request("POST", "/transactions", {
            "username": username, "tbk_user": tbk_user, "buy_order": buy_order,
            "details": [{"commerce_code": child_commerce_code, "buy_order": child_buy_order,
                         "amount": amount, "installments_number": 1}],
        })
        return parse_authorization(body)

    async def status(self, buy_order: str) -> AuthorizationResult:
        return parse_authorization(await self._request("GET", f"/transactions/{buy_order}"))

    async def refund(self, buy_order: str, child_commerce_code: str, child_buy_order: str,
                     amount: int) -> RefundResult:
        body = await self._request("POST", f"/transactions/{buy_order}/refunds", {
            "commerce_code": child_commerce_code, "detail_buy_order": child_buy_order, "amount": amount})
        return RefundResult(type=body.get("type", "NULLIFIED"),
                            response_code=body.get("response_code"),
                            authorization_code=body.get("authorization_code"), raw=body)

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------- simulador
@dataclass
class _FakeInscription:
    username: str
    email: str
    response_url: str
    outcome: str = "pending"    # pending | approved | rejected | aborted


class FakeGateway:
    """Simulador de Oneclick Mall para desarrollo, QA, demos en Vercel y tests.

    · La inscripción redirige a /api/v1/billing/fake-webpay, una página que
      imita el formulario de Webpay (aprobar / rechazar / anular).
    · Funciona sin estado compartido (serverless: cada petición puede caer en otra
      instancia): el token va firmado con HMAC y lleva el usuario; la decisión
      tomada en la página vuelve en la URL de retorno (`fake_decision`), y el
      tbk_user lleva la tarjeta en su sufijo.
    · Tarjetas: inscripciones aprobadas con la «Visa 6623» cobran OK; las
      inscritas con la «Mastercard 0568» son rechazadas por el emisor.
    · Los tests fuerzan resultados con `queue_authorization(...)`.
    """

    name = "fake"

    def __init__(self) -> None:
        self.inscriptions: dict[str, _FakeInscription] = {}
        self.tbk_users: dict[str, dict] = {}
        self.transactions: dict[str, dict] = {}
        self.refunds: list[dict] = []
        self.deleted: list[str] = []
        self.revoked: set[str] = set()   # inscripciones eliminadas en «Transbank» (→ código -96)
        self._forced: list[object] = []   # códigos de respuesta o excepciones a devolver

    # --- controles de prueba ---
    def queue_authorization(self, *outcomes: object) -> None:
        """Encola resultados para los próximos `authorize`: 0 (OK), código <0 o una excepción."""
        self._forced.extend(outcomes)

    def set_inscription_outcome(self, token: str, outcome: str, card: str = "visa") -> None:
        ins = self.inscriptions.get(token)
        if ins is None:
            username = self.username_from_token(token)
            if username is None:
                raise GatewayError("Token de inscripción inválido", 422)
            ins = self.inscriptions[token] = _FakeInscription(username, "", self.response_url())
        ins.outcome = outcome
        ins.card = card  # type: ignore[attr-defined]

    DECISIONS = {"approve_visa": ("approved", "visa"), "approve_mastercard": ("approved", "mastercard"),
                 "reject": ("rejected", "visa"), "abort": ("aborted", "visa")}

    def apply_decision(self, token: str, decision: str) -> None:
        """Aplica lo elegido en la página del simulador (llega en la URL de retorno)."""
        outcome, card = self.DECISIONS.get(decision, ("rejected", "visa"))
        self.set_inscription_outcome(token, outcome, card)

    # --- token firmado: "fk" + uuid del usuario (32 hex) + nonce (8 hex) + firma (22 hex) = 64 ---
    @staticmethod
    def _sign(body: str) -> str:
        return hmac.new(get_settings().jwt_secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:22]

    @classmethod
    def _make_token(cls, username: str) -> str:
        body = "fk" + username.removeprefix("u")[:32].ljust(32, "0") + secrets.token_hex(4)
        return body + cls._sign(body)

    @classmethod
    def username_from_token(cls, token: str) -> str | None:
        if len(token) != 64 or not token.startswith("fk"):
            return None
        body, sig = token[:42], token[42:]
        if not hmac.compare_digest(sig, cls._sign(body)):
            return None
        return "u" + body[2:34]

    @staticmethod
    def response_url() -> str:
        return get_settings().public_base_url.rstrip("/") + "/api/v1/billing/oneclick/return"

    def revoke(self, tbk_user: str) -> None:
        """Simula que la inscripción dejó de existir en Transbank."""
        self.revoked.add(tbk_user)

    def reset(self) -> None:
        self.__init__()

    # --- API ---
    async def start_inscription(self, username: str, email: str, response_url: str) -> InscriptionStart:
        token = self._make_token(username)   # 64 caracteres, como Transbank
        self.inscriptions[token] = _FakeInscription(username, email, response_url)
        base = get_settings().public_base_url.rstrip("/")
        return InscriptionStart(token=token, url_webpay=f"{base}/api/v1/billing/fake-webpay")

    async def finish_inscription(self, token: str) -> InscriptionResult:
        ins = self.inscriptions.get(token)
        if ins is None:
            if self.username_from_token(token) is None:
                raise GatewayError("Token de inscripción inválido", 422)
            # Token válido sin decisión: el formulario expiró sin que el usuario eligiera
            raise GatewayError("El formulario de inscripción expiró", 422)
        if ins.outcome in ("aborted", "pending"):
            raise GatewayError("La inscripción fue anulada por el usuario", 422)
        if ins.outcome != "approved":
            return InscriptionResult(response_code=-1, tbk_user=None, authorization_code=None,
                                     card_type=None, card_last4=None, raw={"response_code": -1})
        card = getattr(ins, "card", "visa")
        tbk_user = f"tbkuser-{secrets.token_hex(12)}-{card}"   # ≤ 40 caracteres, como Transbank
        last4, ctype = ("0568", "Mastercard") if card == "mastercard" else ("6623", "Visa")
        self.tbk_users[tbk_user] = {"username": ins.username, "card": card}
        return InscriptionResult(response_code=0, tbk_user=tbk_user, authorization_code="1213",
                                 card_type=ctype, card_last4=last4,
                                 raw={"response_code": 0, "authorization_code": "1213",
                                      "card_type": ctype, "card_number": "XXXXXXXXXXXX" + last4})

    async def delete_inscription(self, tbk_user: str, username: str) -> None:
        self.tbk_users.pop(tbk_user, None)
        self.revoked.add(tbk_user)
        self.deleted.append(tbk_user)

    async def authorize(self, username: str, tbk_user: str, buy_order: str, child_commerce_code: str,
                        child_buy_order: str, amount: int) -> AuthorizationResult:
        if self._forced:
            forced = self._forced.pop(0)
            if isinstance(forced, BaseException):
                raise forced
            code = int(forced)  # type: ignore[arg-type]
        elif tbk_user in self.revoked:
            code = -96
        else:
            info = self.tbk_users.get(tbk_user)
            if info is None and tbk_user.startswith("tbkuser-"):
                # Sin estado compartido: el sufijo del tbk_user indica la tarjeta. Así el job de
                # facturación (otro proceso) y los datos demo obtienen el mismo resultado que la API.
                info = self.tbk_users[tbk_user] = {
                    "username": username, "card": "mastercard" if tbk_user.endswith("-mastercard") else "visa"}
            code = -96 if info is None else (-1 if info["card"] == "mastercard" else 0)
        user = self.tbk_users.get(tbk_user, {})
        body = {
            "buy_order": buy_order,
            "card_detail": {"card_number": "0568" if user.get("card") == "mastercard" else "6623"},
            "accounting_date": datetime.now().strftime("%m%d"),
            "transaction_date": datetime.now().astimezone().isoformat(),
            "details": [{"amount": amount, "status": "AUTHORIZED" if code == 0 else "FAILED",
                         "authorization_code": f"{secrets.randbelow(900000) + 100000}" if code == 0 else None,
                         "payment_type_code": "VN", "response_code": code, "installments_number": 0,
                         "commerce_code": child_commerce_code, "buy_order": child_buy_order}],
        }
        self.transactions[buy_order] = body
        return parse_authorization(body)

    async def status(self, buy_order: str) -> AuthorizationResult:
        if buy_order not in self.transactions:
            raise GatewayNotFound("Transacción no encontrada", 404)
        return parse_authorization(self.transactions[buy_order])

    async def refund(self, buy_order: str, child_commerce_code: str, child_buy_order: str,
                     amount: int) -> RefundResult:
        tx = self.transactions.get(buy_order)
        if tx is None:
            # Cobros de los datos demo (no pasaron por este simulador): se anulan sin validar el monto
            self.refunds.append({"buy_order": buy_order, "amount": amount})
            return RefundResult(type="NULLIFIED", response_code=0, authorization_code="123456",
                                raw={"type": "NULLIFIED", "response_code": 0, "nullified_amount": amount})
        total = tx["details"][0]["amount"]
        self.refunds.append({"buy_order": buy_order, "amount": amount})
        if amount == total and len([r for r in self.refunds if r["buy_order"] == buy_order]) == 1:
            return RefundResult(type="REVERSED", response_code=None, authorization_code=None,
                                raw={"type": "REVERSED"})
        return RefundResult(type="NULLIFIED", response_code=0, authorization_code="123456",
                            raw={"type": "NULLIFIED", "response_code": 0, "authorization_code": "123456",
                                 "nullified_amount": amount})


# ---------------------------------------------------------------- enrutador (Transbank + datos demo)
class RoutingGateway:
    """Transbank para todo lo nuevo; el simulador solo para los medios de pago creados con él.

    Al pasar un ambiente con datos demo (seed) a Transbank, esas tarjetas ficticias no existen
    en Transbank: sin esto, el cron las rechazaría (−96) y dejaría morosas las suscripciones demo.
    · Inscripciones nuevas → Transbank.
    · Tokens/tbk_user del simulador ("fk…", "tbkuser-…") → simulador.
    · Estado/reembolso de un cobro → según `PaymentMethod.provider` (ver BillingService.gw_for).
    """

    name = "transbank_oneclick"

    def __init__(self, primary: PaymentGateway, fake: FakeGateway):
        self.primary, self.fake = primary, fake

    def for_provider(self, provider: str | None) -> PaymentGateway:
        return self.fake if provider == FakeGateway.name else self.primary

    @staticmethod
    def _is_fake_user(tbk_user: str) -> bool:
        return tbk_user.startswith("tbkuser-")

    async def start_inscription(self, username: str, email: str, response_url: str) -> InscriptionStart:
        return await self.primary.start_inscription(username, email, response_url)

    async def finish_inscription(self, token: str) -> InscriptionResult:
        gw = self.fake if FakeGateway.username_from_token(token) else self.primary
        return await gw.finish_inscription(token)

    async def delete_inscription(self, tbk_user: str, username: str) -> None:
        gw = self.fake if self._is_fake_user(tbk_user) else self.primary
        await gw.delete_inscription(tbk_user, username)

    async def authorize(self, username: str, tbk_user: str, buy_order: str, child_commerce_code: str,
                        child_buy_order: str, amount: int) -> AuthorizationResult:
        gw = self.fake if self._is_fake_user(tbk_user) else self.primary
        return await gw.authorize(username, tbk_user, buy_order, child_commerce_code, child_buy_order, amount)

    async def status(self, buy_order: str) -> AuthorizationResult:
        return await self.primary.status(buy_order)

    async def refund(self, buy_order: str, child_commerce_code: str, child_buy_order: str,
                     amount: int) -> RefundResult:
        return await self.primary.refund(buy_order, child_commerce_code, child_buy_order, amount)


# ---------------------------------------------------------------- fábrica
_gateway: PaymentGateway | None = None


def get_gateway() -> PaymentGateway:
    """Pasarela configurada (singleton por proceso). Dependencia FastAPI sobreescribible en tests."""
    global _gateway
    if _gateway is None:
        s = get_settings()
        if s.billing_gateway == "transbank":
            tbk = TransbankOneclickGateway(s.tbk_base_url, s.tbk_commerce_code, s.tbk_api_secret,
                                           timeout=s.tbk_timeout_seconds)
            # En producción no hay datos demo: Transbank directo, sin enrutador
            _gateway = tbk if s.tbk_environment == "production" else RoutingGateway(tbk, FakeGateway())
        else:
            _gateway = FakeGateway()
    return _gateway


def set_gateway(gateway: PaymentGateway | None) -> None:
    global _gateway
    _gateway = gateway
