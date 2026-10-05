# AgeCare · Consola de administración — Pasarela de pago y suscripciones

Módulo de **pagos y suscripciones** de la consola de administración de AgeCare: cobro mensual
de los planes Dorado, Platino y Proveedor con **Transbank Oneclick Mall**, y su gestión
desde la consola (MRR, mora, reintentos, cancelaciones, extensiones y reembolsos).

| Carpeta | Contenido |
|---|---|
| `agecare-admin-api/` | API FastAPI + PostgreSQL (incluye el resto de la API de la consola). Módulo nuevo en `app/billing`. |
| `agecare-admin-web/` | Consola web React + Vite + TypeScript (módulo Facturación y checkout de prueba). |
| `docker-compose.yml` | Todo el proyecto en Docker: BD, API, job de facturación y web. |
| `DEPLOY_VERCEL.md` | Guía paso a paso para publicar en Vercel. |
| `.github/workflows/` | CI: tests de la API, pruebas SQL del DDL y build de la web. |

## Empezar

```bash
docker compose up --build
# Consola  http://localhost:8080   admin@wellq.co.uk / Admin123!
# Swagger  http://localhost:8080/api/v1/admin/docs
```

Sin Docker: ver `agecare-admin-api/README.md` y `agecare-admin-web/README.md`.

## Documentación

- **Diseño del módulo de pagos** (flujos, estados, endpoints, errores, configuración, paso a
  producción con Transbank): `agecare-admin-api/docs/BILLING.md`
- **Modelo de datos**: `agecare-admin-api/db/` — DDL físico (`agecare_admin_ddl.sql` +
  `agecare_billing_ddl.sql`), pruebas SQL y DBML (importable en dbdiagram.io).
- **Despliegue**: `DEPLOY_VERCEL.md`

## Pruebas

```bash
cd agecare-admin-api && pip install -r requirements-dev.txt && pytest     # 65 tests
psql -f db/agecare_admin_ddl.sql -f db/agecare_billing_ddl.sql            # en una BD vacía
psql -f db/agecare_billing_ddl_tests.sql                                  # 25 pruebas SQL
cd ../agecare-admin-web && npm ci && npm run build
```
