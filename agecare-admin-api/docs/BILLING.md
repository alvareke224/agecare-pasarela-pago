# Pasarela de pago y suscripciones — AgeCare

Módulo `app/billing` de la API de administración. Cobra los planes de pago de AgeCare
(**Dorado** por usuario, **Platino** por familia, **Proveedor** por proveedor) con
**Transbank Oneclick Mall** y gestiona el ciclo de vida de las suscripciones mensuales.

> Versión 1 · octubre 2026. Complementa la *Especificación de Endpoints — Consola v1*
> y el *Modelo de Datos v1* (las tablas nuevas están en `db/agecare_billing_ddl.sql` y
> en la sección 11 de `db/agecare_admin.dbml`).

---

## 1. Por qué Oneclick Mall

| Necesidad | Cómo la cubre Oneclick Mall |
|---|---|
| Cobro mensual automático | El usuario inscribe su tarjeta una vez en Webpay; AgeCare guarda un `tbk_user` y cobra desde un job. Transbank no ofrece suscripciones ni webhooks: **el ciclo lo gestiona este backend**. |
| Sin datos de tarjeta en AgeCare | El formulario es de Transbank. Solo se guardan tipo y últimos 4 dígitos (fuera del alcance PCI más exigente). |
| Mercado chileno, CLP | Débito y crédito chilenos, montos en pesos sin decimales. |
| Varias líneas de negocio | «Mall» permite tiendas hijas: hoy una (suscripciones); el marketplace podría tener la suya. |

La pasarela está detrás de la interfaz `PaymentGateway` (`app/billing/gateway.py`). Para
México u otro país basta otra implementación (p. ej. Mercado Pago) sin tocar el resto.

## 2. Flujos

### 2.1 Contratar un plan (app)

```
App ──POST /api/v1/billing/subscriptions {plan_code}──▶ API
        ├─ si el usuario ya tiene tarjeta → cobra al instante → next_action = "none"
        └─ si no → POST /inscriptions a Transbank → next_action = "redirect_to_webpay"
App ──form POST (TBK_TOKEN) a url_webpay──▶ Webpay (el usuario ingresa su tarjeta)
Webpay ──GET|POST /api/v1/billing/oneclick/return?TBK_TOKEN──▶ API
        PUT /inscriptions/{token} → guarda tbk_user cifrado → cobra el 1er mes
API ──303──▶ ADMIN_BILLING_RETURN_URL?result=success|charge_failed|rejected|aborted|charge_pending|error
```

- La app **debe** redirigir con un formulario `POST` (campo `TBK_TOKEN`); Webpay no acepta GET.
  En móvil se hace desde un WebView o el navegador del sistema, y `ADMIN_BILLING_RETURN_URL`
  puede ser un deep link (`agecare://checkout/resultado`).
- El retorno acepta GET y POST: Transbank usa GET en la API ≥ 1.1 pero POST en algunos casos
  de anulación, y la documentación recomienda soportar ambos.
- Repetir el retorno es idempotente: no vuelve a cobrar.
- Si el pago se rechaza, la suscripción queda `incomplete` y el usuario puede inscribir otra
  tarjeta (`POST /payment-methods`). Al confirmarse, se cobra automáticamente.

### 2.2 Estados de la suscripción

```
incomplete ──pago OK──▶ active ──rechazo en renovación──▶ past_due ──pago OK──▶ active
    │                     │                                   │
    │ 24 h sin pago       │ cancelación                       │ reintentos agotados / cancelación
    ▼                     ▼                                   ▼
 expired               canceled ◀─────────────────────────────┘
```

| Estado | Acceso premium | Se cobra |
|---|---|---|
| `incomplete` | No | Al confirmar la tarjeta |
| `active` | Sí | Al fin de cada periodo |
| `past_due` | **Sí (gracia)** | Reintentos a +1, +3 y +5 días (`billing_dunning`) |
| `canceled` / `expired` | No | Nunca más (terminal) |

- **Cancelar** (usuario): si está activa, se mantiene hasta el fin del periodo pagado
  (`cancel_at_period_end`) y se puede reanudar; si está pendiente o en mora, se cancela ya.
- **Cambiar de plan**: se programa para la próxima renovación (sin prorrateo en v1). El nuevo
  precio se toma de `plan_prices` en ese momento.
- **Precios**: se congelan en la suscripción. Cambiar `plan_prices` en Configuración afecta solo
  a suscripciones nuevas y a cambios de plan.
- **Periodos**: anclados al día del primer pago (un alta el 31 renueva el 28/29 de febrero y
  vuelve al 31 en marzo). Tras una mora, el periodo pagado parte en la fecha de vencimiento
  original, así el día de cobro no se corre.
