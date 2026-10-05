# Despliegue en Vercel — AgeCare · Pasarela de pago y suscripciones

Se despliegan **dos proyectos de Vercel** desde el mismo repositorio de GitHub, más una base
de datos PostgreSQL alojada (Neon, desde el Marketplace de Vercel). Tiempo estimado: 20–30 min.

```
GitHub (este repo)
 ├─ agecare-admin-api  ──▶  Proyecto Vercel «agecare-admin-api»  (FastAPI como Vercel Function + cron diario)
 │                              └─ Neon Postgres (Marketplace)
 └─ agecare-admin-web  ──▶  Proyecto Vercel «agecare-admin-web»  (SPA Vite)
```

> El plan Hobby (gratuito) alcanza para las pruebas. Su único límite relevante es que el cron
> corre **una vez al día**. Para renovar o conciliar en cualquier momento, el admin tiene el botón
> **Ejecutar ahora** en *Resumen de facturación*.

---

## 1. Subir el código a GitHub

Sube la carpeta completa (con `agecare-admin-api/`, `agecare-admin-web/`, `.github/` y
`docker-compose.yml` en la raíz) a un repositorio de GitHub, público o privado.

## 2. Proyecto de la API

1. En Vercel: **Add New… → Project → Import** el repositorio.
2. **Root Directory**: `agecare-admin-api`. Vercel detecta FastAPI (`app/main.py`) y Python 3.12
   (`.python-version`). No cambies los comandos de build.
3. Antes de desplegar, pon el nombre `agecare-admin-api`. La URL quedará como
   `https://agecare-admin-api.vercel.app` (o con un sufijo si el nombre está tomado; anótala).
4. **Deploy**. El primer despliegue puede fallar o quedar sin base de datos: es normal, se
   corrige en los pasos siguientes.

### 2.1 Base de datos

1. En el proyecto de la API: pestaña **Storage** (o *Marketplace*) → crear una base **Neon
   (Postgres)** en el plan gratuito, en la región más cercana disponible.
2. **Connect** al proyecto `agecare-admin-api` (entornos Production y Preview). Vercel crea las
   variables `DATABASE_URL` y `POSTGRES_URL`; la API las lee y las adapta sola
   (`postgres://…?sslmode=require` → asyncpg con SSL).
3. Copia el valor de `DATABASE_URL` (Storage → la base → **.env.local** → Show secret).

### 2.2 Crear las tablas y cargar los datos demo (desde tu computador, una vez)

```bash
cd agecare-admin-api
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL="postgres://…copiado de Neon…"       # Windows PowerShell: $env:DATABASE_URL="…"
alembic upgrade head        # crea todas las tablas (incluidas las billing_*)
python -m scripts.seed      # staff demo, métricas del wireframe y 31 suscripciones demo
```

`scripts.seed` **borra y vuelve a crear** los datos demo: no lo corras sobre datos reales.

### 2.3 Variables de entorno de la API

*Settings → Environment Variables* (Production y Preview). Genera los secretos con
`python -c "import secrets;print(secrets.token_urlsafe(48))"` y la llave Fernet con
`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`.

| Variable | Valor |
|---|---|
| `ADMIN_ENVIRONMENT` | `staging` |
| `ADMIN_JWT_SECRET` | secreto aleatorio (≥ 32 caracteres) |
| `ADMIN_APP_JWT_SECRET` | secreto aleatorio, **el mismo que use la API principal de la app** |
| `ADMIN_BILLING_ENCRYPTION_KEY` | llave Fernet generada |
| `ADMIN_BILLING_GATEWAY` | `fake` (simulador de Webpay) o `transbank` (ver §5) |
| `ADMIN_PUBLIC_BASE_URL` | `https://agecare-admin-api.vercel.app` (tu URL de la API) |
| `ADMIN_BILLING_RETURN_URL` | `https://agecare-admin-web.vercel.app/checkout/resultado` |
| `ADMIN_CORS_ORIGINS` | `https://agecare-admin-web.vercel.app` |
| `ADMIN_CORS_ORIGIN_REGEX` | `https://agecare-admin-web(-[a-z0-9-]+)?\.vercel\.app` (previews) |
| `CRON_SECRET` | secreto aleatorio (≥ 16 caracteres). Vercel lo envía solo al cron |

Luego **Deployments → … → Redeploy** para que tome las variables.

**Comprobar:** `https://agecare-admin-api.vercel.app/health` → `{"status":"ok",…}` y
`/api/v1/admin/docs` muestra Swagger.

