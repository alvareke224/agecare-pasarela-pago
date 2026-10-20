"""Genera el diagrama y el diccionario del modelo físico de facturación desde un PostgreSQL real.

Uso (BD vacía con agecare_admin_ddl.sql + agecare_billing_ddl.sql aplicados):
    DOC_DATABASE_URL=postgresql://postgres:postgres@localhost/docdb python -m scripts.gen_db_doc

Salidas: docs/BD_FISICA.md · docs/bd_facturacion.dot · docs/bd_facturacion.svg/.png (si hay Graphviz).
Lee el catálogo de PostgreSQL (columnas, PK, FK, UNIQUE, CHECK, índices, triggers, RLS, comentarios):
lo que documenta es exactamente lo que crea el DDL.
"""
from __future__ import annotations

import asyncio
import html
import os
import shutil
import subprocess
from pathlib import Path

import asyncpg

DOCS = Path(__file__).resolve().parent.parent / "docs"
MAIN = ["billing_payment_methods", "billing_subscriptions", "billing_charges", "billing_refunds",
        "billing_events", "billing_subscription_transitions"]
HISTORY = ["billing_payment_methods_history", "billing_subscriptions_history", "billing_charges_history"]
EXTERNAL = {"tenants": "Tenant (país/empresa)", "plans": "Catálogo de planes", "admin_users": "Usuarios de la consola"}

Q_COLUMNS = """
select a.attname as col, format_type(a.atttypid, a.atttypmod) as type, a.attnotnull as notnull,
       pg_get_expr(d.adbin, d.adrelid) as default, col_description(a.attrelid, a.attnum) as comment
from pg_attribute a left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
where a.attrelid = $1::regclass and a.attnum > 0 and not a.attisdropped order by a.attnum"""
Q_CONSTRAINTS = """
select conname, contype::text as contype, pg_get_constraintdef(oid) as def,
       (select array_agg(attname order by attnum) from pg_attribute
         where attrelid = conrelid and attnum = any(conkey)) as cols,
       confrelid::regclass::text as ref
from pg_constraint where conrelid = $1::regclass order by contype, conname"""
Q_INDEXES = """
select i.relname as name, pg_get_indexdef(i.oid) as def, x.indisunique as uniq, x.indisprimary as pk
from pg_index x join pg_class i on i.oid = x.indexrelid
where x.indrelid = $1::regclass order by x.indisprimary desc, i.relname"""
Q_TRIGGERS = """
select t.tgname as name, p.proname as fn, pg_get_triggerdef(t.oid) as def
from pg_trigger t join pg_proc p on p.oid = t.tgfoid
where t.tgrelid = $1::regclass and not t.tgisinternal order by t.tgname"""
Q_TABLE = """
select obj_description($1::regclass, 'pg_class') as comment, c.relrowsecurity as rls, c.relforcerowsecurity as force_rls,
       (select string_agg(polname, ', ') from pg_policy where polrelid = c.oid) as policies
from pg_class c where c.oid = $1::regclass"""

TRIGGER_DOC = {
    "tg_set_updated_at": "Actualiza `updated_at` en cada UPDATE.",
    "tg_billing_subscription_transition": "Rechaza cambios de `status` que no estén en `billing_subscription_transitions`.",
    "tg_billing_charge_guard": "Orden de compra, monto, suscripción y comercio son inmutables; un cobro resuelto no vuelve a pending/unknown y uno rechazado no cambia de estado.",
    "tg_forbid_change": "Tabla inmutable: rechaza UPDATE y DELETE.",
    "tg_billing_refund_apply": "Bloquea el cobro, exige que esté aprobado, valida que la suma de reembolsos no supere lo cobrado y actualiza `refunded_amount` y el estado del cobro.",
    "tg_write_history": "Después de cada INSERT/UPDATE/DELETE guarda una copia de la fila en la tabla `*_history`, con los secretos en NULL.",
}


def short_type(t: str) -> str:
    return (t.replace("timestamp with time zone", "timestamptz").replace("character varying", "varchar")
            .replace("character", "char"))