- **Plan efectivo** (`GET /me` → `entitlement`): el mejor plan vigente entre lo que paga el
  usuario y lo que pagan sus familias o su proveedor. Un familiar de una familia Platino tiene
  Platino aunque no sea quien paga.

### 2.3 Garantías contra doble cobro

1. Cada cobro se inserta en `pending` y se hace **COMMIT antes** de llamar a Transbank.
2. Si Transbank no responde (timeout, red, 5xx) el cobro queda `unknown`. El job lo **concilia**
   con `GET /transactions/{buy_order}`: si Transbank lo aprobó se aplica; si nunca llegó
   (404) se marca `TBK_NOT_PROCESSED` y no cuenta como rechazo del usuario.
3. Índice único parcial: **como máximo un cobro en vuelo** (`pending`/`unknown`) por suscripción.
4. El job toma cada suscripción con `SELECT … FOR UPDATE SKIP LOCKED`.

Verificado en PostgreSQL 16:

| Escenario | Resultado |
|---|---|
| 2 workers del job sobre 22 renovaciones vencidas | 11 cada uno · 22 llamadas a Transbank · 0 cobros dobles |
| 5 checkouts simultáneos del mismo plan y beneficiario | 1 × 201, 4 × 409 · 1 suscripción · 1 cobro |
| 3 reembolsos simultáneos de $6.000 sobre un cobro de $10.000 | 1 × 201, 2 × 422 · reembolsado exactamente $6.000 |

> La suite `pytest` corre sobre SQLite, que ignora `FOR UPDATE` y comparte una sola conexión:
> los escenarios de concurrencia solo se validan contra PostgreSQL (como arriba).

### 2.4 Ciclo de facturación (job)

`python -m scripts.billing_cycle` — cada 15 min. En Azure: **Container Apps Job** con cron
`*/15 * * * *`, la misma imagen de la API y el rol de BD `agecare_admin_jobs`. En **Vercel**:
Cron Job que llama `GET /api/v1/billing/internal/cron/billing-cycle` con
`Authorization: Bearer $CRON_SECRET` (diario en el plan Hobby; ver `../DEPLOY_VERCEL.md`). Sale con código 1
si hubo errores, para que Application Insights alerte.

Cada corrida: (1) concilia cobros `pending`/`unknown` de más de 2 min · (2) expira suscripciones
`incomplete` de más de 24 h e inscripciones abandonadas · (3) cierra las que tenían cancelación
programada · (4) cobra renovaciones y reintentos vencidos. Un error en una suscripción no
detiene el resto.

Desde la consola, un admin puede ejecutarlo a mano (`POST /admin/billing/jobs/billing-cycle`).

## 3. Endpoints

### 3.1 App — `/api/v1/billing` (JWT del usuario final)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/plans` | Planes de pago con precio vigente (público) |
| GET | `/me` | Plan efectivo, suscripciones que paga y tarjeta activa |
| GET | `/me/charges` | Historial de cobros (paginado) |
| POST | `/subscriptions` | Contratar `{plan_code, beneficiary_id?}` → cobro o redirección a Webpay |
| POST | `/subscriptions/{id}/cancel` | Cancelar (al fin del periodo si está activa) |
| POST | `/subscriptions/{id}/resume` | Revertir una cancelación programada |
| POST | `/subscriptions/{id}/change-plan` | Programar cambio de plan para la renovación |
| POST | `/payment-methods` | Inscribir/cambiar tarjeta (devuelve `url_webpay` + `token`) |
| DELETE | `/payment-methods/{id}` | Eliminar tarjeta (409 si paga una suscripción activa) |
| GET·POST | `/oneclick/return` | `response_url` de Webpay (no lo llama la app) |

**JWT de la app** — lo emite la API principal de AgeCare; este módulo solo lo verifica
(`ADMIN_APP_JWT_SECRET`, HS256; o RS256 con llave pública). *Claims* esperados:
`sub` (uuid del usuario), `email`, `name`, `family_ids` (familias que administra, para
Platino) y `provider_id` (para Proveedor). **Contrato a confirmar con el equipo de la API
principal.**

