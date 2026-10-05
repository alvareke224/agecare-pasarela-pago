"""Formato estándar de error de la consola (sección 2.4 de la especificación).

Todo error responde:
    {"error": {"code": "...", "message": "...", "details": [...] | null, "request_id": "..."}}
"""
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


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
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response


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
        return JSONResponse(status_code=500, content=_body(
            request, "INTERNAL_ERROR", "Error interno. Revisa el request_id en los logs."))
