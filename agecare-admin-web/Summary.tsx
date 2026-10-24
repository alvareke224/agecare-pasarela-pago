import { useState } from "react";
import { Link } from "react-router-dom";
import { billingAdmin, type CycleStats } from "../../api/billing";
import { useAuth } from "../../auth/AuthContext";
import { usePageTitle } from "../../components/Layout";
import { ErrorBox, Kpi, PlanTag } from "../../components/ui";
import { useLoad } from "../../components/useLoad";
import { dateTime, money, num, pct } from "../../format";

export default function Summary() {
  usePageTitle("Resumen de facturación", "Comercial · Pasarela de pago Transbank Oneclick y suscripciones");
  const { can } = useAuth();
  const { data: s, error, loading, reload } = useLoad(() => billingAdmin.summary(), []);
  const [cycle, setCycle] = useState<CycleStats | null>(null);
  const [cycleErr, setCycleErr] = useState<unknown>(null);
  const [running, setRunning] = useState(false);

  async function runCycle() {
    setRunning(true);
    setCycleErr(null);
    try {
      setCycle(await billingAdmin.runCycle());
      reload();
    } catch (e) {
      setCycleErr(e);
    } finally {
      setRunning(false);
    }
  }

  if (error) return <ErrorBox error={error} />;
  if (loading && !s) return <div className="kpis">{Array.from({ length: 6 }, (_, i) => <div className="kpi" key={i}><div className="skeleton" style={{ height: 44 }} /></div>)}</div>;
  if (!s) return null;

  const totalSubs = s.plans.reduce((a, p) => a + p.active + p.past_due, 0);
  return (
    <>
      {s.gateway === "fake" && (
        <div className="banner info">
          <span className="b-ico" aria-hidden="true">🧪</span>
          <div>
            <div className="b-t">Pasarela en modo simulador</div>
            <div className="b-s">Los cobros los responde el simulador local de Webpay. Configura ADMIN_BILLING_GATEWAY=transbank para usar Transbank.</div>
          </div>
        </div>
      )}
      {s.gateway !== "fake" && (
        <div className="banner ok">
          <span className="b-ico" aria-hidden="true">✔</span>
          <div>
            <div className="b-t">Conectado a Transbank Oneclick Mall</div>
            <div className="b-s">Las tarjetas nuevas se inscriben y cobran en Transbank. Las suscripciones de demostración siguen en el simulador.</div>
          </div>
        </div>
      )}
      {s.unresolved_charges > 0 && (
        <div className="banner warn">
          <span className="b-ico" aria-hidden="true">◌</span>
          <div>
            <div className="b-t">{s.unresolved_charges} cobro(s) por conciliar con Transbank</div>
            <div className="b-s">Transbank no respondió a tiempo. El job los verifica automáticamente; no reintentes manualmente.</div>
          </div>
          <div className="spacer" />
          <Link className="btn sm" to="/facturacion/cobros?status=unknown">Ver cobros</Link>
        </div>
      )}

      <div className="kpis">
        <Kpi label="MRR" value={money(s.mrr)} delta={`${num(totalSubs)} suscripciones que pagan`} note="Ingreso mensual recurrente (CLP)" />
        <Kpi label="Suscripciones activas" value={num(s.subscriptions.active)}
             delta={`${num(s.subscriptions.cancel_scheduled)} con cancelación programada`} />
        <Kpi label="En mora" value={num(s.subscriptions.past_due)} deltaCls={s.subscriptions.past_due ? "down" : ""}
             delta="en reintentos de cobro" note="Mantienen acceso durante la gracia" />
        <Kpi label="Aprobación de cobros · 30 d" value={pct(s.charges_30d.approval_rate)}
             delta={`${num(s.charges_30d.approved)} aprobados · ${num(s.charges_30d.failed)} rechazados`}
             deltaCls={(s.charges_30d.approval_rate ?? 1) < 0.9 ? "down" : ""} />
        <Kpi label="Ingresos netos · 30 d" value={money(s.charges_30d.net_revenue)}
             delta={`${money(s.charges_30d.refunded)} reembolsado`} />
        <Kpi label="Renovaciones · 7 d" value={num(s.upcoming_7d.count)} delta={`${money(s.upcoming_7d.amount)} por cobrar`} />
      </div>

      <div className="grid g2">
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Suscripciones por plan</div>
              <div className="card-sub">Activas y en mora · aportan al MRR</div>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Plan</th><th className="num">Activas</th><th className="num">En mora</th><th className="num">MRR</th><th className="num">Cuota MRR</th></tr></thead>
              <tbody>
                {s.plans.map((p) => (
                  <tr key={p.plan_code}>
                    <td><PlanTag code={p.plan_code} name={p.plan_name} /></td>
                    <td className="num">{num(p.active)}</td>
                    <td className="num">{p.past_due ? <span className="chip warn">▲ {p.past_due}</span> : 0}</td>
                    <td className="num">{money(p.mrr)}</td>
                    <td className="num">{pct(s.mrr ? p.mrr / s.mrr : 0)}</td>
                  </tr>
                ))}
                <tr style={{ fontWeight: 700 }}>
                  <td>Total</td>
                  <td className="num">{num(s.subscriptions.active)}</td>
                  <td className="num">{num(s.subscriptions.past_due)}</td>
                  <td className="num">{money(s.mrr)}</td>
                  <td className="num">100%</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Movimiento · últimos 30 días</div>
              <div className="card-sub">Altas, bajas y suscripciones pendientes</div>
            </div>
          </div>
          <dl className="dl">
            <dt>Nuevas suscripciones</dt><dd>{num(s.subscriptions.started_30d)}</dd>
            <dt>Canceladas</dt><dd>{num(s.subscriptions.canceled_30d)}</dd>
            <dt>Pendientes de pago</dt><dd>{num(s.subscriptions.incomplete)} <span className="muted">· expiran a las 24 h sin pago</span></dd>
            <dt>Ingresos brutos</dt><dd>{money(s.charges_30d.gross_revenue)}</dd>
            <dt>Reembolsos</dt><dd>{money(s.charges_30d.refunded)}</dd>
            <dt>Actualizado</dt><dd>{dateTime(s.computed_at)}</dd>
          </dl>
          <div className="actions" style={{ marginTop: 16 }}>
            <Link className="btn sm" to="/facturacion/suscripciones?status=past_due">Ver morosas</Link>
            <Link className="btn sm" to="/facturacion/cobros?status=failed">Ver rechazos</Link>
          </div>
        </div>
      </div>

      {can("billing_refunds") && (
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Ciclo de facturación</div>
              <div className="card-sub">Concilia cobros pendientes, renueva, reintenta y expira. En Vercel corre solo una vez al día (04:00 en Chile); en Docker, cada 15 minutos.</div>
            </div>
            <button className="btn" onClick={runCycle} disabled={running}>{running ? "Ejecutando…" : "▶ Ejecutar ahora"}</button>
          </div>
          <ErrorBox error={cycleErr} />
          {cycle && (
            <div className="soft" style={{ fontSize: 12.5 }}>
              Renovadas {cycle.renewed} · recuperadas {cycle.recovered} · rechazadas {cycle.failed} · por conciliar {cycle.unknown} ·
              conciliadas {cycle.reconciled} · canceladas {cycle.canceled} · expiradas {cycle.expired_subscriptions}
              {cycle.errors.length > 0 && <div className="error-box" style={{ marginTop: 8 }}>{cycle.errors.join(" · ")}</div>}
            </div>
          )}
        </div>
      )}
    </>
  );
}
