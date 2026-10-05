# AgeCare — Consola de administración · Facturación (web)

SPA en **React 18 + Vite + TypeScript** con el módulo **Facturación** de la consola: la
pasarela de pago (Transbank Oneclick Mall) y las suscripciones Dorado / Platino / Proveedor.
Usa los tokens visuales del *Wireframe Consola Administración v1* y la API `agecare-admin-api`.

| Pantalla | Ruta | Quién |
|---|---|---|
| Resumen (MRR, mora, aprobación, ingresos netos, ciclo de facturación) | `/facturacion` | admin, analista, soporte |
| Suscripciones (filtros por estado, plan y búsqueda) | `/facturacion/suscripciones` | idem |
| Detalle: reintentar cobro, extender, cancelar, línea de tiempo | `/facturacion/suscripciones/:id` | escritura: admin, soporte |
| Cobros y reembolsos | `/facturacion/cobros` · `/facturacion/cobros/:id` | reembolsar: solo admin |
| Checkout de prueba (el flujo de la app con Webpay) | `/checkout` | dev / QA |

Los demás módulos de la consola (EP-13) aparecen deshabilitados en el menú: los desarrollan
otros equipos y se integran en esta misma SPA.

## Desarrollo

```bash
npm install
npm run dev            # http://localhost:5173 (redirige /api a http://localhost:8000)
```

Requiere la API levantada (`docker compose up` en `agecare-admin-api`). Credenciales demo:
`admin@wellq.co.uk / Admin123!`, `soporte@wellq.co.uk / Soporte123!`,
`analista@wellq.co.uk / Analista123!`.

**Checkout de prueba:** genera un JWT de usuario de la app con
`python -m scripts.app_token --email ana@demo.cl` (en la carpeta de la API), pégalo en la
pantalla, elige un plan y completa el simulador de Webpay. Al volver verás el resultado y la
suscripción en la consola.

## Build y despliegue

```bash
npm run typecheck && npm run build     # → dist/
```

**Vercel** (ver `../DEPLOY_VERCEL.md`): proyecto con *Root Directory* `agecare-admin-web` y la
variable `VITE_API_BASE_URL` = URL pública de la API. `vercel.json` define el *fallback* de la
SPA y cabeceras de seguridad. Cada push a `main` despliega solo.

**Docker**: `Dockerfile` (build + nginx con `/api` hacia la API); se levanta con el
`docker-compose.yml` de la raíz en `http://localhost:8080`.

**Checkout de prueba en Vercel:** el botón **Generar usuario de prueba** crea un usuario
ficticio de la app (con familia y proveedor) sin necesidad de scripts. Requiere rol admin o
soporte y está deshabilitado con `ADMIN_ENVIRONMENT=prod`.

## Notas de seguridad

- El access token vive solo en memoria; el refresh token (rotatorio) en `sessionStorage`, se
  pierde al cerrar la pestaña. Un 401 intenta un refresh y, si falla, vuelve al login.
- Los botones de escritura se muestran según el rol, pero **la API es quien autoriza**
  (`billing` / `billing_refunds`); la UI solo evita acciones que fallarían.
- Webpay exige un formulario POST con `TBK_TOKEN` (ver `redirectToWebpay` en `src/api/billing.ts`).