async def describe(conn, table: str) -> dict:
    fq = f"admin.{table}"
    cols = [dict(r) for r in await conn.fetch(Q_COLUMNS, fq)]
    cons = [dict(r) for r in await conn.fetch(Q_CONSTRAINTS, fq)]
    idx = [dict(r) for r in await conn.fetch(Q_INDEXES, fq)]
    trg = [dict(r) for r in await conn.fetch(Q_TRIGGERS, fq)]
    meta = dict(await conn.fetchrow(Q_TABLE, fq))
    pk = next((c["cols"] for c in cons if c["contype"] == "p"), [])
    fks = {col: c["ref"].removeprefix("admin.") for c in cons if c["contype"] == "f" for col in c["cols"]}
    return {"name": table, "cols": cols, "cons": cons, "idx": idx, "trg": trg, "meta": meta, "pk": pk, "fks": fks}


def dot_graph(tables: list[dict]) -> str:
    lines = ['digraph billing {', '  graph [rankdir=LR, fontname="Helvetica", nodesep=0.5, ranksep=1.2, bgcolor="white", splines=true];',
             '  node [shape=plain, fontname="Helvetica", fontsize=10];', '  edge [color="#5b6b82", arrowsize=0.7, fontname="Helvetica", fontsize=9];']
    for t in tables:
        rows = [f'<tr><td bgcolor="#1f3b70" colspan="2"><font color="white"><b>{t["name"]}</b></font></td></tr>']
        for c in t["cols"]:
            key = "PK" if c["col"] in t["pk"] else ("FK" if c["col"] in t["fks"] else "")
            name = f'<b>{c["col"]}</b>' if key == "PK" else c["col"]
            mark = f'<font color="#b35c00">{key}</font> ' if key else ""
            rows.append(f'<tr><td align="left" port="{c["col"]}">{mark}{name}</td>'
                        f'<td align="left" port="{c["col"]}_r"><font color="#5b6b82">{html.escape(short_type(c["type"]))}'
                        f'{"" if c["notnull"] else "?"}</font></td></tr>')
        lines.append(f'  {t["name"]} [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="3">'
                     + "".join(rows) + "</table>>];")
    lines.append('  { rank=same; plans; admin_users; }')
    lines.append('  tenants_note [shape=note, fontsize=9, color="#9aa5b5", fontcolor="#5b6b82", '
                 'label="Todas las tablas: tenant_id → tenants (FK) + RLS por tenant"];')
    for name, label in EXTERNAL.items():
        if name == "tenants":
            continue
        lines.append(f'  {name} [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="#9aa5b5">'
                     f'<tr><td bgcolor="#e8ecf2"><b>{name}</b></td></tr><tr><td><font color="#5b6b82">{label}</font></td></tr></table>>];')
    for t in tables:
        for c in t["cons"]:
            if c["contype"] != "f" or c["cols"][0] == "tenant_id":
                continue   # todas tienen tenant_id → tenants: se indica en la leyenda, no se dibuja
            ref = c["ref"].removeprefix("admin.")
            col = c["cols"][0]
            if ref in EXTERNAL:
                lines.append(f'  {ref}:e -> {t["name"]}:{col}:w [arrowhead=crow, arrowtail=tee, dir=both, '
                             f'style=dashed, color="#9aa5b5"];')
            else:
                lines.append(f'  {ref}:id_r:e -> {t["name"]}:{col}:w [arrowhead=crow, arrowtail=tee, dir=both];')
    lines.append("}")
    return "\n".join(lines)


