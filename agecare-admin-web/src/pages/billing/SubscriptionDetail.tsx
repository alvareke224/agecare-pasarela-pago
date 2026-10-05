import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { billingAdmin, type AdminSubscription, type BillingEvent } from "../../api/billing";
import { useAuth } from "../../auth/AuthContext";
import { usePageTitle } from "../../components/Layout";
import { CANCEL_REASONS, ChargeChip, ErrorBox, KIND_NAMES, Modal, PlanTag, SubscriptionChip } from "../../components/ui";
import { useLoad } from "../../components/useLoad";
import { date, dateTime, money, relativeDays } from "../../format";

const EVENT_TEXT: Record<string, [string, string]> = {
  "subscription.created": ["brand", "Suscripción creada"],
  "subscription.activated": ["ok", "Suscripción activada"],
  "subscription.renewed": ["ok", "Renovada"],
  "subscription.recovered": ["ok", "Recuperada tras la mora"],
  "subscription.past_due": ["warn", "Pasó a mora"],
  "subscription.canceled": ["crit", "Cancelada"],
  "subscription.cancel_scheduled": ["warn", "Cancelación programada"],
  "subscription.resumed": ["brand", "Cancelación revertida"],
  "subscription.expired": ["", "Expiró sin pago"],
  "subscription.extended": ["brand", "Periodo extendido"],
  "subscription.plan_change_scheduled": ["brand", "Cambio de plan programado"],
  "subscription.plan_changed": ["brand", "Plan cambiado"],
  "charge.succeeded": ["ok", "Cobro aprobado"],
  "charge.failed": ["crit", "Cobro rechazado"],
  "charge.unknown": ["warn", "Cobro sin respuesta de Transbank"],
  "charge.refunded": ["", "Reembolso"],
  "charge.skipped": ["warn", "Cobro omitido: sin tarjeta activa"],
};

const PM_STATUS: Record<string, string> = {
  active: "activa", removed: "eliminada", pending: "inscripción en curso", failed: "rechazada", aborted: "anulada",
};

function eventDetail(e: BillingEvent): string {
  const p = e.payload as Record<string, string | number | undefined>;
  const parts: string[] = [];
  if (p.amount !== undefined) parts.push(money(Number(p.amount)));
  if (p.failure_code) parts.push(String(p.failure_code));
  if (p.reason) parts.push(CANCEL_REASONS[String(p.reason)] ?? String(p.reason));
  if (p.retry_count) parts.push(`reintento ${p.retry_count}`);
  if (p.days) parts.push(`+${p.days} días`);
  if (p.to_plan) parts.push(`→ ${p.to_plan}`);
  if (p.note) parts.push(`“${p.note}”`);
  parts.push(e.actor_type === "admin" ? "por staff" : e.actor_type === "user" ? "por el usuario" : "automático");
  return parts.join(" · ");
}

