from datetime import date, datetime

from pydantic import BaseModel

from app.enums import AppRole, PeriodKey, PlanCode


class PeriodOut(BaseModel):
    key: PeriodKey
    start_date: date
    end_date: date


# ---- 4.1 summary ----
class CommercialDeltas(BaseModel):
    new_users_pct: float | None
    mrr_pct: float | None
    active_users_pct: float | None


class CommercialSummaryOut(BaseModel):
    period: PeriodOut
    downloads: int
    new_users: int
    churned_users: int
    churn_rate: float
    active_users: int
    paying_users: int
    paying_share: float
    mrr_clp: int
    deltas: CommercialDeltas
    computed_at: datetime


# ---- 4.2 registrations ----
class BucketOut(BaseModel):
    label: str
    start: datetime
    end: datetime


class RegistrationsOut(BaseModel):
    period: PeriodOut
    granularity: str
    buckets: list[BucketOut]
    signups: list[int]
    cancellations: list[int]
    computed_at: datetime


# ---- 4.3 plans ----
class PlanRow(BaseModel):
    plan_code: PlanCode
    name: str
    users: int
    share: float
    price_clp: int | None
    mrr_clp: int
    monthly_churn: float


class PlanTotals(BaseModel):
    users: int
    mrr_clp: int
    monthly_churn: float


class PlansOut(BaseModel):
    as_of: date
    plans: list[PlanRow]
    totals: PlanTotals
    computed_at: datetime


# ---- 4.4 funnel ----
class FunnelStage(BaseModel):
    stage: str
    name: str
    users: int
    rate_vs_first: float


class FunnelOut(BaseModel):
    as_of: date
    stages: list[FunnelStage]
    computed_at: datetime


# ---- 6.1 / 6.2 roles ----
class RoleSummaryRow(BaseModel):
    role: AppRole
    active_users: int
    growth_8w: float
    sessions_per_week: float
    avg_session_seconds: int
    retention_30d: float


class RolesSummaryOut(BaseModel):
    days: int
    roles: list[RoleSummaryRow]
    computed_at: datetime


class WeeklySeries(BaseModel):
    role: AppRole
    values: list[int]


class WeeklyActiveOut(BaseModel):
    weeks: list[str]
    series: list[WeeklySeries]
    computed_at: datetime


# ---- 7 features ----
class FeatureAdoptionRow(BaseModel):
    feature_key: str
    name: str
    adoption: list[float | None]


class AdoptionOut(BaseModel):
    days: int
    roles: list[AppRole]
    features: list[FeatureAdoptionRow]
    computed_at: datetime


class TopFeature(BaseModel):
    feature_key: str
    name: str
    adoption: float


class TopByRole(BaseModel):
    role: AppRole
    features: list[TopFeature]


class TopOut(BaseModel):
    top_by_role: list[TopByRole]
    computed_at: datetime


class AdoptionAlert(BaseModel):
    feature_key: str
    name: str
    roles: list[AppRole]
    adoption: list[float]
    expected_low: bool
    note: str | None


class AlertsOut(BaseModel):
    threshold: float
    alerts: list[AdoptionAlert]
    computed_at: datetime
