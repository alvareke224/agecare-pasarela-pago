import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { billingAdmin } from "../../api/billing";
import { usePageTitle } from "../../components/Layout";
import { ChargeChip, ErrorBox, KIND_NAMES, Pager, SkeletonRows } from "../../components/ui";
import { useLoad } from "../../components/useLoad";
import { dateTime, money } from "../../format";

const STATUSES: [string, string][] = [
  ["", "Todos"], ["authorized", "Aprobados"], ["failed", "Rechazados"], ["unknown", "Por conciliar"],
  ["partially_refunded", "Reembolso parcial"], ["refunded", "Reembolsados"],
];

export default function Charges() {
  usePageTitle("Cobros y reembolsos", "Comercial · Facturación · Transacciones Oneclick Mall");
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? 1);
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);
  const { data, error, loading } = useLoad(
    () => billingAdmin.charges({ status: status || undefined, q: q || undefined, page }), [status, q, page]);

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
        <div className="seg" role="tablist" aria-label="Estado del cobro">
          {STATUSES.map(([v, l]) => (
            <button key={v} role="tab" aria-selected={status === v} className={status === v ? "on" : ""} onClick={() => update({ status: v })}>{l}</button>
          ))}
        </div>
        <form onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }} style={{ display: "flex", gap: 8, flex: "1 1 220px" }}>
          <input className="input" style={{ flex: 1 }} placeholder="Orden de compra o correo" value={search}
                 onChange={(e) => setSearch(e.target.value)} aria-label="Buscar" />
          <button className="btn">Buscar</button>
        </form>
      </div>
      <ErrorBox error={error} />
      <div className="card">
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Fecha</th><th>Orden de compra</th><th>Pagador</th><th>Tipo</th><th className="num">Monto</th><th>Estado</th><th>Detalle</th></tr></thead>
            {loading && !data ? <SkeletonRows cols={7} /> : (
              <tbody>
                {data?.items.map((c) => (
                  <tr key={c.id} className="click" tabIndex={0} onClick={() => navigate(`/facturacion/cobros/${c.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && navigate(`/facturacion/cobros/${c.id}`)}>
                    <td className="soft">{dateTime(c.created_at)}</td>
                    <td className="mono">{c.buy_order}</td>
                    <td>{c.user_email}</td>
                    <td>{KIND_NAMES[c.kind]}</td>
                    <td className="num">{money(c.amount, c.currency_code)}{c.refunded_amount > 0 && <div className="muted" style={{ fontSize: 11 }}>−{money(c.refunded_amount)}</div>}</td>
                    <td><ChargeChip status={c.status} /></td>
                    <td className="soft" style={{ fontSize: 12 }}>{c.status === "failed" ? c.failure_message : c.authorization_code ? `Aut. ${c.authorization_code} · ···· ${c.card_last4 ?? ""}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
          {data && data.items.length === 0 && <div className="empty">No hay cobros con estos filtros.</div>}
        </div>
        {data && <Pager page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => update({ page: String(p) })} />}
      </div>
    </>
  );
}
