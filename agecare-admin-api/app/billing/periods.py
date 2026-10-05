"""Aritmética de periodos mensuales anclados a un día del mes."""
import calendar
from datetime import datetime


def add_months(dt: datetime, months: int, anchor_day: int) -> datetime:
    """Suma meses manteniendo el día ancla, acotado al último día del mes.

    31-ene + 1 mes (ancla 31) → 28/29-feb; + 2 meses → 31-mar.
    """
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)
