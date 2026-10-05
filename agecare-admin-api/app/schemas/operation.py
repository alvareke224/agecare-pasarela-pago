"""Esquemas de contenido, marketplace y moderación."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.enums import (CaregiverStatus, ContentStatus, ContentType, ModerationItemType,
                       ModerationStatus, RejectReason)


# ---- Contenido ----
class ContentItemOut(BaseModel):
    id: UUID
    type: ContentType
    title: str
    body: str
    audio_available: bool
    tags: list[str]
    status: ContentStatus
    publish_at: datetime | None
    published_at: datetime | None
    created_by_name: str | None
    updated_at: datetime


class ContentCreateIn(BaseModel):
    type: ContentType
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=10, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    publish_at: datetime | None = None


class ContentPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    body: str | None = Field(default=None, min_length=10, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=10)
    publish_at: datetime | None = None


# ---- Marketplace ----
class CaregiverOut(BaseModel):
    caregiver_id: UUID
    name: str
    zone: str
    specialties: list[str]
    languages: list[str]
    certifications_count: int
    rating_avg: float | None
    reviews_count: int
    status: CaregiverStatus
    submitted_at: datetime
    reviewed_by_name: str | None
    internal_note: str | None = None


class CaregiverPatchIn(BaseModel):
    status: CaregiverStatus | None = None
    reason: str | None = Field(default=None, min_length=10, max_length=1000)
    internal_note: str | None = Field(default=None, max_length=2000)


class ProductOut(BaseModel):
    id: UUID
    name: str
    category: str
    vendor: str
    price_clp: int | None
    external_url: str
    image_url: str | None
    status: ContentStatus
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductCreateIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    category: str = Field(min_length=2, max_length=60)
    vendor: str = Field(min_length=2, max_length=120)
    price_clp: int | None = Field(default=None, ge=0)
    external_url: HttpUrl
    image_url: HttpUrl | None = None

    @field_validator("external_url")
    @classmethod
    def https_only(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme != "https":
            raise ValueError("El enlace externo debe usar https.")
        return v


class ProductPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    category: str | None = Field(default=None, min_length=2, max_length=60)
    vendor: str | None = Field(default=None, min_length=2, max_length=120)
    price_clp: int | None = Field(default=None, ge=0)
    external_url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    status: ContentStatus | None = None


# ---- Moderación ----
class ModerationItemOut(BaseModel):
    id: UUID
    type: ModerationItemType
    content: dict
    author: dict
    reported_by: dict | None
    report_reason: str | None
    status: ModerationStatus
    decided_by_name: str | None
    decided_at: datetime | None
    created_at: datetime


class ApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class RejectIn(BaseModel):
    reason_code: RejectReason
    note: str | None = Field(default=None, max_length=1000)
