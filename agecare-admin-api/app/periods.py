"""Resolución de PeriodKey a rangos de fechas en la zona horaria de negocio."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.enums import PeriodKey

MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


@dataclass
class Period:
    key: PeriodKey
    start: date  # inclusive
    end: date    # inclusive
    granularity: str  # hour | day | week | month

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Period":
        """Periodo anterior equivalente (mismo largo, inmediatamente anterior)."""
        length = self.days
        return Period(self.key, self.start - timedelta(days=length),
                      self.start - timedelta(days=1), self.granularity)


def business_today() -> date:
    tz = ZoneInfo(get_settings().business_timezone)
    return datetime.now(tz).date()


def resolve(key: PeriodKey, today: date | None = None) -> Period:
    t = today or business_today()
    if key == PeriodKey.today:
        return Period(key, t, t, "hour")
    if key == PeriodKey.current_week:
        return Period(key, t - timedelta(days=t.weekday()), t, "day")
    if key == PeriodKey.last_7_days:
        return Period(key, t - timedelta(days=6), t, "day")
    if key == PeriodKey.current_month:
        return Period(key, t.replace(day=1), t, "week")
    if key == PeriodKey.last_30_days:
        return Period(key, t - timedelta(days=29), t, "week")
    if key == PeriodKey.ytd:
        return Period(key, t.replace(month=1, day=1), t, "month")
    # last_12_months
    start_month = (t.replace(day=1) - timedelta(days=365)).replace(day=1)
    return Period(key, start_month, t, "month")


def buckets_for(period: Period) -> list[tuple[date, date, str]]:
    """Divide el periodo en buckets (start, end, label) según su granularidad diaria+.
    La granularidad `hour` no pasa por aquí (usa metrics_hourly_users)."""
    out: list[tuple[date, date, str]] = []
    if period.granularity == "day":
        d = period.start
        while d <= period.end:
            out.append((d, d, DAYS_ES[d.weekday()]))
            d += timedelta(days=1)
    elif period.granularity == "week":
        d = period.start
        while d <= period.end:
            end = min(d + timedelta(days=6), period.end)
            label = f"{d.day}–{end.day} {MONTHS_ES[end.month - 1].lower()}"
            out.append((d, end, label))
            d = end + timedelta(days=1)
    else:  # month
        d = period.start.replace(day=1)
        while d <= period.end:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = min(nxt - timedelta(days=1), period.end)
            out.append((max(d, period.start), end, MONTHS_ES[d.month - 1]))
            d = nxt
    return out
