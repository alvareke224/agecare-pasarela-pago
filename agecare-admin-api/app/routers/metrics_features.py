"""Sección 7 — Uso por funcionalidad."""
from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models
from app.deps import Db, require
from app.enums import AppRole
from app.errors import invalid
from app.schemas.metrics import (AdoptionAlert, AdoptionOut, AlertsOut, FeatureAdoptionRow,
                                 TopByRole, TopFeature, TopOut)
from app.security import now_utc

router = APIRouter(prefix="/metrics/features", tags=["Uso por funcionalidad"],
                   dependencies=[require("metrics")])
ROLE_ORDER = [AppRole.family, AppRole.caregiver, AppRole.elder, AppRole.doctor]


def _check_days(days: int) -> int:
    if days not in (7, 30, 90):
        raise invalid("INVALID_RANGE", "La ventana debe ser 7, 30 o 90 días.")
    return days


async def _adoption_map(db, days: int) -> tuple[list[models.Feature], dict, dict, object]:
    """features ordenadas, uso {(feature, role): users}, activos {role: n}, computed_at."""
    features = (await db.execute(select(models.Feature)
                                 .order_by(models.Feature.sort_order))).scalars().all()
    usage_rows = (await db.execute(select(models.FeatureUsageWindow)
                                   .where(models.FeatureUsageWindow.days_window == days))).scalars().all()
    active_rows = (await db.execute(select(models.RoleActivityWindow)
                                    .where(models.RoleActivityWindow.days_window == days))).scalars().all()
    usage = {(r.feature_key, r.role): r.users for r in usage_rows}
    active = {r.role: r.active_users for r in active_rows}
    computed = max((r.computed_at for r in usage_rows), default=now_utc())
    return features, usage, active, computed


def _rate(usage: dict, active: dict, feature: models.Feature, role: AppRole) -> float | None:
    """None = no aplica al rol; 0.0 = disponible pero sin uso."""
    if role.value not in feature.applicable_roles:
        return None
    denom = active.get(role.value, 0)
    if denom <= 0:
        return 0.0
    return round(min(usage.get((feature.feature_key, role.value), 0) / denom, 1.0), 4)


# ---------- 7.1 Adoption matrix ----------
@router.get("/adoption", response_model=AdoptionOut)
async def adoption(db: Db, days: int = Query(default=30)):
    days = _check_days(days)
    features, usage, active, computed = await _adoption_map(db, days)
    return AdoptionOut(
        days=days, roles=ROLE_ORDER,
        features=[FeatureAdoptionRow(feature_key=f.feature_key, name=f.name,
                                     adoption=[_rate(usage, active, f, r) for r in ROLE_ORDER])
                  for f in features],
        computed_at=computed)


# ---------- 7.2 Top per role ----------
@router.get("/top", response_model=TopOut)
async def top(db: Db, days: int = Query(default=30), limit: int = Query(default=1, ge=1, le=5)):
    days = _check_days(days)
    features, usage, active, computed = await _adoption_map(db, days)
    out = []
    for role in ROLE_ORDER:
        scored = [(f, _rate(usage, active, f, role)) for f in features]
        scored = [(f, s) for f, s in scored if s is not None]
        scored.sort(key=lambda x: x[1], reverse=True)
        out.append(TopByRole(role=role,
                             features=[TopFeature(feature_key=f.feature_key, name=f.name, adoption=s)
                                       for f, s in scored[:limit]]))
    return TopOut(top_by_role=out, computed_at=computed)


# ---------- 7.3 Adoption alerts ----------
@router.get("/alerts", response_model=AlertsOut)
async def alerts(db: Db, days: int = Query(default=30),
                 threshold: float = Query(default=0.15)):
    days = _check_days(days)
    if not 0.01 <= threshold <= 0.5:
        raise invalid("INVALID_THRESHOLD", "El umbral debe estar entre 0.01 y 0.5.")
    features, usage, active, computed = await _adoption_map(db, days)
    result = []
    for f in features:
        low_roles, low_values = [], []
        for role in ROLE_ORDER:
            rate = _rate(usage, active, f, role)
            if rate is not None and rate < threshold:
                low_roles.append(role)
                low_values.append(rate)
        if low_roles:
            result.append(AdoptionAlert(feature_key=f.feature_key, name=f.name,
                                        roles=low_roles, adoption=low_values,
                                        expected_low=f.expected_low, note=f.note))
    # las de emergencia (expected_low) al final
    result.sort(key=lambda a: (a.expected_low, min(a.adoption)))
    return AlertsOut(threshold=threshold, alerts=result, computed_at=computed)