### 3.2 Consola — `/api/v1/admin/billing` (JWT de staff)

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/summary` | `billing` lectura: MRR, activas, mora, aprobación 30 d, ingresos netos, renovaciones 7 d |
| GET | `/subscriptions` · `/subscriptions/{id}` | `billing` lectura. Filtros: status, plan, q, cancel_scheduled, user_id |
| POST | `/subscriptions/{id}/cancel` | `billing` escritura. `{immediate, reason, note}` |
| POST | `/subscriptions/{id}/retry-charge` | `billing` escritura. Solo en mora; no consume reintentos |
| POST | `/subscriptions/{id}/extend` | `billing` escritura. Compensación `{days ≤ 90, reason}` |
| GET | `/charges` · `/charges/{id}` | `billing` lectura. Filtros: status, kind, q (orden o correo), fechas |
| POST | `/charges/{id}/refunds` | `billing_refunds` (**solo admin**). `{amount, reason}` |
| POST | `/jobs/billing-cycle` | `billing_refunds` (solo admin) |

Matriz de permisos: `billing` → lectura admin, analista y soporte; escritura admin y soporte.
`billing_refunds` → solo admin. Todas las escrituras quedan en `audit_log`
(`billing.subscription_cancel`, `billing.subscription_extend`, `billing.charge_retry`,
`billing.refund`, `billing.cycle_run`).

### 3.3 Códigos de error nuevos

| HTTP | `error.code` | Cuándo |
|---|---|---|
| 409 | `SUBSCRIPTION_EXISTS` | El beneficiario ya tiene una suscripción viva |
| 409 | `PAYMENT_METHOD_IN_USE` | Eliminar la tarjeta de una suscripción activa |
| 409 | `CHARGE_IN_PROGRESS` | Ya hay un cobro en vuelo para la suscripción |
| 409 | `NOT_PAST_DUE` · `INVALID_TRANSITION` · `NOT_SCHEDULED_TO_CANCEL` · `SAME_PLAN` | Reglas de estado |
| 409 | `CHARGE_NOT_REFUNDABLE` | Reembolsar un cobro no aprobado |
| 422 | `REFUND_EXCEEDS_CHARGE` | El monto supera lo reembolsable |
| 422 | `BENEFICIARY_REQUIRED` · `PLAN_NOT_PURCHASABLE` | Datos de contratación |
| 403 | `FORBIDDEN_BENEFICIARY` | Familia o proveedor que no pertenece al usuario |
| 502 | `PAYMENT_PROVIDER_UNAVAILABLE` · `REFUND_REJECTED` | Transbank falló o rechazó |
| 504 | `REFUND_UNCONFIRMED` | Transbank no respondió al reembolso: verificar en su portal antes de reintentar |
| 503 | `PLAN_PRICE_MISSING` | Falta el precio en `plan_prices` |

### 3.4 Eventos (`billing_events`)

Bitácora inmutable que alimenta la línea de tiempo de la consola y sirve de fuente para que la
API principal notifique al usuario (push/correo): `subscription.created|activated|renewed|
recovered|past_due|canceled|cancel_scheduled|resumed|expired|extended|plan_change_scheduled|
plan_changed`, `charge.succeeded|failed|unknown|refunded|skipped`,
`payment_method.inscription_started|activated|removed|failed|aborted`.

## 4. Modelo de datos

Seis tablas `billing_*` (detalle en el DDL y la sección 11 del DBML). Reglas que **la base de
datos hace cumplir** aunque el código falle:

- Máquina de estados de la suscripción (tabla de transiciones + trigger).
- Plan y tipo de beneficiario coherentes (FK compuesta a `plans(code, billing_unit)`).
- Una suscripción viva por beneficiario y una tarjeta activa por usuario.
- Un cobro en vuelo por suscripción; orden de compra, monto y suscripción de un cobro inmutables;
  un cobro rechazado es definitivo.
- Reembolsos inmutables que no pueden superar lo cobrado (el trigger recalcula `refunded_amount`).
- RLS por tenant, historial `*_history` sin `tbk_user_enc` ni hash del token, rol de solo
  lectura sin acceso a esas columnas.
- Retención: cobros, reembolsos y eventos se conservan (obligación tributaria de 6 años); solo
  se purgan inscripciones abandonadas (`admin.billing_maintenance()`).

## 5. Configuración

| Variable | Defecto | Producción |
|---|---|---|
| `ADMIN_BILLING_GATEWAY` | `fake` | `transbank` |
| `ADMIN_TBK_ENVIRONMENT` | `integration` | `production` |
| `ADMIN_TBK_COMMERCE_CODE` / `_CHILD_COMMERCE_CODE` | códigos públicos de integración | los del contrato |
| `ADMIN_TBK_API_SECRET` | secreto público de integración | **Key Vault** |
| `ADMIN_BILLING_ENCRYPTION_KEY` | derivada del JWT (solo dev) | **Key Vault** (Fernet) |
| `ADMIN_PUBLIC_BASE_URL` | `http://localhost:8000` | URL pública de la API |
| `ADMIN_BILLING_RETURN_URL` | `http://localhost:5173/checkout/resultado` | página o deep link de la app |
| `ADMIN_APP_JWT_SECRET` / `_ALGORITHM` / `_AUDIENCE` | — | igual que la API principal |
| `ADMIN_CORS_ORIGINS` | `http://localhost:5173` | dominio de Static Web Apps |

