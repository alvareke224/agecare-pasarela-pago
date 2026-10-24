# Engine de logs y estructura de mensajes · Pasarela de pago y suscripciones

La API registra lo que pasa en **dos lugares**, cada uno con un propósito distinto:

| | Logs estructurados (este documento) | Bitácoras en base de datos |
|---|---|---|
| Qué es | Una línea JSON por evento en la salida estándar | Tablas `billing_events` y `audit_log` |
| Para qué | Operar y depurar: errores, tiempos, llamadas a Transbank, correlación por `request_id` | Historia de negocio y auditoría: qué pasó con cada suscripción y quién lo hizo |
| Dónde se ven | Vercel → *Logs* · `docker compose logs` · Azure Log Analytics | Consola (detalle de suscripción) y consultas SQL |
| Retención | La del proveedor (Vercel Hobby: 1 h; Pro: 1 día; Log Analytics: configurable) | Permanente (inmutables) |

Todo evento de negocio se escribe en **ambos**: la fila en `billing_events` y una línea de log
`billing.<tipo>` con los mismos datos y el `request_id` de la petición que lo causó.

## 1. Cómo funciona

Código: `app/logs.py` (engine), `app/errors.py` (middleware y errores), `app/billing/service.py`
(eventos de negocio), `app/billing/gateway.py` (llamadas a Transbank), `app/billing/jobs.py` (job).

```
Petición HTTP ──► RequestIdMiddleware ──► dependencias de auth ──► endpoint ──► BillingService ──► Transbank
                  · fija request_id            · fija actor          │              │ event()            │ _request()
                  · mide la duración                                 │              ▼                    ▼
                  · al terminar: http.request              ApiError ─┴─► http.error   billing.*        tbk.response
                                                       excepción ───► http.exception
                      todas las líneas ──► logging (Python) ──► JsonFormatter ──► stdout ──► Vercel / Docker / Azure
```

1. **Contexto por petición.** El middleware toma `X-Request-Id` de la cabecera (o genera uno de
   16 caracteres) y lo guarda en un `contextvar`. La autenticación agrega el `actor`
   (`admin:<uuid>` o `user:<uuid>`). Toda línea emitida durante esa petición, en cualquier
   módulo, lleva ambos campos sin pasarlos a mano.
2. **Formato.** `JsonFormatter` convierte cada registro de `logging` en un objeto JSON de una
   línea (campos fijos + campos del evento). `TextFormatter` hace lo mismo en formato legible
   para desarrollo local.
3. **Protección de datos.** Antes de escribir, `redact()` reemplaza por `"***"` cualquier campo
   llamado `tbk_user`, `token`, `password`, `authorization`, `card_number`, `cvv`, `api_secret`,
   `access_token`, etc. (también dentro de objetos anidados), y `safe_path()` acorta los tokens
   de Webpay que van en rutas (`/inscriptions/e9a1c3…`). Los cuerpos de las peticiones y de las
   respuestas de Transbank **nunca** se registran. Un test (`tests/test_logs.py`) recorre un
   checkout completo y falla si aparece un `tbk_user` o un JWT en los logs.
4. **Salida.** Siempre a `stdout`: es lo que recogen Vercel, Docker y Azure Container Apps
   (Log Analytics) sin configuración adicional. El access log de uvicorn se desactiva porque lo
   reemplaza `http.request`.

### Configuración

| Variable | Valores | Por defecto | Uso |
|---|---|---|---|
| `ADMIN_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` | Nivel mínimo que se escribe |
| `ADMIN_LOG_FORMAT` | `json`, `text` | `json` | `text` solo para leer en local |
| `ADMIN_ENVIRONMENT` | `dev`, `staging`, `prod` | `dev` | Se copia al campo `env` de cada línea |

## 2. Estructura del mensaje

Cada línea es un objeto JSON independiente (formato *JSON Lines*).

### Campos fijos (siempre presentes)

