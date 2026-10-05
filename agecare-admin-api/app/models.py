"""Modelos SQLAlchemy de la consola de administración.

Convenciones: PK UUID, timestamps con zona horaria, JSON portable
(JSONB en PostgreSQL, JSON en SQLite para los tests).
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

PortableJSON = JSON().with_variant(JSONB(), "postgresql")


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def ts_now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ============ Staff ============
class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[uuid.UUID] = pk()
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # null = pendiente de activación
    role: Mapped[str] = mapped_column(String(20))  # enum AdminRole
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invitation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = ts_now()


class AdminSession(Base):
    """Refresh tokens rotatorios (hash SHA-256)."""
    __tablename__ = "admin_sessions"
    id: Mapped[uuid.UUID] = pk()
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = ts_now()


class AuditLog(Base):
    """Registro inmutable (solo INSERT a nivel de permisos de BD)."""
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    before: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ============ Métricas comerciales (agregadas por jobs) ============
class MetricsDailyUsers(Base):
    __tablename__ = "metrics_daily_users"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    signups: Mapped[int] = mapped_column(Integer, default=0)
    cancellations: Mapped[int] = mapped_column(Integer, default=0)
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    active_users_eod: Mapped[int] = mapped_column(Integer, default=0)   # MAU al cierre del día
    paying_users_eod: Mapped[int] = mapped_column(Integer, default=0)
    mrr_clp_eod: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = ts_now()


class MetricsHourlyUsers(Base):
    """Solo para el periodo `today` (refresco horario)."""
    __tablename__ = "metrics_hourly_users"
    id: Mapped[uuid.UUID] = pk()
    ts_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True)
    signups: Mapped[int] = mapped_column(Integer, default=0)
    cancellations: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = ts_now()


class MetricsPlanSnapshot(Base):
    __tablename__ = "metrics_plan_snapshot"
    id: Mapped[uuid.UUID] = pk()
    as_of: Mapped[date] = mapped_column(Date, index=True)
    plan_code: Mapped[str] = mapped_column(String(12))  # enum PlanCode
    users: Mapped[int] = mapped_column(Integer)
    price_clp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mrr_clp: Mapped[int] = mapped_column(Integer, default=0)
    monthly_churn: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("as_of", "plan_code"),)


# ============ Perfiles y adopción (ventanas precalculadas) ============
class RoleActivityWindow(Base):
    """Resumen por perfil para ventanas de 7/30/90 días (job diario)."""
    __tablename__ = "role_activity_window"
    id: Mapped[uuid.UUID] = pk()
    days_window: Mapped[int] = mapped_column(Integer, index=True)  # 7 | 30 | 90
    role: Mapped[str] = mapped_column(String(12))  # enum AppRole
    active_users: Mapped[int] = mapped_column(Integer)
    growth_8w: Mapped[float] = mapped_column(Float, default=0.0)
    sessions_per_week: Mapped[float] = mapped_column(Float, default=0.0)
    avg_session_seconds: Mapped[int] = mapped_column(Integer, default=0)
    retention_30d: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("days_window", "role"),)


class RoleWeeklyActive(Base):
    __tablename__ = "role_weekly_active"
    id: Mapped[uuid.UUID] = pk()
    week_start: Mapped[date] = mapped_column(Date, index=True)  # lunes ISO
    role: Mapped[str] = mapped_column(String(12))
    active_users: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("week_start", "role"),)


class Feature(Base):
    """Catálogo de funcionalidades (se administra por seed/migración, no por API)."""
    __tablename__ = "features"
    feature_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    applicable_roles: Mapped[list] = mapped_column(PortableJSON)  # list[AppRole]
    expected_low: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class FeatureUsageWindow(Base):
    """Adopción por función/rol/ventana, precalculada por el job diario."""
    __tablename__ = "feature_usage_window"
    id: Mapped[uuid.UUID] = pk()
    days_window: Mapped[int] = mapped_column(Integer, index=True)
    feature_key: Mapped[str] = mapped_column(ForeignKey("features.feature_key"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    users: Mapped[int] = mapped_column(Integer)  # usuarios únicos que usaron la función
    computed_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("days_window", "feature_key", "role"),)


# ============ Estado operativo ============
class ComponentState(Base):
    """Última foto de cada componente (la mantiene el monitor cada 60 s)."""
    __tablename__ = "ops_component_state"
    key: Mapped[str] = mapped_column(String(30), primary_key=True)  # enum ComponentKey
    status: Mapped[str] = mapped_column(String(15))  # enum ComponentStatus
    uptime_30d: Mapped[float] = mapped_column(Float, default=1.0)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checked_at: Mapped[datetime] = ts_now()


class LatencyWindow(Base):
    """Percentiles agregados por ventana (1h/24h/7d), desde ops_request_stats."""
    __tablename__ = "ops_latency_window"
    id: Mapped[uuid.UUID] = pk()
    window: Mapped[str] = mapped_column(String(4), index=True)  # 1h | 24h | 7d
    component_key: Mapped[str] = mapped_column(String(30))
    p50_ms: Mapped[int] = mapped_column(Integer)
    p95_ms: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("window", "component_key"),)


class CriticalProcessState(Base):
    __tablename__ = "ops_critical_process_state"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    chain: Mapped[str] = mapped_column(String(300))
    p95_seconds: Mapped[float] = mapped_column(Float)
    success_24h: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(15))
    computed_at: Mapped[datetime] = ts_now()


class Incident(Base):
    __tablename__ = "ops_incidents"
    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str] = mapped_column(String(160))
    component_key: Mapped[str | None] = mapped_column(String(30), nullable=True)
    severity: Mapped[str] = mapped_column(String(15))  # degraded | outage
    status: Mapped[str] = mapped_column(String(15), index=True)  # enum IncidentStatus
    description: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = ts_now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


# ============ Soporte ============
class Ticket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[uuid.UUID] = pk()
    number: Mapped[int] = mapped_column(Integer, unique=True, index=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    requester_name: Mapped[str] = mapped_column(String(120))
    requester_email: Mapped[str] = mapped_column(String(254), index=True)
    requester_role: Mapped[str | None] = mapped_column(String(12), nullable=True)  # enum AppRole
    requester_plan: Mapped[str | None] = mapped_column(String(12), nullable=True)  # enum PlanCode
    category: Mapped[str] = mapped_column(String(20), index=True)
    priority: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(15), index=True)
    channel: Mapped[str] = mapped_column(String(10), default="app")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    csat_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1–5
    csat_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_context: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    assignee: Mapped[AdminUser | None] = relationship(lazy="joined")
    replies: Mapped[list["TicketReply"]] = relationship(back_populates="ticket", lazy="noload")


class TicketReply(Base):
    __tablename__ = "support_ticket_replies"
    id: Mapped[uuid.UUID] = pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author_type: Mapped[str] = mapped_column(String(10))  # admin | user | system
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    author_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ticket: Mapped[Ticket] = relationship(back_populates="replies")


# ============ Contenido, marketplace y moderación ============
class ContentItem(Base):
    __tablename__ = "content_items"
    id: Mapped[uuid.UUID] = pk()
    type: Mapped[str] = mapped_column(String(10), index=True)  # enum ContentType
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(PortableJSON, default=list)
    status: Mapped[str] = mapped_column(String(12), index=True, default="draft")
    tts_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = ts_now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class CaregiverProfile(Base):
    __tablename__ = "marketplace_caregivers"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    zone: Mapped[str] = mapped_column(String(80), index=True)
    specialties: Mapped[list] = mapped_column(PortableJSON, default=list)
    languages: Mapped[list] = mapped_column(PortableJSON, default=list)
    certifications_count: Mapped[int] = mapped_column(Integer, default=0)
    certifications_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    rating_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), index=True, default="pending")
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = ts_now()
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Product(Base):
    __tablename__ = "marketplace_products"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60), index=True)
    vendor: Mapped[str] = mapped_column(String(120))
    price_clp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_url: Mapped[str] = mapped_column(String(500))
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(12), index=True, default="draft")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class ModerationItem(Base):
    __tablename__ = "moderation_queue"
    id: Mapped[uuid.UUID] = pk()
    type: Mapped[str] = mapped_column(String(20), index=True)  # enum ModerationItemType
    content: Mapped[dict] = mapped_column(PortableJSON)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    author_name: Mapped[str] = mapped_column(String(120))
    author_role: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reported_by: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    report_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_safety: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), index=True, default="pending")
    reject_reason_code: Mapped[str | None] = mapped_column(String(15), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ============ Configuración y legales ============
class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | int | str | None] = mapped_column(PortableJSON)
    value_schema: Mapped[dict] = mapped_column(PortableJSON)  # JSON Schema del valor
    description: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LegalVersion(Base):
    __tablename__ = "legal_versions"
    id: Mapped[uuid.UUID] = pk()
    doc_type: Mapped[str] = mapped_column(String(10), index=True)  # terms | privacy
    semver: Mapped[str] = mapped_column(String(10))
    content_md: Mapped[str] = mapped_column(Text)
    changelog: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[date] = mapped_column(Date)
    requires_reacceptance: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | published
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = ts_now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __table_args__ = (UniqueConstraint("doc_type", "semver"),)