Con `ADMIN_ENVIRONMENT=prod`, la API **no arranca** si la pasarela es el simulador, si apunta a
integración, si usa el código de comercio de integración o si falta la llave de cifrado.

## 6. Probar localmente

```bash
docker compose up --build              # API + PostgreSQL + job de facturación (simulador)
# o sin Docker: alembic upgrade head && python -m scripts.seed && uvicorn app.main:app

TOKEN=$(python -m scripts.app_token --email ana@demo.cl --family 7c1e2d4a-0000-4000-8000-000000000001)
curl -X POST localhost:8000/api/v1/billing/subscriptions -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"plan_code":"platinum"}'
```

En el simulador, Webpay es una página local con cuatro opciones: inscribir **Visa ···· 6623**
(pagos aprobados), **Mastercard ···· 0568** (pagos rechazados por el emisor), rechazar la
inscripción o anularla. Son las mismas tarjetas del ambiente de integración de Transbank.

La consola (`agecare-admin-web`) incluye un **Checkout de prueba** que recorre este flujo
desde el navegador.

**Contra el ambiente de integración real de Transbank:** `ADMIN_BILLING_GATEWAY=transbank`
(credenciales por defecto) y `ADMIN_PUBLIC_BASE_URL` debe ser alcanzable desde internet para el
retorno (p. ej. con un túnel). Tarjetas: Visa 4051 8856 0044 6623 (CVV 123, aprobada) y
Mastercard 5186 0595 5959 0568 (rechazada); RUT 11.111.111-1, clave 123 en el simulador
bancario.

En ese modo (`ADMIN_TBK_ENVIRONMENT=integration`) la pasarela es un `RoutingGateway`: las
inscripciones nuevas van a Transbank y los medios de pago creados con el simulador
(`provider = fake`, p. ej. los del seed) se siguen cobrando, conciliando y reembolsando en el
simulador, para que el cron no deje morosas las suscripciones demo. En producción se usa
Transbank directo, sin enrutador.

### Tests

- `pytest` — 78 tests (23 originales + 55 de facturación y despliegue serverless): checkout completo vía simulador y vía Transbank simulado por HTTP (enrutamiento demo/Transbank),
  reglas de beneficiario, cobro perdido por timeout y conciliado sin doble cobro, dunning
  completo hasta la cancelación, recuperación, cancelación programada, cambio de plan,
  expiración, inscripción eliminada en Transbank (−96), job en otro proceso, permisos,
  reembolsos (parcial, exceso, total, rechazo de Transbank) y el **contrato HTTP exacto** con
  Transbank (rutas, cabeceras, cuerpos, clasificación de errores) sin red.
- `psql -f db/agecare_billing_ddl_tests.sql` — 25 pruebas del DDL (restricciones, triggers,
  RLS, historial sin secretos, permisos del rol de solo lectura).

## 7. Paso a producción con Transbank

1. **Contratar Oneclick Mall** con Transbank (código de mall + código de tienda hija) y
   solicitar la llave secreta de producción.
2. **Validación de la integración**: Transbank exige un proceso de validación con evidencias
   de transacciones en integración antes de habilitar producción. Las pruebas de la sección 6
   generan esas evidencias (aprobada, rechazada, anulada, reembolso).
3. Cargar en Key Vault `ADMIN_TBK_API_SECRET` y `ADMIN_BILLING_ENCRYPTION_KEY`, y fijar
   `ADMIN_ENVIRONMENT=prod`, `ADMIN_BILLING_GATEWAY=transbank` y `ADMIN_TBK_ENVIRONMENT=production`.
4. Aplicar `db/agecare_billing_ddl.sql` en Azure PostgreSQL (después del DDL base).
5. Programar el Container Apps Job `billing_cycle` y una alerta de Application Insights sobre
   su código de salida y sobre `unresolved_charges > 0` durante más de 30 min.

## 8. Pendiente / fuera de alcance v1

- **Boleta electrónica (SII)**: Transbank no la emite. Integrar un proveedor de boletas
  (p. ej. vía `charge.succeeded`).
- **Prorrateo** en cambios de plan inmediatos (v1 aplica el cambio en la renovación).
- **Notificaciones** al usuario: la API principal debe consumir `billing_events`.
- **Migración de precios** a suscripciones existentes (hoy quedan congeladas).
- Revisar con el equipo legal los textos de suscripción recurrente y la cancelación.
- `plan_snapshot` / `metrics_daily` pueden leer `billing_subscriptions` en lugar de
  `app.subscriptions` (Modelo de Datos v1, sección 9.3).
