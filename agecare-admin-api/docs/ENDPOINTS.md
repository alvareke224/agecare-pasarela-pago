# Referencia de endpoints · Pasarela de pago y suscripciones

> Generado automáticamente desde el código (`python -m scripts.gen_api_reference`). No editar a mano: los resúmenes y errores están en `app/billing/api_docs.py` y los tipos en los esquemas Pydantic. La versión interactiva está en Swagger: `/api/v1/admin/docs`.

## Convenciones

- **Base URL:** staging `https://agecare-admin-api-delta.vercel.app` · local `http://localhost:8000`.
- **Autenticación:** `Authorization: Bearer <jwt>`. La app usa el JWT que emite la API principal de AgeCare (claims `sub`, `email`, `name`, `family_ids`, `provider_id`); la consola usa el token de `POST /api/v1/admin/auth/login`.
- **Formato:** JSON UTF-8. Montos en CLP como enteros (sin decimales). Fechas ISO 8601 en UTC.
- **Trazabilidad:** toda respuesta trae `X-Request-Id` (se puede enviar uno propio); aparece en los errores y en cada línea de log de esa petición.
- **Paginación:** `page` (≥ 1) y `page_size` (1–100); la respuesta es `{items, page, page_size, total}`.

### Formato de error (igual en todos los endpoints)

```json
{"error": {"code": "SUBSCRIPTION_EXISTS",
           "message": "Ya existe una suscripción vigente para este beneficiario.",
           "details": null,
           "request_id": "9f2c41d0a1b2c3d4"}}
```

`code` es estable (la app decide por él); `message` está en español y se puede mostrar al usuario; `details` solo viene en `VALIDATION_ERROR` con la lista `{field, issue}`.

## Índice

