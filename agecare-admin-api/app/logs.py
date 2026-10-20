"""Engine de logs estructurados de la API (JSON, una línea por evento).

Cada línea es un objeto JSON con campos fijos más los propios del evento:

    {"ts": "2026-10-18T03:48:47.684Z", "level": "INFO", "logger": "agecare.http",
     "event": "http.request", "msg": "POST /api/v1/billing/subscriptions → 201",
     "service": "agecare-admin-api", "env": "staging", "request_id": "9f2c41d0a1b2c3d4",
     "method": "POST", "path": "/api/v1/billing/subscriptions", "status": 201, "duration_ms": 412.7}

· `request_id` se toma de la cabecera X-Request-Id (o se genera) y se agrega solo a todas las
  líneas emitidas durante esa petición (contextvars), también dentro del servicio de facturación.
· Los datos sensibles nunca salen: claves como `tbk_user`, `token`, `password`, `authorization`
  o `card_number` se reemplazan por "***", y los tokens de Webpay en rutas se acortan.
· Salida: stdout. Vercel, Docker y Azure (Container Apps / Log Analytics) la recogen tal cual.
· Configuración: ADMIN_LOG_LEVEL (INFO) y ADMIN_LOG_FORMAT (json | text, para leer en local).
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

SERVICE = "agecare-admin-api"

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("actor", default=None)

SENSITIVE_KEYS = {"tbk_user", "tbk_user_enc", "token", "tbk_token", "password", "password_hash",
                  "authorization", "api_secret", "tbk_api_secret", "card_number", "cvv", "secret",
                  "access_token", "refresh_token", "mfa_secret"}
# Atributos estándar de LogRecord: no se copian como campos del evento
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_-]{40,}")


def redact(value: Any, key: str | None = None) -> Any:
    """Enmascara recursivamente los valores sensibles por nombre de clave."""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return "***"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def safe_path(path: str) -> str:
    """Acorta tokens largos en rutas (p. ej. PUT /inscriptions/{token})."""
    return _LONG_TOKEN.sub(lambda m: m.group(0)[:6] + "…", path)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class JsonFormatter(logging.Formatter):
    def __init__(self, environment: str = "dev"):
        super().__init__()
        self.environment = environment

    def build(self, record: logging.LogRecord) -> dict:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        out: dict[str, Any] = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or "log",
            "msg": record.getMessage(),
            "service": SERVICE,
            "env": self.environment,
        }
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        actor = actor_var.get()
        if actor:
            out["actor"] = actor
        for k, v in record.__dict__.items():
            if k not in _RESERVED and k != "event" and not k.startswith("_"):
                out[k] = _jsonable(redact(v, k))
        if record.exc_info and record.exc_info[0] is not None:
            out["error"] = {"type": record.exc_info[0].__name__, "message": str(record.exc_info[1])[:500],
                            "stack": "".join(traceback.format_exception(*record.exc_info))[-4000:]}
        return out

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.build(record), ensure_ascii=False)


class TextFormatter(JsonFormatter):
    """Formato legible para desarrollo local: hora nivel evento mensaje {campos}."""

    def format(self, record: logging.LogRecord) -> str:
        d = self.build(record)
        head = f"{d.pop('ts')[11:23]} {d.pop('level'):<7} {d.pop('event'):<28} {d.pop('msg')}"
        for k in ("logger", "service", "env"):
            d.pop(k, None)
        err = d.pop("error", None)
        line = head + (f"  {json.dumps(d, ensure_ascii=False)}" if d else "")
        return line + (f"\n{err['stack']}" if err else "")


_configured = False


def setup_logging(level: str = "INFO", fmt: str = "json", environment: str = "dev") -> None:
    """Configura el logger raíz una sola vez (idempotente)."""
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(TextFormatter(environment) if fmt == "text" else JsonFormatter(environment))
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn: sus líneas pasan por el mismo formato; su access log se reemplaza por http.request
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def log_event(logger: logging.Logger, event: str, msg: str, level: int = logging.INFO, **fields) -> None:
    """Emite un evento con nombre estable (`event`) y campos propios."""
    logger.log(level, msg, extra={"event": event, **{k: v for k, v in fields.items() if v is not None}})
