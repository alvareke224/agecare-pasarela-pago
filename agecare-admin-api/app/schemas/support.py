from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.enums import (AppRole, PlanCode, TicketCategory, TicketChannel, TicketPriority,
                       TicketStatus)


class SupportDeltas(BaseModel):
    open_wow: int
    resolved_mom_pct: float | None
    first_response_mom_hours: float | None


class SupportSummaryOut(BaseModel):
    open: int
    in_progress: int
    waiting_user: int
    resolved_30d: int
    first_response_hours_avg: float
    csat_avg: float | None
    csat_count: int
    deltas: SupportDeltas
    computed_at: datetime


class CategoryRow(BaseModel):
    category: TicketCategory
    name: str
    count: int
    share: float


class ByCategoryOut(BaseModel):
    days: int
    total: int
    categories: list[CategoryRow]


class Requester(BaseModel):
    user_id: UUID | None
    name: str
    role: AppRole | None
    email: EmailStr


class Assignee(BaseModel):
    admin_id: UUID
    name: str


class TicketOut(BaseModel):
    id: UUID
    number: int
    subject: str
    requester: Requester
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    assigned_to: Assignee | None
    channel: TicketChannel
    created_at: datetime
    updated_at: datetime
    first_response_at: datetime | None


class Csat(BaseModel):
    score: int
    comment: str | None


class TicketDetailOut(TicketOut):
    description: str
    requester_context: dict | None
    replies_count: int
    csat: Csat | None


class TicketCreateIn(BaseModel):
    subject: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=1, max_length=8000)
    user_email: EmailStr
    category: TicketCategory
    priority: TicketPriority = TicketPriority.medium
    channel: TicketChannel = TicketChannel.console
    confirm_unlinked: bool = False


class TicketPatchIn(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category: TicketCategory | None = None
    assigned_to: UUID | None = None
    # distinguir "no enviado" de "null para desasignar":
    model_config = {"json_schema_extra": {"note": "assigned_to=null desasigna"}}


class ReplyCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    internal: bool = False


class ReplyOut(BaseModel):
    id: UUID
    ticket_id: UUID
    author_type: str
    author_name: str
    body: str
    internal: bool
    created_at: datetime

    model_config = {"from_attributes": True}
