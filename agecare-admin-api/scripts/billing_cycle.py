"""Job de facturación: conciliación, renovaciones, reintentos y expiración.

Uso:
    python -m scripts.billing_cycle

En Azure se programa cada 15 minutos (Container Apps Job con cron "*/15 * * * *")
usando el rol de BD agecare_admin_jobs. Sale con código 1 si hubo errores para
que el orquestador lo marque como fallido y alerte (Application Insights).
"""
import asyncio
import json
import sys

from app.billing import models as billing_models  # noqa: F401
from app.billing.gateway import get_gateway
from app.billing.jobs import run_billing_cycle
from app.config import get_settings
from app.database import get_session_factory
from app.logs import setup_logging


async def main() -> int:
    s = get_settings()
    setup_logging(s.log_level, s.log_format, s.environment)
    async with get_session_factory()() as db:
        stats = await run_billing_cycle(db, get_gateway())
        await db.commit()
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