export default function SubscriptionDetail() {
  const { id = "" } = useParams();
  usePageTitle("Detalle de suscripción", "Comercial · Facturación · Suscripciones");
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad(() => billingAdmin.subscription(id), [id]);
  const [modal, setModal] = useState<null | "cancel" | "extend">(null);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <div className="card"><div className="skeleton" style={{ height: 160 }} /></div>;
  if (!data) return null;
  const s = data.subscription;
  const writable = can("billing.write");
  const live = s.status === "active" || s.status === "past_due" || s.status === "incomplete";

  async function retry() {
    setRetrying(true);
    setActionErr(null);
    try {
      const c = await billingAdmin.retry(s.id);
      setNotice(c.status === "authorized" ? `Cobro aprobado (${c.buy_order}). La suscripción quedó activa.`
        : c.status === "failed" ? `Transbank volvió a rechazar el cobro: ${(c.failure_message ?? c.failure_code ?? "").replace(/\.$/, "")}.`
        : "Transbank no respondió; el cobro se conciliará automáticamente.");
      reload();
    } catch (e) {
      setActionErr(e);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <>
      <div className="tb-crumb" style={{ marginBottom: 12 }}><Link to="/facturacion/suscripciones">← Volver a suscripciones</Link></div>
      {notice && <div className="banner info" role="status"><div className="b-t" style={{ fontWeight: 600 }}>{notice}</div></div>}
      <ErrorBox error={actionErr} />

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title" style={{ fontSize: 16 }}>{s.user_name ?? s.user_email}</div>
            <div className="card-sub">{s.user_email} · usuario <span className="mono">{s.user_id}</span></div>
          </div>
          <div className="actions">
            <SubscriptionChip sub={s} />
          </div>
        </div>
        {writable && live && (
          <div className="actions" style={{ marginBottom: 16 }}>
            {s.status === "past_due" && <button className="btn primary" onClick={retry} disabled={retrying}>{retrying ? "Cobrando…" : "↻ Reintentar cobro ahora"}</button>}
            {s.status === "active" && <button className="btn" onClick={() => setModal("extend")}>＋ Extender periodo</button>}
            <button className="btn danger" onClick={() => setModal("cancel")}>Cancelar suscripción</button>
          </div>
        )}
        <dl className="dl">
          <dt>Plan</dt>
          <dd><PlanTag code={s.plan_code} name={s.plan_name} /> · {money(s.price_amount, s.currency_code)} / mes
            {s.pending_plan_code && <span className="chip brand" style={{ marginLeft: 8 }}>Cambia a {s.pending_plan_code} en la renovación</span>}</dd>
          <dt>Beneficiario</dt><dd>{{ user: "El propio usuario", family: "Familia", provider: "Proveedor" }[s.beneficiary_type] ?? s.beneficiary_type} · <span className="mono">{s.beneficiary_id}</span></dd>
          <dt>Periodo pagado</dt><dd>{s.current_period_start ? `${date(s.current_period_start)} → ${date(s.current_period_end)}` : "—"}</dd>
          <dt>Próximo cobro</dt><dd>{s.next_charge_at ? `${dateTime(s.next_charge_at)} (${relativeDays(s.next_charge_at)})` : "—"}{s.status === "past_due" && ` · reintento ${s.retry_count}`}</dd>
          <dt>Tarjeta</dt>
          <dd>{s.payment_method ? <>{s.payment_method.card_type ?? "—"} ···· {s.payment_method.card_last4 ?? "—"} <span className="muted">· {PM_STATUS[s.payment_method.status] ?? s.payment_method.status}{s.payment_method.failure_code ? ` · ${s.payment_method.failure_code}` : ""}</span></> : "Sin tarjeta inscrita"}</dd>
          <dt>Cliente desde</dt><dd>{dateTime(s.started_at)}</dd>
          {(s.canceled_at || s.cancel_at_period_end) && <><dt>Cancelación</dt><dd>{CANCEL_REASONS[s.cancel_reason ?? ""] ?? "—"}{s.canceled_at ? ` · ${dateTime(s.canceled_at)}` : " · al fin del periodo"}{s.cancel_note && <div className="muted">“{s.cancel_note}”</div>}</dd></>}
        </dl>
      </div>

      <div className="grid g-detail">
        <div className="card">
          <div className="card-head"><div><div className="card-title">Cobros</div><div className="card-sub">Cada intento enviado a Transbank</div></div></div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Fecha</th><th>Tipo</th><th>Orden de compra</th><th className="num">Monto</th><th>Estado</th></tr></thead>
              <tbody>
                {data.charges.map((c) => (
                  <tr key={c.id}>
                    <td className="soft">{dateTime(c.created_at)}</td>
                    <td>{KIND_NAMES[c.kind]}{c.attempt > 1 && <span className="muted"> · #{c.attempt}</span>}</td>
                    <td><Link className="mono" to={`/facturacion/cobros/${c.id}`}>{c.buy_order}</Link></td>
                    <td className="num">{money(c.amount, c.currency_code)}</td>
                    <td><ChargeChip status={c.status} />{c.failure_code && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{c.failure_code}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.charges.length === 0 && <div className="empty">Aún no hay cobros.</div>}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><div><div className="card-title">Línea de tiempo</div><div className="card-sub">Eventos de facturación, más recientes primero</div></div></div>
          <ul className="timeline">
            {data.events.map((e) => {
              const [cls, text] = EVENT_TEXT[e.type] ?? ["", e.type];
              return (
                <li key={e.id}>
                  <span className={`dot ${cls}`} aria-hidden="true" />
                  <div><div className="tl-t">{text}</div><div className="tl-s">{dateTime(e.created_at)} · {eventDetail(e)}</div></div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      {modal === "cancel" && <CancelModal sub={s} onClose={() => setModal(null)} onDone={(msg) => { setModal(null); setNotice(msg); reload(); }} />}
      {modal === "extend" && <ExtendModal sub={s} onClose={() => setModal(null)} onDone={(msg) => { setModal(null); setNotice(msg); reload(); }} />}
    </>
  );
}

function CancelModal({ sub, onClose, onDone }: { sub: AdminSubscription; onClose: () => void; onDone: (m: string) => void }) {
  const [immediate, setImmediate] = useState(sub.status !== "active");
  const [reason, setReason] = useState("admin");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await billingAdmin.cancel(sub.id, { immediate, reason, note });
      onDone(r.status === "canceled" ? "Suscripción cancelada. El usuario vuelve al plan Gratuito." : `La suscripción terminará el ${date(r.current_period_end)}.`);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <Modal title="Cancelar suscripción" sub={`${sub.plan_name} de ${sub.user_email}. No se emite reembolso automático.`} onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        {sub.status === "active" && (
          <label className="check"><input type="checkbox" checked={immediate} onChange={(e) => setImmediate(e.target.checked)} />
            <span>Cancelar de inmediato<br /><span className="muted" style={{ fontSize: 12 }}>Si no se marca, mantiene el acceso hasta el {date(sub.current_period_end)}.</span></span></label>
        )}
        <div className="field">
          <label htmlFor="reason">Motivo</label>
          <select id="reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="admin">Decisión de AgeCare</option>
            <option value="user_request">Solicitud del usuario (por soporte)</option>
            <option value="fraud">Fraude o contracargo</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="note">Nota (queda en la auditoría)</label>
          <textarea id="note" className="input" required minLength={5} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ej.: El usuario lo pidió por teléfono, ticket #1482" />
        </div>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Volver</button>
          <button className="btn danger solid" disabled={busy || note.trim().length < 5}>{busy ? "Cancelando…" : "Confirmar cancelación"}</button>
        </div>
      </form>
    </Modal>
  );
}

function ExtendModal({ sub, onClose, onDone }: { sub: AdminSubscription; onClose: () => void; onDone: (m: string) => void }) {
  const [days, setDays] = useState(7);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await billingAdmin.extend(sub.id, { days, reason });
      onDone(`Periodo extendido hasta el ${date(r.current_period_end)}. El próximo cobro se movió a esa fecha.`);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <Modal title="Extender periodo sin cobro" sub="Compensación de soporte: corre la fecha del próximo cobro." onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="days">Días</label>
          <input id="days" className="input" type="number" min={1} max={90} required value={days} onChange={(e) => setDays(Number(e.target.value))} />
          <span className="hint">Hoy termina el {date(sub.current_period_end)}.</span>
        </div>
        <div className="field">
          <label htmlFor="ereason">Motivo</label>
          <textarea id="ereason" className="input" required minLength={5} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej.: Compensación por el incidente de notificaciones del 19 ago" />
        </div>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Volver</button>
          <button className="btn primary" disabled={busy || reason.trim().length < 5}>{busy ? "Guardando…" : "Extender"}</button>
        </div>
      </form>
    </Modal>
  );
}
