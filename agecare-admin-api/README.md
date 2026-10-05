# AgeCare — Admin API

Backend de la **Consola de Administración** de AgeCare (Wellq Co).
FastAPI · Python 3.12 · SQLAlchemy 2 async · PostgreSQL 16 · Alembic.

Implementa los 45 endpoints de la *Especificación de Endpoints — Consola de
Administración v1*: autenticación de staff con MFA, las cuatro secciones
analíticas del wireframe (uso comercial, estado operativo, usuarios y soporte,
uso por funcionalidad) y los módulos de operación (tickets, curación de
contenido, catálogos del marketplace, moderación, configuración, legales y
auditoría).

## Pasarela de pago y suscripciones (nuevo)

Módulo `app/billing`: cobro mensual de los planes Dorado, Platino y Proveedor con
**Transbank Oneclick Mall**, más la gestión de suscripciones desde la consola (KPIs, mora,
reintentos, cancelaciones, extensiones y reembolsos). Documentación completa en
**[docs/BILLING.md](docs/BILLING.md)**.

- Endpoints de la app: `/api/v1/billing/*` (JWT del usuario final)
- Endpoints de la consola: `/api/v1/admin/billing/*` (permisos `billing` y `billing_refunds`)
- Job: `python -m scripts.billing_cycle` (cada 15 min) · en Vercel: cron diario (`vercel.json`)
- Despliegue en Vercel: `../DEPLOY_VERCEL.md` · todo el proyecto en Docker: `../docker-compose.yml`
- DDL: `db/agecare_billing_ddl.sql` (+ pruebas `db/agecare_billing_ddl_tests.sql`)
- Por defecto usa un **simulador de Webpay** local; `ADMIN_BILLING_GATEWAY=transbank` usa Transbank.

## Arranque rápido (Docker)

```bash
docker compose up --build
```

Levanta PostgreSQL, aplica las migraciones, siembra datos demo (incluidas 31 suscripciones),
corre el job de facturación cada 15 min y sirve la API en
`http://localhost:8000`. Documentación interactiva (Swagger):
`http://localhost:8000/api/v1/admin/docs`.

## Arranque manual

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                  # ajusta ADMIN_DATABASE_URL si hace falta
alembic upgrade head                  # crea las tablas
python -m scripts.seed                # datos demo del wireframe
uvicorn app.main:app --reload
```

## Credenciales demo (seed)

| Correo | Contraseña | Rol |
|---|---|---|
| admin@wellq.co.uk | Admin123! | admin |
| soporte@wellq.co.uk | Soporte123! | soporte |
| analista@wellq.co.uk | Analista123! | analista |
| editora@wellq.co.uk | Editora123! | editor |

(Sin MFA para facilitar la prueba; en producción el rol admin exige TOTP.)

## Probar con curl

```bash
BASE=http://localhost:8000/api/v1/admin

# Login
TOKEN=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@wellq.co.uk","password":"Admin123!"}' | python3 -c \
  'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# Sección 1 · Uso comercial
curl -s "$BASE/metrics/commercial/summary?period=current_month" -H "$AUTH"
curl -s "$BASE/metrics/commercial/registrations?period=last_7_days" -H "$AUTH"
curl -s "$BASE/metrics/commercial/plans" -H "$AUTH"
curl -s "$BASE/metrics/commercial/funnel" -H "$AUTH"

# Sección 2 · Estado operativo
curl -s "$BASE/ops/status" -H "$AUTH"
curl -s "$BASE/ops/latency?window=1h" -H "$AUTH"
curl -s "$BASE/ops/incidents?days=30" -H "$AUTH"

# Sección 3 · Usuarios y soporte
curl -s "$BASE/metrics/roles/summary" -H "$AUTH"
curl -s "$BASE/metrics/roles/weekly-active?weeks=8" -H "$AUTH"
curl -s "$BASE/support/summary" -H "$AUTH"
curl -s "$BASE/support/tickets?status=open" -H "$AUTH"

# Sección 4 · Uso por funcionalidad
curl -s "$BASE/metrics/features/adoption?days=30" -H "$AUTH"
curl -s "$BASE/metrics/features/alerts?threshold=0.15" -H "$AUTH"
```

## Tests

```bash
pytest            # 65 pruebas: auth/roles, métricas, tickets, settings, auditoría
                  #   + 42 de facturación y despliegue (checkout, dunning, conciliación, reembolsos,
                  #     contrato Transbank, Vercel/serverless)
psql -f db/agecare_billing_ddl_tests.sql   # 25 pruebas SQL del DDL de facturación
```

La suite corre sobre SQLite en memoria (sin PostgreSQL) gracias a tipos
portables; el despliegue real usa PostgreSQL vía `ADMIN_DATABASE_URL`.

## Estructura

```
app/
  config.py        Configuración (pydantic-settings, prefijo ADMIN_)
  database.py      Engine async + sesión por petición
  errors.py        Formato estándar de error {error:{code,message,details,request_id}}
  enums.py         Enumeraciones y matriz de permisos por rol
  security.py      Argon2id, JWT (access 15 min / refresh 12 h rotatorio), TOTP
  deps.py          get_current_admin, require(módulo, write=…)
  audit.py         Escritura de audit_log con enmascarado de campos sensibles
  periods.py       Resolución de PeriodKey en America/Santiago + buckets
  models.py        Tablas: staff, agregadas de métricas, ops, tickets, contenido,
                   marketplace, moderación, settings, legales, audit_log
  schemas/         Esquemas Pydantic por módulo
  routers/         Un router por módulo (prefijo común /api/v1/admin)
  billing/         Pasarela de pago y suscripciones (ver docs/BILLING.md)
    gateway.py       Interfaz PaymentGateway + cliente Transbank Oneclick Mall + simulador
    service.py       Reglas: suscribir, cobrar, dunning, cancelar, cambiar plan, reembolsar
    jobs.py          Ciclo de facturación (conciliación, renovaciones, reintentos, expiración)
    router_app.py    /api/v1/billing (usuario final) · router_admin.py /api/v1/admin/billing
    models.py · schemas.py · enums.py · crypto.py · app_auth.py · periods.py
alembic/           Migraciones 0001 (inicial) y 0002 (facturación)
db/                DDL físico canónico, pruebas SQL y DBML (base + facturación)
scripts/           seed.py (+ seed_billing.py), billing_cycle.py (job), app_token.py (JWT dev)
tests/             pytest + httpx (asyncio)
docs/BILLING.md    Diseño, flujos, endpoints, configuración y paso a producción del módulo de pagos
```

## Decisiones de implementación

- **Estrategia mixta de datos**: los endpoints analíticos leen tablas agregadas
  (`metrics_daily_users`, `metrics_plan_snapshot`, `role_activity_window`,
  `feature_usage_window`, `ops_latency_window`…) que en producción alimentan
  jobs; el seed las puebla directamente. El estado operativo se lee de la foto
  que mantiene el monitor (`ops_component_state`).
- **En la matriz de adopción, `null` ≠ `0.0`**: null = la función no aplica al
  rol (celda gris); 0.0 = disponible pero sin uso.
- **Refresh tokens rotatorios** con detección de reutilización: reusar un token
  rotado revoca todas las sesiones del administrador.
- **Bloqueo optimista** en configuración (`version`) y validación del valor
  contra el JSON Schema de cada parámetro.
- **Auditoría en toda mutación** y en logins (exitosos y fallidos), con
  campos sensibles enmascarados.
- Los puntos donde en producción se integraría correo, push, TTS o webhooks
  están marcados con comentarios en el código.
