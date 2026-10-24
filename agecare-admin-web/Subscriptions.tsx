import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { billingAdmin } from "../../api/billing";
import { usePageTitle } from "../../components/Layout";
import { ErrorBox, Pager, PlanTag, SkeletonRows, SubscriptionChip } from "../../components/ui";
import { useLoad } from "../../components/useLoad";
import { date, money, relativeDays } from "../../format";

const STATUSES: [string, string][] = [
  ["", "Todas"], ["active", "Activas"], ["past_due", "En mora"], ["incomplete", "Pendientes"],
  ["canceled", "Canceladas"], ["expired", "Expiradas"],
];
const PLANS: [string, string][] = [["", "Todos los planes"], ["gold", "Dorado"], ["platinum", "Platino"], ["provider", "Proveedor"]];

export default function Subscriptions() {
  usePageTitle("Suscripciones", "Comercial · Facturación · Suscripciones");
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const status = params.get("status") ?? "";
  const plan = params.get("plan") ?? "";
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? 1);
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const { data, error, loading } = useLoad(
    () => billingAdmin.subscriptions({ status: status || undefined, plan: plan || undefined, q: q || undefined, page }),
    [status, plan, q, page]);

  const update = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    if (!("page" in patch)) next.delete("page");
    setParams(next);
  };

  return (
    <>
      <div className="filter-row">
        <span className="filter-label">Estado</span>
        <div className="seg" role="tablist" aria-label="Estado de la suscripción">
          {STATUSES.map(([v, l]) => (
            <button key={v} role="tab" aria-selected={status === v} className={status === v ? "on" : ""} onClick={() => update({ status: v })}>{l}</button>
          ))}
        </div>
        <select className="input" aria-label="Plan" value={plan} onChange={(e) => update({ plan: e.target.value })}>
          {PLANS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <form onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }} style={{ display: "flex", gap: 8, flex: "1 1 220px" }}>
          <input className="input" style={{ flex: 1 }} placeholder="Buscar por correo o nombre del pagador" value={search}
                 onChange={(e) => setSearch(e.target.value)} aria-label="Buscar" />
          <button className="btn">Buscar</button>
        </form>
      </div>

      <ErrorBox error={error} />
      <div className="card">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>Pagador</th><th>Plan</th><th>Estado</th><th className="num">Precio</th><th>Tarjeta</th><th>Próximo cobro</th><th>Desde</th></tr>
            </thead>
            {loading && !data ? <SkeletonRows cols={7} /> : (
              <tbody>
                {data?.items.map((s) => (
                  <tr key={s.id} className="click" tabIndex={0} onClick={() => navigate(`/facturacion/suscripciones/${s.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && navigate(`/facturacion/suscripciones/${s.id}`)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{s.user_name ?? "—"}</div>
                      <div className="muted" style={{ fontSize: 11.5 }}>{s.user_email}</div>
                    </td>
                    <td><PlanTag code={s.plan_code} name={s.plan_name} />{s.pending_plan_code && <div className="muted" style={{ fontSize: 11 }}>→ cambia a {s.pending_plan_code}</div>}</td>
                    <td><SubscriptionChip sub={s} />{s.status === "past_due" && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>reintento {s.retry_count}</div>}</td>
                    <td className="num">{money(s.price_amount, s.currency_code)}</td>
                    <td>{s.payment_method?.card_last4 ? <span className="soft">{s.payment_method.card_type} ···· {s.payment_method.card_last4}</span> : <span className="muted">—</span>}</td>
                    <td>{s.next_charge_at ? <>{date(s.next_charge_at)}<div className="muted" style={{ fontSize: 11 }}>{relativeDays(s.next_charge_at)}</div></> : <span className="muted">—</span>}</td>
                    <td className="soft">{date(s.started_at ?? s.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
          {data && data.items.length === 0 && <div className="empty">No hay suscripciones con estos filtros.</div>}
        </div>
        {data && <Pager page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => update({ page: String(p) })} />}
      </div>
    </>
  );
}