| Campo | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `ts` | string ISO 8601 UTC, ms | Momento del evento | `"2026-10-18T03:48:47.684Z"` |
| `level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | Severidad | `"INFO"` |
| `logger` | string | Módulo que lo emite | `"agecare.billing"` |
| `event` | string | Nombre estable del evento (para filtrar y alertar) | `"billing.charge.succeeded"` |
| `msg` | string | Texto legible | `"POST /api/v1/billing/subscriptions → 201"` |
| `service` | string | Siempre `agecare-admin-api` | |
| `env` | string | `dev`, `staging` o `prod` | `"staging"` |

### Campos de contexto (cuando hay una petición HTTP)

| Campo | Tipo | Descripción |
|---|---|---|
| `request_id` | string | El mismo `X-Request-Id` que recibe el cliente y que aparece en el cuerpo de los errores |
| `actor` | string | `admin:<uuid>` (consola) o `user:<uuid>` (app). Ausente en peticiones anónimas, Webpay y el job |

### Campo de error (cuando hay una excepción)

| Campo | Tipo | Descripción |
|---|---|---|
| `error.type` | string | Clase de la excepción |
| `error.message` | string | Mensaje (máx. 500 caracteres) |
| `error.stack` | string | Traza (últimos 4000 caracteres) |

El resto de los campos dependen del evento (sección 3).

## 3. Catálogo de eventos

### HTTP (`logger: agecare.http`)

| `event` | Nivel | Cuándo | Campos propios |
|---|---|---|---|
| `http.request` | INFO (2xx/3xx) · WARNING (4xx) · ERROR (5xx) | Al terminar cada petición (salvo `/health`) | `method`, `path`, `status`, `duration_ms`, `actor`, `client_ip` |
| `http.error` | INFO (4xx) · ERROR (5xx) | Error de negocio con código (`ApiError`) | `status`, `code` |
| `http.exception` | ERROR | Excepción no controlada (responde 500 `INTERNAL_ERROR`) | `code`, `error` |

### Transbank (`logger: agecare.billing.transbank`)

| `event` | Nivel | Cuándo | Campos propios |
|---|---|---|---|
| `tbk.response` | INFO · WARNING si HTTP ≥ 400 | Cada llamada a la API de Oneclick Mall | `method`, `path` (token acortado), `status`, `duration_ms` |
| `tbk.unavailable` | WARNING | Timeout o error de red con Transbank | `method`, `path`, `error`, `duration_ms` |

### Negocio (`logger: agecare.billing`, `event: billing.<tipo>`)

Campos comunes: `subscription_id`, `charge_id`, `user_id`, `actor_type` (`user`, `admin`,
`system`) y los datos propios de cada tipo. Son los mismos tipos que se guardan en
`billing_events.type`.

| Tipo | Nivel | Significado | Datos propios |
|---|---|---|---|
| `subscription.created` | INFO | Se creó una suscripción (estado `incomplete`) | `plan_code`, `price_amount` |
| `subscription.activated` | INFO | Primer pago aprobado | `period_end` |
| `subscription.renewed` | INFO | Renovación mensual aprobada | `period_end` |
| `subscription.recovered` | INFO | Pago aprobado tras estar en mora | `period_end` |
| `subscription.past_due` | WARNING | Cobro rechazado: entra o sigue en mora | `retry_count`, `next_charge_at` |
| `subscription.canceled` | INFO | Cancelada (usuario, admin, fraude o reintentos agotados) | `reason`, `note` |
| `subscription.cancel_scheduled` | INFO | Cancelación al fin del periodo | `reason`, `effective_at` |
| `subscription.resumed` | INFO | Se deshizo la cancelación programada | — |
| `subscription.expired` | WARNING | Incompleta sin pago en 24 h | — |
| `subscription.extended` | INFO | Extensión manual desde la consola | `days`, `reason`, `period_end` |
| `subscription.plan_change_scheduled` | INFO | Cambio de plan para la próxima renovación | `from_plan`, `to_plan`, `effective_at` |
| `subscription.plan_changed` | INFO | Cambio de plan aplicado al renovar | `from_plan`, `to_plan`, `price_amount` |
| `subscription.plan_change_skipped` | WARNING | No se aplicó: el beneficiario ya tenía otra suscripción | `reason` |
| `payment_method.inscription_started` | INFO | Se abrió Webpay para inscribir tarjeta | `payment_method_id` |
| `payment_method.activated` | INFO | Tarjeta inscrita | `payment_method_id`, `card_type`, `card_last4` |
| `payment_method.failed` / `payment_method.aborted` | WARNING | Inscripción rechazada por Webpay / anulada por el usuario o abandonada (el job la cierra con `TBK_TIMEOUT`) | `payment_method_id`, `failure_code` |
| `payment_method.removed` | INFO | Tarjeta eliminada (por el usuario o reemplazada) | `payment_method_id` |
| `charge.succeeded` | INFO | Transbank aprobó el cobro | `amount`, `buy_order` |
| `charge.failed` | WARNING | Transbank rechazó el cobro (o respondió error) | `failure_code`, `amount` |
| `charge.unknown` | WARNING | Transbank no respondió; el job lo concilia | `buy_order` |
| `charge.skipped` | WARNING | El job no cobró (p. ej., sin tarjeta activa) | `reason` |
| `charge.amount_mismatch` | **ERROR** | Transbank aprobó un monto distinto al pedido | `expected`, `received` |
| `charge.succeeded_on_closed_subscription` | **ERROR** | Cobro conciliado tarde sobre una suscripción ya cerrada: hay que reembolsar | `subscription_status` |
| `charge.chargeback` | **ERROR** | Contracargo registrado desde la consola | `amount`, `tbk_reference`, `buy_order` |
| `charge.refunded` | INFO | Reembolso aplicado | `amount`, `refund_type` |
| `refund.failed` | WARNING | Transbank rechazó el reembolso | `amount` |
| `refund.unconfirmed` | **ERROR** | Transbank no respondió al reembolso: verificar en su portal antes de reintentar | `amount` |

### Aplicación y job

| `event` | Nivel | Cuándo | Campos propios |
|---|---|---|---|
| `job.metrics_sync` | INFO | Métricas comerciales conciliadas con la facturación | `as_of`, `paying_units`, `mrr_clp` |
| `app.started` | INFO | Arranque de la API | `gateway`, `tbk_environment` |
| `job.billing_cycle` | INFO · ERROR si hubo errores | Fin de cada ciclo de facturación (cron, botón *Ejecutar ahora* o contenedor del job) | `duration_ms`, `reconciled`, `renewed`, `recovered`, `failed`, `unknown`, `canceled`, `expired_subscriptions`, `expired_inscriptions`, `errors` |

## 4. Ejemplos reales

Un checkout con tarjeta ya inscrita (líneas emitidas por la API en los tests):

```json
{"ts": "2026-10-18T04:03:25.663Z", "level": "INFO", "logger": "agecare.billing", "event": "billing.subscription.created", "msg": "subscription.created", "service": "agecare-admin-api", "env": "staging", "request_id": "req-test-123", "actor": "user:99573d08-c03b-5391-989e-33ae3532257a", "subscription_id": "001988a2-1da7-4632-82b8-fec7d5e9d633", "user_id": "99573d08-c03b-5391-989e-33ae3532257a", "actor_type": "user", "plan_code": "gold", "price_amount": 1000}
{"ts": "2026-10-18T04:03:25.667Z", "level": "INFO", "logger": "agecare.billing", "event": "billing.payment_method.inscription_started", "msg": "payment_method.inscription_started", "service": "agecare-admin-api", "env": "staging", "request_id": "req-test-123", "actor": "user:99573d08-c03b-5391-989e-33ae3532257a", "user_id": "99573d08-c03b-5391-989e-33ae3532257a", "actor_type": "user", "payment_method_id": "a19e9187-10d1-4f4b-9f4d-4430c76cc9e3"}
{"ts": "2026-10-18T04:03:25.672Z", "level": "INFO", "logger": "agecare.http", "event": "http.request", "msg": "POST /api/v1/billing/subscriptions → 201", "service": "agecare-admin-api", "env": "staging", "request_id": "req-test-123", "method": "POST", "path": "/api/v1/billing/subscriptions", "status": 201, "duration_ms": 94.9, "actor": "user:99573d08-c03b-5391-989e-33ae3532257a", "client_ip": "127.0.0.1"}
```

Llamada a Transbank y error de negocio:

```json
{"ts": "2026-10-18T03:48:47.512Z", "level": "INFO", "logger": "agecare.billing.transbank", "event": "tbk.response", "msg": "PUT /inscriptions/e9a1c3… → 200", "service": "agecare-admin-api", "env": "staging", "request_id": "5c1d0e2f9a7b4c3d", "method": "PUT", "path": "/inscriptions/e9a1c3…", "status": 200, "duration_ms": 431.2}
{"ts": "2026-10-18T03:52:10.004Z", "level": "INFO", "logger": "agecare.http", "event": "http.error", "msg": "SUBSCRIPTION_EXISTS: Ya existe una suscripción vigente para este beneficiario.", "service": "agecare-admin-api", "env": "staging", "request_id": "71954a53582840c1", "actor": "user:cd17d03e-a0bc-5067-90fb-af0881925a7d", "status": 409, "code": "SUBSCRIPTION_EXISTS"}
```

En local con `ADMIN_LOG_FORMAT=text`:

```
03:48:47.684 INFO    billing.charge.succeeded     charge.succeeded  {"request_id": "5c1d0e2f9a7b4c3d", "charge_id": "…", "amount": 1000, "buy_order": "AG261018AA97FB9C3D"}
```

## 5. Cómo usarlos

**Seguir un error que reporta un usuario.** La consola muestra el `request_id` en los mensajes
de error. Buscarlo en los logs devuelve todas las líneas de esa petición: la petición, los
eventos de negocio, las llamadas a Transbank y la excepción si la hubo.

**Vercel:** proyecto `agecare-admin-api` → *Logs* → buscar el `request_id`, `billing.charge.failed`,
`tbk.unavailable`, etc. Los cron se ven en *Settings → Cron Jobs → View Logs*.

**Docker:**

```bash
docker compose logs -f api | grep '"level": "ERROR"'
docker compose logs api billing-cycle | grep 'job.billing_cycle'
docker compose logs api | jq 'select(.event == "tbk.response") | .duration_ms'   # tiempos de Transbank
```

**Azure Log Analytics (KQL), si la API corre en Container Apps:**

```kusto
ContainerAppConsoleLogs_CL
| extend e = parse_json(Log_s)
| where e.service == "agecare-admin-api" and e.level in ("ERROR", "WARNING")
| project TimeGenerated, event = tostring(e.event), msg = tostring(e.msg), request_id = tostring(e.request_id)
```

### Alertas recomendadas

| Condición | Por qué |
|---|---|
| Cualquier `level = ERROR` | Error 500, monto distinto, reembolso sin confirmar o job con errores |
| `tbk.unavailable` > 3 en 10 min | Transbank caído o lento: los cobros quedan `unknown` |
| `billing.charge.failed` > 20 % de los cobros en 1 h | Problema con el emisor o con la integración |
| Falta `job.billing_cycle` en 25 h | El cron diario no corrió |

## 6. Buenas prácticas para quien agregue código

- Usar `log_event(logger, "dominio.accion", "mensaje", nivel, campo=valor)` y no `print`.
- Nombres de evento estables, en minúsculas, con punto: los usan filtros y alertas.
- Nunca registrar cuerpos de peticiones, respuestas de Transbank, JWT ni datos de tarjeta. Si
  aparece un campo sensible nuevo, agregarlo a `SENSITIVE_KEYS` en `app/logs.py`.
- Un evento de negocio va con `BillingService.event()`: así queda en `billing_events` **y** en el log.
