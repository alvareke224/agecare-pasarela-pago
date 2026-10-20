"""Genera docs/ENDPOINTS.md (referencia de la API de facturación) desde el OpenAPI real.

Uso:  python -m scripts.gen_api_reference
Fuente: rutas de FastAPI + esquemas Pydantic + catálogo app/billing/api_docs.py.
"""
from __future__ import annotations

from pathlib import Path

from app.billing.api_docs import ENDPOINTS
from app.main import app

OUT = Path(__file__).resolve().parent.parent / "docs" / "ENDPOINTS.md"


def ref_name(schema: dict) -> str | None:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def show(name: str) -> str:
    """Nombre legible de un esquema (FastAPI antepone el módulo cuando dos se llaman igual)."""
    return name.split("__")[-1].replace("Page_", "Page[").rstrip("_") + ("]" if name.startswith("Page_") else "")


def num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def type_of(schema: dict, components: dict) -> str:
    if not schema:
        return "any"
    if "$ref" in schema:
        name = ref_name(schema)
        target = components.get(name, {})
        if "enum" in target:
            return " \\| ".join(f"`{v}`" for v in target["enum"])
        return f"[{show(name)}](#{show(name).lower().replace('[', '').replace(']', '')})"
    if "anyOf" in schema:
        parts = [type_of(s, components) for s in schema["anyOf"] if s.get("type") != "null"]
        nullable = any(s.get("type") == "null" for s in schema["anyOf"])
        return " \\| ".join(parts) + (" \\| null" if nullable else "")
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return type_of(schema["allOf"][0], components)
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    t = schema.get("type", "object")
    if t == "array":
        return f"array<{type_of(schema.get('items', {}), components)}>"
    fmt = schema.get("format")
    if fmt in ("uuid", "date-time", "date"):
        return {"uuid": "uuid", "date-time": "datetime (ISO 8601)", "date": "date"}[fmt]
    return t


def constraints(schema: dict) -> str:
    out = []
    for key, label in (("minimum", "≥"), ("maximum", "≤"), ("exclusiveMinimum", ">"),
                       ("minLength", "largo ≥"), ("maxLength", "largo ≤")):
        if key in schema:
            out.append(f"{label} {num(schema[key])}")
    if "default" in schema and schema["default"] is not None:
        d = schema["default"]
        out.append(f"por defecto `{str(d).lower() if isinstance(d, bool) else d}`")
    return ", ".join(out)