def md_table(t: dict) -> list[str]:
    out = [f"### {t['name']}", ""]
    if t["meta"]["comment"]:
        out += [t["meta"]["comment"], ""]
    out += ["| Columna | Tipo | Nulo | Por defecto | Clave | Descripción |", "|---|---|---|---|---|---|"]
    for c in t["cols"]:
        key = "PK" if c["col"] in t["pk"] else (f"FK → {t['fks'][c['col']]}" if c["col"] in t["fks"] else "")
        default = (c["default"] or "").replace("|", "\\|")
        out.append(f"| `{c['col']}` | {short_type(c['type'])} | {'no' if c['notnull'] else 'sí'} | "
                   f"{f'`{default}`' if default else ''} | {key} | {(c['comment'] or '').replace('|', '/')} |")
    checks = [c for c in t["cons"] if c["contype"] in ("c", "u", "f", "x")]
    if checks:
        out += ["", "**Restricciones**", "", "| Nombre | Tipo | Definición |", "|---|---|---|"]
        kinds = {"c": "CHECK", "u": "UNIQUE", "f": "FOREIGN KEY", "x": "EXCLUDE"}
        for c in checks:
            out.append(f"| `{c['conname']}` | {kinds[c['contype']]} | `{c['def'].replace('|', '\\|')}` |")
    if t["idx"]:
        out += ["", "**Índices**", "", "| Índice | Tipo | Definición |", "|---|---|---|"]
        for i in t["idx"]:
            kind = "PK" if i["pk"] else ("UNIQUE" if i["uniq"] else "índice")
            if " WHERE " in i["def"]:
                kind += " parcial"
            d = i["def"].split(" USING ", 1)[-1].replace("|", "\\|")
            out.append(f"| `{i['name']}` | {kind} | `USING {d}` |")
    if t["trg"]:
        out += ["", "**Triggers**", "", "| Trigger | Función | Qué hace |", "|---|---|---|"]
        for g in t["trg"]:
            doc = TRIGGER_DOC.get(g["fn"]) or TRIGGER_DOC.get(g["name"]) or ""
            out.append(f"| `{g['name']}` | `{g['fn']}()` | {doc} |")
    m = t["meta"]
    if m["rls"]:
        out += ["", f"**Seguridad por fila (RLS):** activa{' y forzada' if m['force_rls'] else ''} · "
                    f"políticas: {m['policies'] or '—'}."]
    return out + [""]


