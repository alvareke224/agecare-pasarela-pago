"""Configuración de la API de administración de AgeCare."""
import os
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Acepta las URLs que entregan Neon / Vercel Postgres / Supabase (postgres://…?sslmode=require)
    y las convierte al formato de SQLAlchemy + asyncpg (postgresql+asyncpg://…?ssl=require)."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+asyncpg://"):
        return url
    parts = urlsplit(url)
    query = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k == "sslmode":
            query.append(("ssl", "require" if v in ("require", "verify-ca", "verify-full") else v))
        elif k in ("channel_binding", "options", "pgbouncer", "supa"):
            continue   # parámetros de libpq que asyncpg no entiende
        else:
            query.append((k, v))
    return urlunsplit(parts._replace(query=urlencode(query)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADMIN_", extra="ignore")

    app_name: str = "AgeCare Admin API"
    environment: str = "dev"  # dev | staging | prod
    database_url: str = Field(default="", validate_default=True)
    # Serverless (Vercel): sin pool persistente entre invocaciones. Se activa solo en Vercel.
    db_nullpool: bool = bool(os.environ.get("VERCEL"))

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db(cls, v: str) -> str:
        # En Vercel la integración de Postgres expone POSTGRES_URL / DATABASE_URL sin prefijo ADMIN_
        v = (v or os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
             or "postgresql+asyncpg://agecare:agecare@localhost:5432/agecare_admin")
        return normalize_database_url(v)

    # JWT
    jwt_secret: str = "cambia-esto-en-produccion"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_hours: int = 12

    # Login
    max_login_attempts: int = 5
    lockout_minutes: int = 15
    allowed_email_domains: str = "wellq.co.uk,wellq.co"  # separados por coma

    # Orígenes del frontend, separados por coma; y una regex opcional para las URLs de
    # previsualización de Vercel, p. ej. https://agecare-admin-web(-[a-z0-9-]+)?\.vercel\.app
    cors_origins: str = "http://localhost:5173"
    cors_origin_regex: str | None = None

    # Secreto del cron de Vercel (variable CRON_SECRET, sin prefijo: así la envía Vercel)
    cron_secret: str = os.environ.get("CRON_SECRET", "")

    # Negocio
    business_timezone: str = "America/Santiago"

    # ---- Pasarela de pago y suscripciones (app/billing) ----
    # fake = simulador local de Webpay (dev/tests) · transbank = Oneclick Mall real
    billing_gateway: str = "fake"
    billing_currency: str = "CLP"
    # Llave Fernet para cifrar tbk_user (Key Vault en Azure). Obligatoria fuera de dev.
    billing_encryption_key: str = ""
    # URL pública de esta API (Transbank redirige aquí tras la inscripción)
    public_base_url: str = "http://localhost:8000"
    # Adónde se envía al usuario tras inscribir la tarjeta (web o deep link de la app)
    billing_return_url: str = "http://localhost:5173/checkout/resultado"
    billing_incomplete_ttl_hours: int = 24      # suscripción sin pagar → expired
    billing_reconcile_after_seconds: int = 120  # cobros pending/unknown a conciliar
    # Transbank Oneclick Mall. Valores por defecto = ambiente de integración público.
    tbk_environment: str = "integration"  # integration | production
    tbk_commerce_code: str = "597055555541"        # código del mall
    tbk_child_commerce_code: str = "597055555542"  # tienda hija que cobra las suscripciones
    tbk_api_secret: str = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"
    tbk_timeout_seconds: float = 30.0

    # JWT de usuarios de la app (emitido por la API principal de AgeCare)
    app_jwt_secret: str = "cambia-esto-jwt-app"  # HS256: secreto compartido · RS256: llave pública PEM
    app_jwt_algorithm: str = "HS256"
    app_jwt_audience: str | None = None

    @property
    def tbk_base_url(self) -> str:
        return ("https://webpay3g.transbank.cl" if self.tbk_environment == "production"
                else "https://webpay3gint.transbank.cl")

    def validate_billing(self) -> None:
        """Evita desplegar a producción con credenciales de integración o sin cifrado."""
        if self.environment != "prod":
            return
        problems = []
        if self.billing_gateway != "transbank":
            problems.append("ADMIN_BILLING_GATEWAY debe ser 'transbank'")
        if self.tbk_environment != "production":
            problems.append("ADMIN_TBK_ENVIRONMENT debe ser 'production'")
        if self.tbk_commerce_code.startswith("5970555555"):
            problems.append("ADMIN_TBK_COMMERCE_CODE es el de integración")
        if not self.billing_encryption_key:
            problems.append("falta ADMIN_BILLING_ENCRYPTION_KEY")
        if problems:
            raise RuntimeError("Configuración de pagos inválida para producción: " + "; ".join(problems))

    @property
    def allowed_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