## 3. Proyecto de la consola web

1. **Add New… → Project → Import** el mismo repositorio.
2. **Root Directory**: `agecare-admin-web` (Vercel detecta Vite; `vercel.json` ya trae el
   *fallback* de la SPA).
3. Variable de entorno: `VITE_API_BASE_URL` = `https://agecare-admin-api.vercel.app`.
4. Nombre del proyecto `agecare-admin-web` → **Deploy**.

Si la URL de la web quedó distinta a la que pusiste en la API, corrige `ADMIN_CORS_ORIGINS` y
`ADMIN_BILLING_RETURN_URL` en la API y vuelve a desplegarla.

## 4. Probar y compartir

1. Abre `https://agecare-admin-web.vercel.app` e ingresa con un usuario demo.
2. *Checkout de prueba* → **Generar usuario de prueba** → elige plan → *Continuar a Webpay* →
   en el simulador elige **Inscribir Visa ···· 6623** → vuelves con la suscripción activa.
   **Mastercard ···· 0568** simula una tarjeta rechazada.
3. Revisa la suscripción en *Suscripciones* / *Cobros y reembolsos*.

**Texto para la descripción del grupo de WhatsApp** (cambia las URLs):

```
AgeCare · Consola de administración — Pasarela de pago y suscripciones
Web: https://agecare-admin-web.vercel.app
API (Swagger): https://agecare-admin-api.vercel.app/api/v1/admin/docs
Usuarios de prueba:
 • Administrador: admin@wellq.co.uk / Admin123!  (todo, incluye reembolsos)
 • Soporte: soporte@wellq.co.uk / Soporte123!    (cancelar, extender, reintentar)
 • Analista: analista@wellq.co.uk / Analista123! (solo lectura)
Probar un pago: menú «Checkout de prueba» → «Generar usuario de prueba» → elegir plan →
Webpay (simulado): Visa ···· 6623 = aprobada · Mastercard ···· 0568 = rechazada.
Ningún cobro es real.
```

## 5. (Opcional) Transbank real en ambiente de integración

Como Vercel publica la API en internet, Webpay puede volver a ella: puedes probar contra el
**ambiente de integración real de Transbank** cambiando `ADMIN_BILLING_GATEWAY=transbank`
(las credenciales de integración vienen por defecto). En el formulario de Transbank usa
Visa `4051 8856 0044 6623`, CVV `123` (aprobada) o Mastercard `5186 0595 5959 0568`
(rechazada); en el simulador bancario RUT `11.111.111-1`, clave `123`.
Los datos demo del seed tienen tarjetas ficticias: renovarlos contra Transbank real fallará
(código −96), así que en ese modo prueba con usuarios generados en el checkout.

## 6. Cron de facturación

`agecare-admin-api/vercel.json` programa `GET /api/v1/billing/internal/cron/billing-cycle` a las
07:00 UTC (≈ 04:00 en Santiago). En Hobby, Vercel lo ejecuta en algún minuto de esa hora.
El endpoint exige `Authorization: Bearer $CRON_SECRET`; sin `CRON_SECRET` responde 404.
Se ve en *Settings → Cron Jobs → View Logs*. Con plan Pro se puede subir a `*/15 * * * *`.

## 7. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| La consola dice «No hay conexión con la API» | `VITE_API_BASE_URL` mal escrita o sin redeploy de la web; o CORS: revisa `ADMIN_CORS_ORIGINS` (sin `/` final) |
| Login responde 500 | Faltan las tablas: corre el paso 2.2 contra la `DATABASE_URL` de Neon |
| Tras Webpay vuelve a la API y no a la web | `ADMIN_BILLING_RETURN_URL` sin configurar |
| «Token de inscripción inválido» en el simulador | Cambió `ADMIN_JWT_SECRET` entre la creación del token y el retorno: reintenta el checkout |
| La API no arranca con `ADMIN_ENVIRONMENT=prod` | Es intencional: prod exige Transbank real y llave de cifrado propia. Usa `staging` para pruebas |
| Logs | Proyecto de la API → *Logs* (cada request lleva `X-Request-Id`, que también aparece en los errores de la consola) |

## 8. Docker (Hito 2)

El proyecto completo también corre en local con Docker, sin Vercel:

```bash
docker compose up --build     # en la raíz del repositorio
# Consola: http://localhost:8080 · API: http://localhost:8080/api/v1/admin/docs
```

Levanta PostgreSQL, la API (con migraciones y seed), el job de facturación cada 15 min y la web
servida por nginx en el mismo origen que la API.
