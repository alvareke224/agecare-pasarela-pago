from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import ComponentKey, ComponentStatus, IncidentStatus


class ComponentOut(BaseModel):
    key: ComponentKey
    name: str
    status: ComponentStatus
    uptime_30d: float
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    note: str | None


class StatusOut(BaseModel):
    overall: ComponentStatus
    checked_at: datetime
    components: list[ComponentOut]


class LatencyRow(BaseModel):
    key: ComponentKey
    name: str
    p50_ms: int
    p95_ms: int
    sample_count: int


class LatencyOut(BaseModel):
    window: str
    components: list[LatencyRow]
    computed_at: datetime


class CriticalProcessOut(BaseModel):
    key: str
    name: str
    chain: str
    p95_seconds: float
    success_24h: float
    status: ComponentStatus


class CriticalProcessesOut(BaseModel):
    processes: list[CriticalProcessOut]
    computed_at: datetime


class IncidentOut(BaseModel):
    id: UUID
    title: str
    component_key: ComponentKey | None
    severity: str
    status: IncidentStatus
    description: str
    resolution: str | None
    is_maintenance: bool
    started_at: datetime
    resolved_at: datetime | None
    created_by: UUID | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentCreateIn(BaseModel):
    title: str = Field(min_length=5, max_length=160)
    component_key: ComponentKey | None = None
    severity: str = Field(pattern="^(degraded|outage)$")
    description: str = Field(min_length=1, max_length=4000)
    started_at: datetime
    is_maintenance: bool = False


class IncidentPatchIn(BaseModel):
    status: IncidentStatus | None = None
    resolution: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=4000)
