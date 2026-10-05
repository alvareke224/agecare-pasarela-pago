"""AgeCare — API de la Consola de Administración (FastAPI + PostgreSQL)."""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.billing import router_admin as billing_admin
from app.billing import router_app as billing_app
from app.config import get_settings
from app.errors import RequestIdMiddleware, register_error_handlers
from app.routers import (auth, content, marketplace, metrics_commercial, metrics_features,
                         metrics_roles, moderation, ops, support)
from app.routers import system as system_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().validate_billing()   # impide arrancar en prod con credenciales de integración
    yield


app = FastAPI(
    lifespan=lifespan,
    title="AgeCare Admin API",
    version="1.0.0",
    description="Backend de la Consola de Administración de AgeCare · Wellq Co",
    docs_url="/api/v1/admin/docs",
    openapi_url="/api/v1/admin/openapi.json",
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_origin_regex=get_settings().cors_origin_regex,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
                   expose_headers=["X-Request-Id"])
register_error_handlers(app)

api = APIRouter(prefix="/api/v1/admin")
api.include_router(auth.router)
api.include_router(metrics_commercial.router)
api.include_router(ops.router)
api.include_router(metrics_roles.router)
api.include_router(support.router)
api.include_router(metrics_features.router)
api.include_router(content.router)
api.include_router(marketplace.router)
api.include_router(moderation.router)
api.include_router(system_router.router)
api.include_router(billing_admin.router)
app.include_router(api)
# Pasarela de pago para la app (usuarios finales, JWT de la app; no es parte de /admin)
app.include_router(billing_app.router)



@app.get("/", include_in_schema=False)
async def root():
    return {"service": get_settings().app_name, "docs": "/api/v1/admin/docs", "health": "/health"}


@app.get("/health", tags=["Salud"])
async def health():
    return {"status": "ok", "service": get_settings().app_name}