| Método | Ruta | Descripción | Acceso |
|---|---|---|---|
| `GET` | [`/api/v1/billing/me`](#get--api-v1-billing-me) | Estado de facturación del usuario (plan efectivo, suscripciones y tarjeta) | App · JWT de usuario (Bearer) |
| `GET` | [`/api/v1/billing/me/charges`](#get--api-v1-billing-me-charges) | Historial de cobros del usuario (paginado) | App · JWT de usuario (Bearer) |
| `GET` | [`/api/v1/billing/oneclick/return`](#get--api-v1-billing-oneclick-return) | Retorno de Webpay tras la inscripción (response_url) | Webpay (TBK_TOKEN) |
| `POST` | [`/api/v1/billing/oneclick/return`](#post--api-v1-billing-oneclick-return) | Retorno de Webpay tras la inscripción (response_url, formulario POST) | Webpay (TBK_TOKEN) |
| `POST` | [`/api/v1/billing/payment-methods`](#post--api-v1-billing-payment-methods) | Inscribir o cambiar la tarjeta (devuelve url_webpay + token) | App · JWT de usuario (Bearer) |
| `DELETE` | [`/api/v1/billing/payment-methods/{pm_id}`](#delete--api-v1-billing-payment-methods-pm-id) | Eliminar la tarjeta inscrita | App · JWT de usuario (Bearer) |
| `GET` | [`/api/v1/billing/plans`](#get--api-v1-billing-plans) | Listar planes y precios vigentes | Público |
| `POST` | [`/api/v1/billing/subscriptions`](#post--api-v1-billing-subscriptions) | Contratar un plan (inicia Webpay si no hay tarjeta o cobra con la inscrita) | App · JWT de usuario (Bearer) |
| `POST` | [`/api/v1/billing/subscriptions/{sub_id}/cancel`](#post--api-v1-billing-subscriptions-sub-id-cancel) | Cancelar una suscripción (al fin del periodo si está activa; de inmediato si no) | App · JWT de usuario (Bearer) |
| `POST` | [`/api/v1/billing/subscriptions/{sub_id}/change-plan`](#post--api-v1-billing-subscriptions-sub-id-change-plan) | Programar un cambio de plan para la próxima renovación | App · JWT de usuario (Bearer) |
| `POST` | [`/api/v1/billing/subscriptions/{sub_id}/resume`](#post--api-v1-billing-subscriptions-sub-id-resume) | Deshacer una cancelación programada | App · JWT de usuario (Bearer) |
| `GET` | [`/api/v1/admin/billing/charges`](#get--api-v1-admin-billing-charges) | Buscar y filtrar cobros (paginado) | Consola · admin, analyst, support |
| `GET` | [`/api/v1/admin/billing/charges/{charge_id}`](#get--api-v1-admin-billing-charges-charge-id) | Detalle de un cobro con reembolsos y respuesta de Transbank | Consola · admin, analyst, support |
| `POST` | [`/api/v1/admin/billing/charges/{charge_id}/chargeback`](#post--api-v1-admin-billing-charges-charge-id-chargeback) | Registrar un contracargo informado por Transbank (idempotente) | Consola · admin |
| `POST` | [`/api/v1/admin/billing/charges/{charge_id}/refunds`](#post--api-v1-admin-billing-charges-charge-id-refunds) | Reembolsar total o parcialmente un cobro aprobado | Consola · admin |
| `POST` | [`/api/v1/admin/billing/dev/app-token`](#post--api-v1-admin-billing-dev-app-token) | Crear un usuario de prueba de la app (solo dev/staging) | Consola · admin |
| `POST` | [`/api/v1/admin/billing/jobs/billing-cycle`](#post--api-v1-admin-billing-jobs-billing-cycle) | Ejecutar ahora el ciclo de facturación | Consola · admin |
| `GET` | [`/api/v1/admin/billing/subscriptions`](#get--api-v1-admin-billing-subscriptions) | Buscar y filtrar suscripciones (paginado) | Consola · admin, analyst, support |
| `GET` | [`/api/v1/admin/billing/subscriptions/{sub_id}`](#get--api-v1-admin-billing-subscriptions-sub-id) | Detalle de una suscripción con sus cobros y eventos | Consola · admin, analyst, support |
| `POST` | [`/api/v1/admin/billing/subscriptions/{sub_id}/cancel`](#post--api-v1-admin-billing-subscriptions-sub-id-cancel) | Cancelar una suscripción desde la consola (inmediata o al fin del periodo) | Consola · admin |
| `POST` | [`/api/v1/admin/billing/subscriptions/{sub_id}/extend`](#post--api-v1-admin-billing-subscriptions-sub-id-extend) | Extender el periodo pagado sin cobrar (compensación) | Consola · admin |
| `POST` | [`/api/v1/admin/billing/subscriptions/{sub_id}/retry-charge`](#post--api-v1-admin-billing-subscriptions-sub-id-retry-charge) | Reintentar ahora el cobro de una suscripción en mora | Consola · admin |
| `GET` | [`/api/v1/admin/billing/summary`](#get--api-v1-admin-billing-summary) | KPIs de facturación (MRR, activas, mora, aprobación, ingresos) | Consola · admin, analyst, support |
| `GET` | [`/api/v1/billing/internal/cron/billing-cycle`](#get--api-v1-billing-internal-cron-billing-cycle) | Ciclo de facturación invocado por Vercel Cron | Authorization: Bearer CRON_SECRET |

## App móvil (usuarios finales)

### GET /api/v1/billing/me

**Estado de facturación del usuario (plan efectivo, suscripciones y tarjeta).**

- **Acceso:** App · JWT de usuario (Bearer)
- **Detalle:** Suscripciones del usuario, tarjeta activa y plan efectivo (derecho a funciones premium).  El plan efectivo considera las suscripciones que paga el usuario y las de sus familias o proveedor (un miembro de una familia Platino tiene Platino aunque no sea quien paga).

**Respuesta 200:** [MeOut](#meout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |

### GET /api/v1/billing/me/charges

**Historial de cobros del usuario (paginado).**

- **Acceso:** App · JWT de usuario (Bearer)

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `page` | query | integer | no | ≥ 1, por defecto `1` |
| `page_size` | query | integer | no | ≥ 1, ≤ 100, por defecto `20` |

**Respuesta 200:** [Page[ChargeOut]](#pagechargeout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |

### GET /api/v1/billing/oneclick/return

**Retorno de Webpay tras la inscripción (response_url).**

- **Acceso:** Webpay (TBK_TOKEN)
- **Nota:** No responde JSON: redirige (303) a ADMIN_BILLING_RETURN_URL con `result` = success | charge_failed | charge_pending | rejected | aborted | error, más `subscription_id` y `card` cuando aplican.

**Respuesta 303:** redirección a `ADMIN_BILLING_RETURN_URL` (ver nota).

**Errores:** no devuelve errores JSON; cualquier fallo redirige con `result=error`.

### POST /api/v1/billing/oneclick/return

**Retorno de Webpay tras la inscripción (response_url, formulario POST).**

- **Acceso:** Webpay (TBK_TOKEN)
- **Nota:** Igual que la variante GET.

**Respuesta 303:** redirección a `ADMIN_BILLING_RETURN_URL` (ver nota).

**Errores:** no devuelve errores JSON; cualquier fallo redirige con `result=error`.

### POST /api/v1/billing/payment-methods

**Inscribir o cambiar la tarjeta (devuelve url_webpay + token).**

- **Acceso:** App · JWT de usuario (Bearer)
- **Detalle:** Inicia la inscripción de una tarjeta nueva (reemplaza la actual al confirmarse).

**Respuesta 201:** [InscriptionOut](#inscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 502 | `PAYMENT_PROVIDER_UNAVAILABLE` | Transbank no respondió al iniciar la inscripción. |

### DELETE /api/v1/billing/payment-methods/{pm_id}

**Eliminar la tarjeta inscrita.**

- **Acceso:** App · JWT de usuario (Bearer)

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `pm_id` | path | uuid | sí |  |

**Respuesta 200:** [PaymentMethodOut](#paymentmethodout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 404 | `NOT_FOUND` | La tarjeta no existe, no es del usuario o no está activa. |
| 409 | `PAYMENT_METHOD_IN_USE` | La tarjeta paga una suscripción activa o en mora. |

### GET /api/v1/billing/plans

**Listar planes y precios vigentes.**

- **Acceso:** Público
- **Detalle:** Planes de pago con su precio vigente (público: lo usa la pantalla de planes).

**Respuesta 200:** [PlansOut](#plansout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 503 | `PLAN_PRICE_MISSING` | No hay precio configurado para algún plan. |

### POST /api/v1/billing/subscriptions

**Contratar un plan (inicia Webpay si no hay tarjeta o cobra con la inscrita).**

- **Acceso:** App · JWT de usuario (Bearer)

**Cuerpo** (`application/json`, SubscribeIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `beneficiary_id` | uuid \| null | no | Familia (Platino) o proveedor (Proveedor). Opcional si el usuario tiene una sola |

**Respuesta 201:** [SubscribeOut](#subscribeout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 403 | `FORBIDDEN_BENEFICIARY` | La familia no es del usuario, o pide plan Proveedor sin ser proveedor (o con otro provider_id). |
| 409 | `SUBSCRIPTION_EXISTS` | El beneficiario ya tiene una suscripción vigente. |
| 409 | `CHARGE_IN_PROGRESS` | Ya hay un cobro en curso para esa suscripción. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |
| 422 | `INVALID_BENEFICIARY` | Plan Dorado con un beneficiary_id distinto del propio usuario. |
| 422 | `BENEFICIARY_REQUIRED` | Plan Platino sin beneficiary_id y el usuario no tiene exactamente una familia. |
| 422 | `PLAN_NOT_PURCHASABLE` | Se pidió el plan Gratuito. |
| 502 | `PAYMENT_PROVIDER_UNAVAILABLE` | Transbank no respondió al iniciar la inscripción. |
| 503 | `PLAN_PRICE_MISSING` | El plan no tiene precio configurado. |

### POST /api/v1/billing/subscriptions/{sub_id}/cancel

**Cancelar una suscripción (al fin del periodo si está activa; de inmediato si no).**

- **Acceso:** App · JWT de usuario (Bearer)
- **Detalle:** Cancela al final del periodo pagado (si está activa) o de inmediato (pendiente o en mora).

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Respuesta 200:** [SubscriptionOut](#subscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 404 | `NOT_FOUND` | La suscripción no existe o no pertenece al usuario. |
| 409 | `INVALID_TRANSITION` | La suscripción ya terminó (cancelada o expirada). |

### POST /api/v1/billing/subscriptions/{sub_id}/change-plan

**Programar un cambio de plan para la próxima renovación.**

- **Acceso:** App · JWT de usuario (Bearer)
- **Detalle:** Programa el cambio de plan para la próxima renovación (sin prorrateo en v1).

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Cuerpo** (`application/json`, ChangePlanIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `beneficiary_id` | uuid \| null | no |  |

**Respuesta 200:** [SubscriptionOut](#subscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 403 | `FORBIDDEN_BENEFICIARY` | La familia no es del usuario, o pide plan Proveedor sin ser proveedor (o con otro provider_id). |
| 404 | `NOT_FOUND` | La suscripción no existe o no pertenece al usuario. |
| 409 | `INVALID_TRANSITION` | La suscripción no está activa. |
| 409 | `SAME_PLAN` | Ya está en ese plan y no hay cambio pendiente. |
| 409 | `SUBSCRIPTION_EXISTS` | El nuevo beneficiario ya tiene otra suscripción vigente. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |
| 422 | `INVALID_BENEFICIARY` | Plan Dorado con un beneficiary_id distinto del propio usuario. |
| 422 | `BENEFICIARY_REQUIRED` | Plan Platino sin beneficiary_id y el usuario no tiene exactamente una familia. |
| 422 | `PLAN_NOT_PURCHASABLE` | Se pidió el plan Gratuito (para eso se cancela). |

### POST /api/v1/billing/subscriptions/{sub_id}/resume

**Deshacer una cancelación programada.**

- **Acceso:** App · JWT de usuario (Bearer)

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Respuesta 200:** [SubscriptionOut](#subscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el JWT de la app, es inválido o expiró. |
| 404 | `NOT_FOUND` | La suscripción no existe o no pertenece al usuario. |
| 409 | `NOT_SCHEDULED_TO_CANCEL` | La suscripción no tiene una cancelación programada. |

## Consola de administración

### GET /api/v1/admin/billing/charges

**Buscar y filtrar cobros (paginado).**

- **Acceso:** Consola · admin, analyst, support

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `status` | query | `pending` \| `authorized` \| `failed` \| `unknown` \| `refunded` \| `partially_refunded` \| null | no |  |
| `kind` | query | `initial` \| `renewal` \| `retry` \| `manual` \| null | no |  |
| `subscription_id` | query | uuid \| null | no |  |
| `q` | query | string \| null | no | Orden de compra o correo; largo ≤ 120 |
| `date_from` | query | datetime (ISO 8601) \| null | no |  |
| `date_to` | query | datetime (ISO 8601) \| null | no |  |
| `page` | query | integer | no | ≥ 1, por defecto `1` |
| `page_size` | query | integer | no | ≥ 1, ≤ 100, por defecto `25` |

**Respuesta 200:** [Page[AdminChargeOut]](#pageadminchargeout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |
| 422 | `INVALID_RANGE` | date_to es anterior a date_from. |

### GET /api/v1/admin/billing/charges/{charge_id}

**Detalle de un cobro con reembolsos y respuesta de Transbank.**

- **Acceso:** Consola · admin, analyst, support

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `charge_id` | path | uuid | sí |  |

**Respuesta 200:** [AdminChargeDetailOut](#adminchargedetailout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 404 | `NOT_FOUND` | El cobro no existe. |

### POST /api/v1/admin/billing/charges/{charge_id}/chargeback

**Registrar un contracargo informado por Transbank (idempotente).**

- **Acceso:** Consola · admin
- **Detalle:** Registra un contracargo informado por Transbank. Idempotente: repetirlo no duplica nada.  Transbank no notifica contracargos por API (no hay webhooks en Webpay/Oneclick): llegan por el portal de comercios. Este endpoint los incorpora: deja el evento `charge.chargeback`, cancela la suscripción (motivo fraude) y recalcula las métricas comerciales.

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `charge_id` | path | uuid | sí |  |

**Cuerpo** (`application/json`, ChargebackIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `note` | string | sí | Detalle del contracargo (queda en la auditoría). largo ≥ 5, largo ≤ 500 |
| `tbk_reference` | string \| null | no | N.° de disputa o referencia que entrega Transbank. largo ≤ 60 |
| `amount` | integer \| null | no | Monto disputado en CLP; por defecto, el del cobro. > 0 |
| `cancel_subscription` | boolean | no | Cancela de inmediato la suscripción (motivo fraude). por defecto `true` |

**Respuesta 200:** [ChargebackOut](#chargebackout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | El cobro no existe. |
| 409 | `CHARGE_NOT_PAID` | El cobro no fue aprobado. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |
| 422 | `CHARGEBACK_EXCEEDS_CHARGE` | El monto disputado supera lo cobrado. |

### POST /api/v1/admin/billing/charges/{charge_id}/refunds

**Reembolsar total o parcialmente un cobro aprobado.**

- **Acceso:** Consola · admin

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `charge_id` | path | uuid | sí |  |

**Cuerpo** (`application/json`, RefundIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `amount` | integer | sí | Monto a reembolsar en la moneda del cobro (CLP sin decimales). > 0 |
| `reason` | string | sí | largo ≥ 5, largo ≤ 500 |

**Respuesta 201:** [RefundOut](#refundout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | El cobro no existe. |
| 409 | `CHARGE_NOT_REFUNDABLE` | El cobro no está aprobado (o ya se reembolsó entero). |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |
| 422 | `REFUND_EXCEEDS_CHARGE` | El monto supera lo que queda por reembolsar. |
| 502 | `REFUND_REJECTED` | Transbank rechazó el reembolso. |
| 504 | `REFUND_UNCONFIRMED` | Transbank no respondió; hay que verificar antes de reintentar. |

### POST /api/v1/admin/billing/dev/app-token

**Crear un usuario de prueba de la app (solo dev/staging).**

- **Acceso:** Consola · admin
- **Detalle:** Emite un JWT de usuario de la app para el *Checkout de prueba* (demos en Vercel, QA).  Crea un usuario ficticio nuevo con una familia y un proveedor propios, así se pueden probar los tres planes. Deshabilitado en producción (ADMIN_ENVIRONMENT=prod).

**Respuesta 200:** [DevAppTokenOut](#devapptokenout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | Deshabilitado con ADMIN_ENVIRONMENT=prod. |

### POST /api/v1/admin/billing/jobs/billing-cycle

**Ejecutar ahora el ciclo de facturación.**

- **Acceso:** Consola · admin
- **Detalle:** Ejecuta ahora el ciclo de facturación (en producción corre cada 15 min como job).

**Respuesta 200:** [CycleOut](#cycleout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |

### GET /api/v1/admin/billing/subscriptions

**Buscar y filtrar suscripciones (paginado).**

- **Acceso:** Consola · admin, analyst, support

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `status` | query | `incomplete` \| `active` \| `past_due` \| `canceled` \| `expired` \| null | no |  |
| `plan` | query | `free` \| `gold` \| `platinum` \| `provider` \| null | no |  |
| `cancel_scheduled` | query | boolean \| null | no |  |
| `user_id` | query | uuid \| null | no |  |
| `q` | query | string \| null | no | Correo o nombre del pagador; largo ≤ 120 |
| `order` | query | string | no | por defecto `-created_at` |
| `page` | query | integer | no | ≥ 1, por defecto `1` |
| `page_size` | query | integer | no | ≥ 1, ≤ 100, por defecto `25` |

**Respuesta 200:** [Page[AdminSubscriptionOut]](#pageadminsubscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |

### GET /api/v1/admin/billing/subscriptions/{sub_id}

**Detalle de una suscripción con sus cobros y eventos.**

- **Acceso:** Consola · admin, analyst, support

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Respuesta 200:** [AdminSubscriptionDetailOut](#adminsubscriptiondetailout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 404 | `NOT_FOUND` | La suscripción no existe. |

### POST /api/v1/admin/billing/subscriptions/{sub_id}/cancel

**Cancelar una suscripción desde la consola (inmediata o al fin del periodo).**

- **Acceso:** Consola · admin

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Cuerpo** (`application/json`, AdminCancelIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `immediate` | boolean | no | true = cancela ya; false = al fin del periodo pagado. por defecto `false` |
| `reason` | `admin` \| `fraud` \| `user_request` | no | por defecto `admin` |
| `note` | string | sí | largo ≥ 5, largo ≤ 500 |

**Respuesta 200:** [AdminSubscriptionOut](#adminsubscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | La suscripción no existe. |
| 409 | `INVALID_TRANSITION` | La suscripción ya terminó o no admite cancelación programada. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |

### POST /api/v1/admin/billing/subscriptions/{sub_id}/extend

**Extender el periodo pagado sin cobrar (compensación).**

- **Acceso:** Consola · admin
- **Detalle:** Extiende el periodo pagado sin cobro (compensación de soporte).

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Cuerpo** (`application/json`, ExtendIn)

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `days` | integer | sí | ≥ 1, ≤ 90 |
| `reason` | string | sí | largo ≥ 5, largo ≤ 500 |

**Respuesta 200:** [AdminSubscriptionOut](#adminsubscriptionout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | La suscripción no existe. |
| 409 | `INVALID_TRANSITION` | Solo se extienden suscripciones activas. |
| 422 | `VALIDATION_ERROR` | Algún parámetro o campo del cuerpo no cumple su tipo o rango. |

### POST /api/v1/admin/billing/subscriptions/{sub_id}/retry-charge

**Reintentar ahora el cobro de una suscripción en mora.**

- **Acceso:** Consola · admin
- **Detalle:** Reintenta ya el cobro de una suscripción en mora (no consume un reintento programado).

**Parámetros**

| Nombre | En | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|---|
| `sub_id` | path | uuid | sí |  |

**Respuesta 200:** [AdminChargeOut](#adminchargeout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |
| 403 | `MFA_REQUIRED` | La cuenta no tiene 2FA activo y el ambiente lo exige (prod). |
| 404 | `NOT_FOUND` | La suscripción no existe. |
| 409 | `NOT_PAST_DUE` | La suscripción no está en mora. |
| 409 | `CHARGE_IN_PROGRESS` | Ya hay un cobro en curso. |
| 409 | `NO_PAYMENT_METHOD` | No tiene una tarjeta activa. |

### GET /api/v1/admin/billing/summary

**KPIs de facturación (MRR, activas, mora, aprobación, ingresos).**

- **Acceso:** Consola · admin, analyst, support

**Respuesta 200:** [BillingSummaryOut](#billingsummaryout)

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | Falta el token de la consola o expiró. |
| 403 | `FORBIDDEN` | El rol del usuario no tiene permiso sobre este módulo. |

## Internos (no se llaman desde la app ni la consola)

### GET /api/v1/billing/internal/cron/billing-cycle

**Ciclo de facturación invocado por Vercel Cron.**

- **Acceso:** Authorization: Bearer CRON_SECRET

**Errores**

| HTTP | code | Cuándo |
|---|---|---|
| 401 | `UNAUTHORIZED` | El secreto no coincide. |
| 404 | `NOT_FOUND` | CRON_SECRET no está configurado (endpoint deshabilitado). |

## Esquemas de respuesta

### AdminChargeDetailOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `charge` | [AdminChargeOut](#adminchargeout) | sí |  |
| `refunds` | array<[RefundOut](#refundout)> | sí |  |
| `refundable_amount` | integer | sí |  |
| `gateway_response` | object \| null | sí |  |

### AdminChargeOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `subscription_id` | uuid | sí |  |
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `kind` | `initial` \| `renewal` \| `retry` \| `manual` | sí |  |
| `buy_order` | string | sí |  |
| `amount` | integer | sí |  |
| `refunded_amount` | integer | sí |  |
| `currency_code` | string | sí |  |
| `status` | `pending` \| `authorized` \| `failed` \| `unknown` \| `refunded` \| `partially_refunded` | sí |  |
| `period_start` | datetime (ISO 8601) | sí |  |
| `period_end` | datetime (ISO 8601) | sí |  |
| `card_last4` | string \| null | sí |  |
| `authorization_code` | string \| null | sí |  |
| `transaction_date` | datetime (ISO 8601) \| null | sí |  |
| `failure_message` | string \| null | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |
| `user_id` | uuid | sí |  |
| `user_email` | string | sí |  |
| `attempt` | integer | sí |  |
| `response_code` | integer \| null | sí |  |
| `failure_code` | string \| null | sí |  |
| `commerce_code` | string | sí |  |
| `detail_buy_order` | string | sí |  |
| `resolved_at` | datetime (ISO 8601) \| null | sí |  |
| `requested_by` | uuid \| null | sí |  |

### AdminPaymentMethodOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `card_type` | string \| null | sí |  |
| `card_last4` | string \| null | sí |  |
| `status` | `pending` \| `active` \| `failed` \| `aborted` \| `removed` | sí |  |
| `activated_at` | datetime (ISO 8601) \| null | sí |  |
| `provider` | string | sí |  |
| `failure_code` | string \| null | sí |  |
| `removed_at` | datetime (ISO 8601) \| null | sí |  |

### AdminSubscriptionDetailOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `subscription` | [AdminSubscriptionOut](#adminsubscriptionout) | sí |  |
| `charges` | array<[AdminChargeOut](#adminchargeout)> | sí |  |
| `events` | array<[EventOut](#eventout)> | sí |  |

### AdminSubscriptionOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `plan_name` | string | sí |  |
| `status` | `incomplete` \| `active` \| `past_due` \| `canceled` \| `expired` | sí |  |
| `status_name` | string | sí |  |
| `beneficiary_type` | string | sí |  |
| `beneficiary_id` | uuid | sí |  |
| `price_amount` | integer | sí |  |
| `currency_code` | string | sí |  |
| `current_period_start` | datetime (ISO 8601) \| null | sí |  |
| `current_period_end` | datetime (ISO 8601) \| null | sí |  |
| `next_charge_at` | datetime (ISO 8601) \| null | sí |  |
| `cancel_at_period_end` | boolean | sí |  |
| `pending_plan_code` | `free` \| `gold` \| `platinum` \| `provider` \| null | sí |  |
| `retry_count` | integer | sí |  |
| `started_at` | datetime (ISO 8601) \| null | sí |  |
| `canceled_at` | datetime (ISO 8601) \| null | sí |  |
| `cancel_reason` | string \| null | sí |  |
| `payment_method` | [AdminPaymentMethodOut](#adminpaymentmethodout) \| null | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |
| `user_id` | uuid | sí |  |
| `user_email` | string | sí |  |
| `user_name` | string \| null | sí |  |
| `cancel_note` | string \| null | sí |  |

### BillingSummaryOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `currency_code` | string | sí |  |
| `mrr` | integer | sí |  |
| `plans` | array<[PlanRow](#planrow)> | sí |  |
| `subscriptions` | [SubscriptionCounts](#subscriptioncounts) | sí |  |
| `charges_30d` | [ChargeStats](#chargestats) | sí |  |
| `upcoming_7d` | [Upcoming](#upcoming) | sí |  |
| `unresolved_charges` | integer | sí |  |
| `gateway` | string | sí |  |
| `computed_at` | datetime (ISO 8601) | sí |  |

### ChargeOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `subscription_id` | uuid | sí |  |
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `kind` | `initial` \| `renewal` \| `retry` \| `manual` | sí |  |
| `buy_order` | string | sí |  |
| `amount` | integer | sí |  |
| `refunded_amount` | integer | sí |  |
| `currency_code` | string | sí |  |
| `status` | `pending` \| `authorized` \| `failed` \| `unknown` \| `refunded` \| `partially_refunded` | sí |  |
| `period_start` | datetime (ISO 8601) | sí |  |
| `period_end` | datetime (ISO 8601) | sí |  |
| `card_last4` | string \| null | sí |  |
| `authorization_code` | string \| null | sí |  |
| `transaction_date` | datetime (ISO 8601) \| null | sí |  |
| `failure_message` | string \| null | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |

### ChargeStats

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `approved` | integer | sí |  |
| `failed` | integer | sí |  |
| `approval_rate` | number \| null | sí |  |
| `gross_revenue` | integer | sí |  |
| `refunded` | integer | sí |  |
| `net_revenue` | integer | sí |  |

### ChargebackOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `charge_id` | uuid | sí |  |
| `amount` | integer | sí |  |
| `already_registered` | boolean | sí | true si el contracargo ya estaba registrado (llamada repetida) |
| `subscription` | [AdminSubscriptionOut](#adminsubscriptionout) | sí |  |

### CycleOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `reconciled` | integer | sí |  |
| `renewed` | integer | sí |  |
| `recovered` | integer | sí |  |
| `failed` | integer | sí |  |
| `unknown` | integer | sí |  |
| `canceled` | integer | sí |  |
| `expired_subscriptions` | integer | sí |  |
| `expired_inscriptions` | integer | sí |  |
| `paying_units` | integer | sí | Unidades que pagan según la facturación (llevadas al dashboard comercial) |
| `mrr_clp` | integer | sí | MRR real en CLP (llevado al dashboard comercial) |
| `errors` | array<string> | sí |  |

### DevAppTokenOut

Usuario ficticio de la app para el Checkout de prueba.

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `token` | string | sí | JWT de usuario de la app, válido por 12 horas |
| `user_id` | uuid | sí |  |
| `email` | string | sí |  |
| `family_id` | uuid | sí | Familia propia del usuario (para probar Platino) |
| `provider_id` | uuid | sí | Perfil de proveedor propio (para probar el plan Proveedor) |

### EntitlementOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `plan_name` | string | sí |  |
| `subscription_id` | uuid \| null | sí |  |
| `valid_until` | datetime (ISO 8601) \| null | sí |  |

### EventOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | integer | sí |  |
| `type` | string | sí |  |
| `actor_type` | string | sí |  |
| `actor_id` | uuid \| null | sí |  |
| `charge_id` | uuid \| null | sí |  |
| `payload` | object | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |

### InscriptionOut

El cliente debe enviar un formulario POST a `url_webpay` con el campo TBK_TOKEN = token.

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `url_webpay` | string | sí |  |
| `token` | string | sí |  |
| `method` | `POST` | no | por defecto `POST` |
| `token_field` | `TBK_TOKEN` | no | por defecto `TBK_TOKEN` |

### MeOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `entitlement` | [EntitlementOut](#entitlementout) | sí |  |
| `subscriptions` | array<[SubscriptionOut](#subscriptionout)> | sí |  |
| `payment_method` | [PaymentMethodOut](#paymentmethodout) \| null | sí |  |

### Page[AdminChargeOut]

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `items` | array<[AdminChargeOut](#adminchargeout)> | sí |  |
| `page` | integer | sí |  |
| `page_size` | integer | sí |  |
| `total` | integer | sí |  |

### Page[AdminSubscriptionOut]

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `items` | array<[AdminSubscriptionOut](#adminsubscriptionout)> | sí |  |
| `page` | integer | sí |  |
| `page_size` | integer | sí |  |
| `total` | integer | sí |  |

### Page[ChargeOut]

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `items` | array<[ChargeOut](#chargeout)> | sí |  |
| `page` | integer | sí |  |
| `page_size` | integer | sí |  |
| `total` | integer | sí |  |

### PaymentMethodOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `card_type` | string \| null | sí |  |
| `card_last4` | string \| null | sí |  |
| `status` | `pending` \| `active` \| `failed` \| `aborted` \| `removed` | sí |  |
| `activated_at` | datetime (ISO 8601) \| null | sí |  |

### PlanOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `name` | string | sí |  |
| `billing_unit` | string | sí |  |
| `price_amount` | integer | sí |  |
| `currency_code` | string | sí |  |

### PlanRow

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `plan_name` | string | sí |  |
| `active` | integer | sí |  |
| `past_due` | integer | sí |  |
| `mrr` | integer | sí |  |

### PlansOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `plans` | array<[PlanOut](#planout)> | sí |  |

### RefundOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `charge_id` | uuid | sí |  |
| `amount` | integer | sí |  |
| `status` | string | sí |  |
| `refund_type` | string \| null | sí |  |
| `authorization_code` | string \| null | sí |  |
| `reason` | string | sí |  |
| `failure_message` | string \| null | sí |  |
| `requested_by` | uuid | sí |  |
| `requested_by_name` | string \| null | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |

### SubscribeOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `subscription` | [SubscriptionOut](#subscriptionout) | sí |  |
| `next_action` | `redirect_to_webpay` \| `none` | sí |  |
| `inscription` | [InscriptionOut](#inscriptionout) \| null | no |  |
| `charge` | [ChargeOut](#chargeout) \| null | no |  |

### SubscriptionCounts

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `active` | integer | sí |  |
| `past_due` | integer | sí |  |
| `incomplete` | integer | sí |  |
| `cancel_scheduled` | integer | sí |  |
| `canceled_30d` | integer | sí |  |
| `started_30d` | integer | sí |  |

### SubscriptionOut

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `id` | uuid | sí |  |
| `plan_code` | `free` \| `gold` \| `platinum` \| `provider` | sí |  |
| `plan_name` | string | sí |  |
| `status` | `incomplete` \| `active` \| `past_due` \| `canceled` \| `expired` | sí |  |
| `status_name` | string | sí |  |
| `beneficiary_type` | string | sí |  |
| `beneficiary_id` | uuid | sí |  |
| `price_amount` | integer | sí |  |
| `currency_code` | string | sí |  |
| `current_period_start` | datetime (ISO 8601) \| null | sí |  |
| `current_period_end` | datetime (ISO 8601) \| null | sí |  |
| `next_charge_at` | datetime (ISO 8601) \| null | sí |  |
| `cancel_at_period_end` | boolean | sí |  |
| `pending_plan_code` | `free` \| `gold` \| `platinum` \| `provider` \| null | sí |  |
| `retry_count` | integer | sí |  |
| `started_at` | datetime (ISO 8601) \| null | sí |  |
| `canceled_at` | datetime (ISO 8601) \| null | sí |  |
| `cancel_reason` | string \| null | sí |  |
| `payment_method` | [PaymentMethodOut](#paymentmethodout) \| null | sí |  |
| `created_at` | datetime (ISO 8601) | sí |  |

### Upcoming

| Campo | Tipo | Obligatorio | Descripción / reglas |
|---|---|---|---|
| `count` | integer | sí |  |
| `amount` | integer | sí |  |
