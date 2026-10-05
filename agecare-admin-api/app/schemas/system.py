"""Esquemas de configuración, legales y auditoría."""
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import LegalDocType


# ---- Configuración ----
class SettingOut(BaseModel):
    key: str
    value: Any
    schema_: dict = Field(alias="schema")
    description: str
    updated_by: dict | None
    updated_at: datetime | None
    version: int

    model_config = {"populate_by_name": True}


class SettingsOut(BaseModel):
    settings: list[SettingOut]


class SettingPutIn(BaseModel):
    value: Any
    version: int
    change_note: str = Field(min_length=5, max_length=500)


# ---- Legales ----
class LegalCurrentOut(BaseModel):
    version_id: UUID
    semver: str
    published_at: datetime | None
    effective_date: date
    requires_reacceptance: bool


class LegalDraftOut(BaseModel):
    version_id: UUID
    semver: str
    created_by_name: str | None
    updated_at: datetime


class LegalDocumentOut(BaseModel):
    doc_type: LegalDocType
    current: LegalCurrentOut | None
    drafts: list[LegalDraftOut]


class LegalDocumentsOut(BaseModel):
    documents: list[LegalDocumentOut]


class LegalVersionCreateIn(BaseModel):
    semver: str = Field(pattern=r"^\d+\.\d+$")
    content_md: str = Field(min_length=100)
    changelog: str = Field(min_length=10, max_length=2000)
    effective_date: date


class LegalVersionOut(BaseModel):
    version_id: UUID
    doc_type: LegalDocType
    semver: str
    effective_date: date
    requires_reacceptance: bool
    status: str
    published_at: datetime | None = None


# ---- Auditoría ----
class AuditEntryOut(BaseModel):
    id: UUID
    actor: dict
    action: str
    entity_type: str | None
    entity_id: UUID | None
    before: dict | None
    after: dict | None
    ip: str | None
    user_agent: str | None
    created_at: datetime