async def main() -> None:
    url = os.environ.get("DOC_DATABASE_URL", "postgresql://postgres:postgres@localhost/docdb")
    conn = await asyncpg.connect(url)
    try:
        main_t = [await describe(conn, n) for n in MAIN]
        hist_t = [await describe(conn, n) for n in HISTORY]
        version = await conn.fetchval("show server_version")
    finally:
        await conn.close()

    DOCS.mkdir(exist_ok=True)
    dot = dot_graph([t for t in main_t if t["name"] != "billing_subscription_transitions"])
    (DOCS / "bd_facturacion.dot").write_text(dot, encoding="utf-8")
    if shutil.which("dot"):
        for fmt in ("svg", "png"):
            args = ["dot", f"-T{fmt}", str(DOCS / "bd_facturacion.dot"), "-o", str(DOCS / f"bd_facturacion.{fmt}")]
            if fmt == "png":
                args.insert(1, "-Gdpi=160")
            subprocess.run(args, check=True)

    n_idx = sum(len(t["idx"]) for t in main_t + hist_t)
    lines = [
        "# Modelo físico de datos · Pasarela de pago y suscripciones",
        "",
        f"> Generado desde el catálogo de PostgreSQL {version} con `python -m scripts.gen_db_doc` "
        "después de aplicar `db/agecare_admin_ddl.sql` + `db/agecare_billing_ddl.sql`. "
        "Describe exactamente lo que crea el DDL.",
        "",
        "## Diagrama",
        "",
        "![Diagrama físico de facturación](bd_facturacion.png)",
        "",
        "Flechas: relación 1 → N (pata de gallo en el lado N). En gris punteado, tablas del resto de "
        "la consola (`plans`, `admin_users`); la FK `tenant_id → tenants` de todas las tablas no se dibuja "
        "para no recargar el diagrama. `?` = columna que admite NULL. "
        "Versión vectorial: `bd_facturacion.svg`; fuente Graphviz: `bd_facturacion.dot`. "
        "El modelo completo de la consola (55 tablas) está en `db/agecare_admin.dbml` "
        "(se importa en dbdiagram.io).",
        "",
        "## Resumen",
        "",
        "| Tabla | Propósito | Filas por… |",
        "|---|---|---|",
        "| `billing_payment_methods` | Tarjeta inscrita en Oneclick (tbk_user cifrado, tipo y últimos 4) | inscripción |",
        "| `billing_subscriptions` | Suscripción mensual a un plan, su estado y periodo pagado | beneficiario y plan |",
        "| `billing_charges` | Cada intento de cobro a Transbank | intento |",
        "| `billing_refunds` | Reembolsos pedidos desde la consola (inmutable) | reembolso |",
        "| `billing_events` | Bitácora inmutable del ciclo de vida | evento |",
        "| `billing_subscription_transitions` | Catálogo de cambios de estado permitidos | transición |",
        "| `*_history` | Copia de cada versión de la fila (auditoría) | cambio |",
        "",
        f"**Total:** {len(main_t) + len(hist_t)} tablas, {n_idx} índices. Todas en el esquema `admin`, "
        "con `tenant_id` y RLS por tenant (`admin.current_tenant_id()`).",
        "",
        "## Reglas que garantiza la base de datos",
        "",
        "- **Una tarjeta activa por usuario:** índice único parcial `billing_pm_one_active_uq` (`WHERE status = 'active'`).",
        "- **Una suscripción vigente por beneficiario:** `billing_sub_one_live_uq` (estados incomplete/active/past_due).",
        "- **Nunca dos cobros en curso para la misma suscripción:** `billing_charges_one_inflight_uq` "
        "(`WHERE status IN ('pending','unknown')`). Junto con `SELECT … FOR UPDATE SKIP LOCKED` en el job, evita el doble cobro.",
        "- **Órdenes de compra únicas** (`buy_order`, `detail_buy_order`): Transbank exige que no se repitan.",
        "- **Estados válidos:** el trigger de transición rechaza, por ejemplo, `canceled → active`.",
        "- **Montos coherentes:** CHECK `amount > 0` y `0 ≤ refunded_amount ≤ amount`; el estado `refunded` exige "
        "reembolso total y `partially_refunded` uno parcial; un cobro aprobado exige `response_code = 0` y código de "
        "autorización. El trigger `trg_refund_apply` valida la suma de reembolsos bajo bloqueo.",
        "- **Inmutabilidad:** `billing_refunds`, `billing_events` y las tablas `*_history` no admiten UPDATE ni DELETE.",
        "- **Datos sensibles:** no se guarda el número de tarjeta; `tbk_user` va cifrado (Fernet) y el historial lo deja en NULL.",
        "",
        "## Índices y para qué sirven",
        "",
        "| Índice | Consulta que acelera |",
        "|---|---|",
        "| `billing_sub_due_idx` (parcial) | Job: suscripciones a renovar o reintentar (`next_charge_at`) |",
        "| `billing_charges_unresolved_idx` (parcial) | Job: cobros `pending/unknown` por conciliar |",
        "| `billing_pm_pending_idx` (parcial) | Job: inscripciones abandonadas que expiran |",
        "| `billing_sub_status_idx`, `billing_sub_plan_idx` | Consola: filtros por estado/plan y KPIs (MRR, mezcla de planes) |",
        "| `billing_sub_search_idx` (GIN trigram) | Consola: búsqueda por correo o nombre |",
        "| `billing_charges_status_idx`, `billing_charges_created_idx` | Consola: listado de cobros y tasa de aprobación |",
        "| `billing_charges_sub_idx`, `billing_events_sub_idx` | Detalle de una suscripción (cobros y eventos) |",
        "| `billing_refunds_charge_idx` | Detalle de un cobro (reembolsos) |",
        "| `billing_pm_user_idx`, `billing_sub_user_idx` | App: `GET /me` del usuario |",
        "",
        "## Tablas",
        "",
    ]
    for t in main_t:
        lines += md_table(t)
    lines += ["## Tablas de historial", "",
              "El trigger `trg_history` (AFTER INSERT, UPDATE o DELETE) guarda una copia de cada versión de "
              "la fila: las columnas de la tabla original más `history_id`, `op` (I/U/D), `changed_at` y "
              "`changed_by` (actor de la sesión). Los secretos (`tbk_user_enc`, `inscription_token_hash`) "
              "existen como columna pero se guardan siempre en NULL. Son inmutables.", ""]
    for t in hist_t:
        lines += [f"- `{t['name']}` · {len(t['cols'])} columnas · índices: "
                  + ", ".join(f"`{i['name']}`" for i in t["idx"])]
    (DOCS / "BD_FISICA.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"docs/BD_FISICA.md · {len(main_t) + len(hist_t)} tablas · {n_idx} índices")


if __name__ == "__main__":
    asyncio.run(main())
