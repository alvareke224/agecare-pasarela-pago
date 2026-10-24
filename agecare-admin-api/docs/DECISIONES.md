# Decisiones de arquitectura · Pasarela de pago y suscripciones

Registro de las decisiones técnicas del módulo, con su contexto y sus consecuencias.

## D1 · Transbank Oneclick Mall en lugar de Webpay Plus

**Contexto.** Los planes Dorado, Platino y Proveedor se cobran todos los meses. Webpay Plus
procesa un pago por transacción: el usuario ingresa su tarjeta en Webpay cada vez que paga. No
permite que el comercio cobre después sin el usuario presente.

**Decisión.** Usar **Oneclick Mall**, el producto de la familia Webpay de Transbank para pagos
recurrentes: el usuario inscribe su tarjeta **una vez en el formulario de Webpay** y AgeCare
cobra cada mes desde el servidor con el `tbk_user` que devuelve Transbank. Se usa la variante
*Mall* porque permite separar líneas de negocio en tiendas hijas (hoy una: suscripciones).

**Consecuencias.** La experiencia de pago sigue siendo Webpay (mismo formulario y bancos). El
cobro mensual lo programa AgeCare (job de facturación), porque Transbank no administra
suscripciones. Si se agregara un pago único (por ejemplo, un artículo del marketplace), Webpay
Plus se integraría como otra implementación de `PaymentGateway` sin cambiar el resto del módulo.

## D2 · Confirmación servidor a servidor en lugar de webhook firmado

**Contexto.** La API REST de Transbank (Webpay Plus y Oneclick) **no envía notificaciones
servidor a servidor (webhooks) ni firmas**: al terminar, Webpay redirige el navegador del
usuario a la `response_url` del comercio, y el comercio debe **confirmar** el resultado llamando
a Transbank con sus credenciales. Los contracargos se informan en el portal de comercios de
Transbank, no por API.

**Decisión.** El endpoint de retorno `/api/v1/billing/oneclick/return` cumple el papel del
webhook, con estas garantías:

| Garantía de un webhook firmado | Cómo se cumple aquí |
|---|---|
| Autenticidad del mensaje | No se confía en los parámetros del navegador: el resultado se obtiene llamando a Transbank (`PUT /inscriptions/{token}`, `POST /transactions`, `GET /transactions/{buy_order}`) con `Tbk-Api-Key-Id` y `Tbk-Api-Key-Secret` sobre TLS. |
| Token válido y no reutilizable | El `TBK_TOKEN` se busca por su hash SHA-256 guardado al iniciar la inscripción; uno desconocido o vencido se rechaza. |
| Idempotencia | Un retorno repetido devuelve el mismo resultado sin volver a cobrar (estado del medio de pago); un cobro sin respuesta queda `unknown` y el job lo concilia por `buy_order`; un índice único impide dos cobros en curso. |
| Confirmación y rechazo | Se aplican al confirmar con Transbank: suscripción `active` o `past_due` con sus reintentos. |
| Contracargo | `POST /api/v1/admin/billing/charges/{id}/chargeback` (solo admin): idempotente, cancela la suscripción por fraude y recalcula las métricas. |
| Métricas actualizadas | Cada ciclo de facturación y cada contracargo actualizan `metrics_plan_snapshot` y `metrics_daily_users`. |

**Consecuencias.** Si Transbank publicara notificaciones firmadas en el futuro, se agregarían
como otra entrada al mismo `BillingService` (las operaciones ya son idempotentes).

## D3 · Las métricas comerciales salen de la facturación real

**Contexto.** El dashboard «Uso comercial» leía `metrics_plan_snapshot` y `metrics_daily_users`
con datos simulados.

**Decisión.** `app/billing/metrics_sync.py` reescribe cada día (y en cada ejecución del ciclo)
las filas de hoy de los planes de pago: unidades que pagan (`active` + `past_due`), precio
vigente, MRR (suma de los precios congelados) y churn de 30 días; y en `metrics_daily_users`,
los pagadores y el MRR del día. Es idempotente. La fila del plan Gratuito y las columnas de uso
(altas, descargas, activos) siguen siendo de la ingesta de la app.

**Consecuencias.** La consola de facturación y el dashboard comercial muestran el mismo MRR.
Los días anteriores a la puesta en marcha conservan sus valores históricos.

## D4 · Alcance PCI-DSS mínimo

Los datos de tarjeta se ingresan solo en el formulario de Webpay: ningún número de tarjeta, CVV
ni fecha pasa por los servidores de AgeCare. Se guardan el tipo y los últimos 4 dígitos; el
`tbk_user` se cifra con Fernet (llave en una variable de entorno o Key Vault) y no se copia al
historial ni a los logs. Las credenciales de Transbank viven en variables de entorno, y la API
no arranca en producción con las credenciales públicas de integración.

## D5 · Permisos: escrituras de pagos solo para administradores con 2FA

Lectura de facturación: admin, analista y soporte. Toda escritura (cancelar, extender,
reintentar, reembolsar, contracargo, ciclo manual, usuario de prueba): **solo admin**. En
producción (`ADMIN_ENVIRONMENT=prod`, o `ADMIN_BILLING_REQUIRE_MFA=true`) además se exige que la
cuenta tenga 2FA activo (`MFA_REQUIRED`). Las sesiones usan el esquema de la consola: JWT de 15
minutos y refresh de 12 horas. Cada escritura queda en `audit_log` con el antes y el después.
