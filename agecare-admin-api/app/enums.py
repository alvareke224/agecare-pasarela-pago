"""Enumeraciones de la consola (sección 2.7 de la especificación)."""
from enum import StrEnum


class AdminRole(StrEnum):
    admin = "admin"
    analyst = "analyst"
    support = "support"
    editor = "editor"
    moderator = "moderator"


class PeriodKey(StrEnum):
    today = "today"
    current_week = "current_week"
    last_7_days = "last_7_days"
    current_month = "current_month"
    last_30_days = "last_30_days"
    ytd = "ytd"
    last_12_months = "last_12_months"


class PlanCode(StrEnum):
    free = "free"
    gold = "gold"
    platinum = "platinum"
    provider = "provider"


class ComponentKey(StrEnum):
    api_core = "api_core"
    database = "database"
    auth = "auth"
    push = "push"
    alert_engine = "alert_engine"
    wearable_ingest = "wearable_ingest"
    ai_assistant = "ai_assistant"
    storage = "storage"
    music_sync = "music_sync"


class ComponentStatus(StrEnum):
    operational = "operational"
    degraded = "degraded"
    outage = "outage"


class IncidentStatus(StrEnum):
    investigating = "investigating"
    observing = "observing"
    resolved = "resolved"
    completed = "completed"


class AppRole(StrEnum):
    family = "family"
    caregiver = "caregiver"
    elder = "elder"
    doctor = "doctor"


class TicketStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    waiting_user = "waiting_user"
    resolved = "resolved"
    closed = "closed"


class TicketPriority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TicketCategory(StrEnum):
    account_access = "account_access"
    wearable_sync = "wearable_sync"
    alerts_push = "alerts_push"
    billing_plans = "billing_plans"
    medications = "medications"
    other = "other"


class TicketChannel(StrEnum):
    app = "app"
    email = "email"
    phone = "phone"
    console = "console"


class ContentType(StrEnum):
    joke = "joke"
    news = "news"


class ContentStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class CaregiverStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    suspended = "suspended"


class ModerationItemType(StrEnum):
    review = "review"
    caregiver_profile = "caregiver_profile"
    photo = "photo"
    chat_message = "chat_message"


class ModerationStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class RejectReason(StrEnum):
    offensive = "offensive"
    spam = "spam"
    privacy = "privacy"
    off_topic = "off_topic"
    other = "other"


class LegalDocType(StrEnum):
    terms = "terms"
    privacy = "privacy"


# Nombres visibles en español (para respuestas de la consola)
PLAN_NAMES = {PlanCode.free: "Gratuito", PlanCode.gold: "Dorado",
              PlanCode.platinum: "Platino", PlanCode.provider: "Proveedor"}
ROLE_NAMES = {AppRole.family: "Familiar", AppRole.caregiver: "Cuidadora",
              AppRole.elder: "Adulto mayor", AppRole.doctor: "Médico"}
CATEGORY_NAMES = {
    TicketCategory.account_access: "Acceso y cuenta",
    TicketCategory.wearable_sync: "Wearable y sincronización",
    TicketCategory.alerts_push: "Alertas y push",
    TicketCategory.billing_plans: "Pagos y planes",
    TicketCategory.medications: "Medicamentos",
    TicketCategory.other: "Otros",
}
COMPONENT_NAMES = {
    ComponentKey.api_core: "API central", ComponentKey.database: "Base de datos",
    ComponentKey.auth: "Autenticación", ComponentKey.push: "Notificaciones push",
    ComponentKey.alert_engine: "Motor de alertas", ComponentKey.wearable_ingest: "Ingesta wearables",
    ComponentKey.ai_assistant: "Asistente IA", ComponentKey.storage: "Almacenamiento",
    ComponentKey.music_sync: "Sync Director Musical",
}

# Matriz de permisos por módulo (sección 2.3): módulo -> roles con acceso de lectura/escritura
READ = {"metrics": {AdminRole.admin, AdminRole.analyst, AdminRole.support},
        "ops": {AdminRole.admin, AdminRole.analyst, AdminRole.support},
        "support": {AdminRole.admin, AdminRole.analyst, AdminRole.support},
        "content": {AdminRole.admin, AdminRole.editor},
        "marketplace": {AdminRole.admin, AdminRole.editor, AdminRole.moderator},
        "moderation": {AdminRole.admin, AdminRole.moderator},
        "settings": {AdminRole.admin},
        "legal": {AdminRole.admin},
        "staff": {AdminRole.admin},
        "audit": {AdminRole.admin},
        "billing": {AdminRole.admin, AdminRole.analyst, AdminRole.support},
        "billing_refunds": {AdminRole.admin}}
WRITE = {"ops": {AdminRole.admin, AdminRole.support},
         "support": {AdminRole.admin, AdminRole.support},
         "content": {AdminRole.admin, AdminRole.editor},
         "marketplace": {AdminRole.admin, AdminRole.editor},
         "moderation": {AdminRole.admin, AdminRole.moderator},
         "settings": {AdminRole.admin},
         "legal": {AdminRole.admin},
         "staff": {AdminRole.admin},
         # Pagos: toda escritura (cancelar, extender, reintentar, reembolsar, contracargos) solo admin
         "billing": {AdminRole.admin},
         "billing_refunds": {AdminRole.admin}}