def esc(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def fields_table(name: str, components: dict) -> list[str]:
    schema = components[name]
    req = set(schema.get("required", []))
    rows = ["| Campo | Tipo | Obligatorio | Descripción / reglas |", "|---|---|---|---|"]
    for field, fs in schema.get("properties", {}).items():
        desc = esc(fs.get("description")).rstrip(".")
        rules = constraints(fs) or constraints(next((s for s in fs.get("anyOf", []) if s.get("type") != "null"), {}))
        rows.append(f"| `{field}` | {type_of(fs, components)} | {'sí' if field in req else 'no'} | "
                    f"{'. '.join(x for x in (desc, rules) if x)} |")
    return rows


def main() -> None:
    spec = app.openapi()
    components = spec["components"]["schemas"]
    used: set[str] = set()
    lines = [
        "# Referencia de endpoints · Pasarela de pago y suscripciones",
        "",
        "> Generado automáticamente desde el código (`python -m scripts.gen_api_reference`). "
        "No editar a mano: los resúmenes y errores están en `app/billing/api_docs.py` y los "
        "tipos en los esquemas Pydantic. La versión interactiva está en Swagger: `/api/v1/admin/docs`.",
        "",
        "## Convenciones",
        "",
        "- **Base URL:** staging `https://agecare-admin-api-delta.vercel.app` · local `http://localhost:8000`.",
        "- **Autenticación:** `Authorization: Bearer <jwt>`. La app usa el JWT que emite la API principal "
        "de AgeCare (claims `sub`, `email`, `name`, `family_ids`, `provider_id`); la consola usa el "
        "token de `POST /api/v1/admin/auth/login`.",
        "- **Formato:** JSON UTF-8. Montos en CLP como enteros (sin decimales). Fechas ISO 8601 en UTC.",
        "- **Trazabilidad:** toda respuesta trae `X-Request-Id` (se puede enviar uno propio); aparece en "
        "los errores y en cada línea de log de esa petición.",
        "- **Paginación:** `page` (≥ 1) y `page_size` (1–100); la respuesta es "
        "`{items, page, page_size, total}`.",
        "",
        "### Formato de error (igual en todos los endpoints)",
        "",
        "```json",
        '{"error": {"code": "SUBSCRIPTION_EXISTS",',
        '           "message": "Ya existe una suscripción vigente para este beneficiario.",',
        '           "details": null,',
        '           "request_id": "9f2c41d0a1b2c3d4"}}',
        "```",
        "",
        "`code` es estable (la app decide por él); `message` está en español y se puede mostrar al "
        "usuario; `details` solo viene en `VALIDATION_ERROR` con la lista `{field, issue}`.",
        "",
        "## Índice",
        "",
        "| Método | Ruta | Descripción | Acceso |",
        "|---|---|---|---|",
    ]
    def group_of(path: str, info: dict) -> tuple[int, str]:
        if info.get("internal"):
            return 2, "Internos (no se llaman desde la app ni la consola)"
        if path.startswith("/api/v1/billing"):
            return 0, "App móvil (usuarios finales)"
        return 1, "Consola de administración"

    ordered = sorted(ENDPOINTS.items(), key=lambda kv: (group_of(kv[0][1], kv[1])[0], kv[0][1], kv[0][0]))
    for (method, path), info in ordered:
        anchor = f"{method}-{path}".lower().replace("/", "-").replace("{", "").replace("}", "").replace("_", "-")
        lines.append(f"| `{method}` | [`{path}`](#{anchor.strip('-')}) | {info['summary']} | {info['auth']} |")

    section = None
    for (method, path), info in ordered:
        group = group_of(path, info)[1]
        if group != section:
            section = group
            lines += ["", f"## {group}"]
        op = spec["paths"].get(path, {}).get(method.lower(), {})
        lines += ["", f"### {method} {path}", "", f"**{info['summary']}.**", ""]
        lines.append(f"- **Acceso:** {info['auth']}")
        desc = (op.get("description") or "").split("**Acceso:**")[0].strip()
        if desc:
            lines.append(f"- **Detalle:** {esc(desc)}")
        if info.get("notes"):
            lines.append(f"- **Nota:** {info['notes']}")
        params = op.get("parameters", [])
        if params:
            lines += ["", "**Parámetros**", "", "| Nombre | En | Tipo | Obligatorio | Descripción / reglas |",
                      "|---|---|---|---|---|"]
            for p in params:
                s = p.get("schema", {})
                rules = constraints(s) or constraints(next((x for x in s.get("anyOf", []) if x.get("type") != "null"), {}))
                lines.append(f"| `{p['name']}` | {p['in']} | {type_of(s, components)} | "
                             f"{'sí' if p.get('required') else 'no'} | "
                             f"{'; '.join(x for x in (esc(p.get('description')), rules) if x)} |")
        body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        if body:
            name = ref_name(body)
            lines += ["", f"**Cuerpo** (`application/json`, {show(name)})", ""]
            if name:
                lines += fields_table(name, components)
        ok = next((c for c in op.get("responses", {}) if c.startswith("2") or c.startswith("3")), None)
        if "oneclick/return" in path:
            lines += ["", "**Respuesta 303:** redirección a `ADMIN_BILLING_RETURN_URL` (ver nota)."]
        elif ok:
            schema = op["responses"][ok].get("content", {}).get("application/json", {}).get("schema", {})
            rtype = type_of(schema, components) if schema else "—"
            lines += ["", f"**Respuesta {ok}:** {rtype}"]
            stack = [schema]
            while stack:
                s = stack.pop()
                n = ref_name(s)
                if n and n not in used and "properties" in components.get(n, {}):
                    used.add(n)
                    for fs in components[n]["properties"].values():
                        stack += [fs, fs.get("items", {})] + fs.get("anyOf", []) + fs.get("allOf", [])
                stack += s.get("anyOf", []) + ([s["items"]] if "items" in s else [])
        if info["errors"]:
            lines += ["", "**Errores**", "", "| HTTP | code | Cuándo |", "|---|---|---|"]
            for status, code, when in sorted(info["errors"], key=lambda e: e[0]):
                lines.append(f"| {status} | `{code}` | {when} |")
        elif method in ("GET", "POST") and "oneclick" in path:
            lines += ["", "**Errores:** no devuelve errores JSON; cualquier fallo redirige con `result=error`."]

    lines += ["", "## Esquemas de respuesta", ""]
    for name in sorted(used, key=show):
        lines += [f"### {show(name)}", ""]
        if components[name].get("description"):
            lines += [esc(components[name]["description"]), ""]
        lines += fields_table(name, components) + [""]
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"{OUT} · {len(ENDPOINTS)} endpoints · {len(used)} esquemas")


if __name__ == "__main__":
    main()
