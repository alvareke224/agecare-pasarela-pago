"""Formato estándar de error de la consola (sección 2.4 de la especificación).

Todo error responde:
    {"error": {"code": "...", "message": "...", "details": [...] | null, "request_id": "..."}}
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.logs import actor_var, log_event, request_id_var, safe_path

http_log = logging.getLogger("agecare.http")


class ApiError(Exception):
    """Error de negocio con código interno estable y mensaje en español."""

    def __init__(self, status_code: int, code: str, message: str, details: list | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


# Atajos para los errores más comunes
def unauthorized(message: str = "Tu sesión de administración expiró. Vuelve a iniciar sesión.",
                 code: str = "UNAUTHORIZED") -> ApiError:
    return ApiError(401, code, message)


def forbidden(message: str = "Tu rol no tiene permisos sobre este módulo.") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def not_found(message: str = "El recurso solicitado no existe.") -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def invalid(code: str, message: str, details: list | None = None) -> ApiError:
    return ApiError(422, code, message, details)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Asigna X-Request-Id y emite un evento `http.request` por petición (método, ruta, estado, ms)."""

    async def dispatch(self, request: Request, call_next):
        incoming = (request.headers.get("X-Request-Id") or "")[:64]
        rid = incoming if incoming.replace("-", "").isalnum() else uuid.uuid4().hex[:16]
        request.state.request_id = rid
        rid_token, actor_token = request_id_var.set(rid), actor_var.set(None)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = rid
            return response
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            path = safe_path(request.url.path)
            if path not in ("/health", "/favicon.ico"):
                level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
                log_event(http_log, "http.request", f"{request.method} {path} → {status}", level,
                          method=request.method, path=path, status=status, duration_ms=ms,
                          actor=getattr(request.state, "actor_ref", None),
                          client_ip=request.client.host if request.client else None)
            request_id_var.reset(rid_token)
            actor_var.reset(actor_token)


def _body(request: Request, code: str, message: str, details: list | None = None) -> dict:
    return {"error": {
        "code": code,
        "message": message,
        "details": details,
        "request_id": getattr(request.state, "request_id", "-"),
    }}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        log_event(http_log, "http.error", f"{exc.code}: {exc.message}",
                  logging.ERROR if exc.status_code >= 500 else logging.INFO,
                  status=exc.status_code, code=exc.code)
        return JSONResponse(status_code=exc.status_code,
                            content=_body(request, exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "issue": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_body(
            request, "VALIDATION_ERROR",
            "Hay datos inválidos en la solicitud. Revisa los campos marcados.", details))

    @app.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception):
        http_log.error("Excepción no controlada en %s %s", request.method, safe_path(request.url.path),
                       exc_info=exc, extra={"event": "http.exception", "code": "INTERNAL_ERROR"})
        return JSONResponse(status_code=500, content=_body(
            request, "INTERNAL_ERROR", "Error interno. Revisa el request_id en los logs."))
