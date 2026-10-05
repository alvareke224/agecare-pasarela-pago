"""Sección 6 (parte analítica) — Estadísticas por perfil."""
from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models
from app.deps import Db, require
from app.enums import AppRole
from app.errors import invalid
from app.schemas.metrics import (RoleSummaryRow, RolesSummaryOut, WeeklyActiveOut,
                                 WeeklySeries)
from app.security import now_utc

router = APIRouter(prefix="/metrics/roles", tags=["Perfiles"], dependencies=[require("metrics")])
ROLE_ORDER = [AppRole.family, AppRole.caregiver, AppRole.elder, AppRole.doctor]


# ---------- 6.1 Summary ----------
@router.get("/summary", response_model=RolesSummaryOut)
async def roles_summary(db: Db, days: int = Query(default=30, ge=7, le=90)):
    if days not in (7, 30, 90):
        # normalizamos a la ventana precalculada más cercana
        days = min((7, 30, 90), key=lambda w: abs(w - days))
    rows = (await db.execute(select(models.RoleActivityWindow)
                             .where(models.RoleActivityWindow.days_window == days))).scalars().all()
    if not rows:
        raise invalid("INVALID_RANGE", "La ventana debe estar entre 7 y 90 días.")
    rows.sort(key=lambda r: ROLE_ORDER.index(AppRole(r.role)))
    return RolesSummaryOut(
        days=days,
        roles=[RoleSummaryRow(role=AppRole(r.role), active_users=r.active_users,
                              growth_8w=r.growth_8w, sessions_per_week=r.sessions_per_week,
                              avg_session_seconds=r.avg_session_seconds,
                              retention_30d=r.retention_30d) for r in rows],
        computed_at=max(r.computed_at for r in rows))


# ---------- 6.2 Weekly active ----------
@router.get("/weekly-active", response_model=WeeklyActiveOut)
async def weekly_active(db: Db, weeks: int = Query(default=8)):
    if not 2 <= weeks <= 26:
        raise invalid("INVALID_RANGE", "El número de semanas debe estar entre 2 y 26.")
    week_starts = (await db.execute(
        select(models.RoleWeeklyActive.week_start).distinct()
        .order_by(models.RoleWeeklyActive.week_start.desc()).limit(weeks))).scalars().all()
    week_starts = sorted(week_starts)
    rows = (await db.execute(select(models.RoleWeeklyActive)
                             .where(models.RoleWeeklyActive.week_start.in_(week_starts)))).scalars().all()
    by = {(r.week_start, r.role): r.active_users for r in rows}
    labels = [f"S{w.isocalendar()[1]}" for w in week_starts]
    series = [WeeklySeries(role=role, values=[by.get((w, role.value), 0) for w in week_starts])
              for role in ROLE_ORDER]
    return WeeklyActiveOut(weeks=labels, series=series,
                           computed_at=max((r.computed_at for r in rows), default=now_utc()))
