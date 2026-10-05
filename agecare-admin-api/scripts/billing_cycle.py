"""Job de facturación: conciliación, renovaciones, reintentos y expiración.

Uso:
    python -m scripts.billing_cycle

En Azure se programa cada 15 minutos (Container Apps Job con cron "*/15 * * * *")
usando el rol de BD agecare_admin_jobs. Sale con código 1 si hubo errores para
que el orquestador lo marque como fallido y alerte (Application Insights).
"""
import asyncio
import json
import logging
import sys

from app.billing import models as billing_models  # noqa: F401
from app.billing.gateway import get_gateway
from app.billing.jobs import run_billing_cycle
from app.database import get_session_factory


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    async with get_session_factory()() as db:
        stats = await run_billing_cycle(db, get_gateway())
        await db.commit()
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
