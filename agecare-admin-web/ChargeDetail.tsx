import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { billingAdmin, type AdminCharge } from "../../api/billing";
import { useAuth } from "../../auth/AuthContext";
import { usePageTitle } from "../../components/Layout";
import { ChargeChip, ErrorBox, KIND_NAMES, Modal } from "../../components/ui";
import { useLoad } from "../../components/useLoad";
import { date, dateTime, money } from "../../format";

export default function ChargeDetail() {
  const { id = "" } = useParams();
  usePageTitle("Detalle de cobro", "Comercial · Facturación · Cobros");
  const { can } = useAuth();
  const { data, error, loading, reload } = useLoad(() => billingAdmin.charge(id), [id]);
  const [open, setOpen] = useState(false);
  const [cbOpen, setCbOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <div className="card"><div className="skeleton" style={{ height: 160 }} /></div>;
  if (!data) return null;
  const c = data.charge;

  return (
    <>
      <div className="tb-crumb" style={{ marginBottom: 12 }}>
        <Link to="/facturacion/cobros">← Cobros</Link> · <Link to={`/facturacion/suscripciones/${c.subscription_id}`}>Ver suscripción</Link>
      </div>
      {notice && <div className="banner ok" role="status"><span className="b-ico">✔</span><div className="b-t">{notice}</div></div>}
      <div className="grid g-detail">
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title" style={{ fontSize: 16 }}>{money(c.amount, c.currency_code)} · {KIND_NAMES[c.kind]}</div>
              <div className="card-sub">Orden de compra <span className="mono">{c.buy_order}</span></div>
            </div>
            <ChargeChip status={c.status} />
          </div>
          <dl className="dl">
            <dt>Pagador</dt><dd>{c.user_email}</dd>
            <dt>Periodo que paga</dt><dd>{date(c.period_start)} → {date(c.period_end)}</dd>
            <dt>Intento</dt><dd>#{c.attempt}</dd>
            <dt>Fecha Transbank</dt><dd>{dateTime(c.transaction_date)}</dd>
            <dt>Código de autorización</dt><dd className="mono">{c.authorization_code ?? "—"}</dd>
            <dt>Tarjeta</dt><dd>{c.card_last4 ? `···· ${c.card_last4}` : "—"}</dd>
            <dt>Código de respuesta</dt><dd>{c.response_code ?? "—"}{c.failure_code && <span className="muted"> · {c.failure_code}</span>}</dd>
            {c.failure_message && <><dt>Motivo</dt><dd>{c.failure_message}</dd></>}
            <dt>Comercio · orden hija</dt><dd className="mono">{c.commerce_code} · {c.detail_buy_order}</dd>
            <dt>Reembolsado</dt><dd>{money(c.refunded_amount)} <span className="muted">· disponible {money(data.refundable_amount)}</span></dd>
          </dl>
          <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
            <button className="btn sm" onClick={() => setShowRaw((v) => !v)}>{showRaw ? "Ocultar" : "Ver"} respuesta de Transbank</button>
            {can("billing_refunds") && ["authorized", "partially_refunded", "refunded"].includes(c.status) &&
              <button className="btn sm danger" onClick={() => setCbOpen(true)}>⚠ Registrar contracargo</button>}
          </div>
          {showRaw && <pre className="mono" style={{ marginTop: 10, background: "#f6f8fb", padding: 12, borderRadius: 10, overflowX: "auto" }}>{JSON.stringify(data.gateway_response, null, 2)}</pre>}
        </div>

        <div className="card">
          <div className="card-head">
            <div><div className="card-title">Reembolsos</div><div className="card-sub">Reversa (mismo día, total) o anulación</div></div>
            {can("billing_refunds") && data.refundable_amount > 0 && <button className="btn" onClick={() => setOpen(true)}>↩ Reembolsar</button>}
          </div>
          {!can("billing_refunds") && <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>Solo el rol Administrador puede reembolsar.</div>}
          <ul className="timeline">
            {data.refunds.map((r) => (
              <li key={r.id}>
                <span className={`dot ${r.status === "succeeded" ? "ok" : "crit"}`} aria-hidden="true" />
                <div>
                  <div className="tl-t">{money(r.amount)} · {r.status === "succeeded" ? (r.refund_type === "REVERSED" ? "Reversado" : "Anulado") : "Fallido"}</div>
                  <div className="tl-s">{dateTime(r.created_at)} · {r.requested_by_name ?? "—"} · “{r.reason}”</div>
                  {r.failure_message && <div className="tl-s" style={{ color: "var(--crit)" }}>{r.failure_message}</div>}
                </div>
              </li>
            ))}
          </ul>
          {data.refunds.length === 0 && <div className="empty">Sin reembolsos.</div>}
        </div>
      </div>
      {cbOpen && <ChargebackModal charge={c} onClose={() => setCbOpen(false)}
                                  onDone={(m) => { setCbOpen(false); setNotice(m); reload(); }} />}
      {open && <RefundModal charge={c} max={data.refundable_amount} onClose={() => setOpen(false)}
                            onDone={(m) => { setOpen(false); setNotice(m); reload(); }} />}
    </>
  );
}

function RefundModal({ charge, max, onClose, onDone }: { charge: AdminCharge; max: number; onClose: () => void; onDone: (m: string) => void }) {
  const [amount, setAmount] = useState(max);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await billingAdmin.refund(charge.id, { amount, reason });
      onDone(`Reembolso de ${money(r.amount)} aplicado en Transbank (${r.refund_type === "REVERSED" ? "reversa" : "anulación"}).`);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <Modal title="Reembolsar cobro" sub={`Orden ${charge.buy_order} de ${charge.user_email}. La devolución se hace a la misma tarjeta.`} onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="amount">Monto (CLP)</label>
          <input id="amount" className="input" type="number" min={1} max={max} step={1} required value={amount}
                 onChange={(e) => setAmount(Number(e.target.value))} />
          <span className="hint">Máximo {money(max)}. Un reembolso total el mismo día es una reversa; si no, una anulación.</span>
        </div>
        <div className="field">
          <label htmlFor="rreason">Motivo (queda en la auditoría)</label>
          <textarea id="rreason" className="input" required minLength={5} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          <span>Confirmo devolver {money(amount)}. Esta operación no se puede deshacer.</span></label>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Volver</button>
          <button className="btn danger solid" disabled={busy || !confirm || reason.trim().length < 5 || amount < 1 || amount > max}>
            {busy ? "Procesando…" : "Reembolsar"}
          </button>
        </div>
      </form>
    </Modal>
  );
}


function ChargebackModal({ charge, onClose, onDone }: { charge: AdminCharge; onClose: () => void; onDone: (m: string) => void }) {
  const [note, setNote] = useState("");
  const [ref, setRef] = useState("");
  const [cancel, setCancel] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await billingAdmin.chargeback(charge.id, { note, tbk_reference: ref.trim() || undefined, cancel_subscription: cancel });
      onDone(r.already_registered ? "Este contracargo ya estaba registrado; no se hizo ningún cambio."
        : `Contracargo registrado. Suscripción: ${r.subscription.status === "canceled" ? "cancelada" : "sin cambios"}.`);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <Modal title="Registrar contracargo" sub={`Transbank informa los contracargos en su portal de comercios. Orden ${charge.buy_order}.`} onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="cbref">Referencia de Transbank (opcional)</label>
          <input id="cbref" className="input" maxLength={60} value={ref} onChange={(e) => setRef(e.target.value)} placeholder="N.° de disputa" />
        </div>
        <div className="field">
          <label htmlFor="cbnote">Detalle (queda en la auditoría)</label>
          <textarea id="cbnote" className="input" required minLength={5} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <label className="check"><input type="checkbox" checked={cancel} onChange={(e) => setCancel(e.target.checked)} />
          <span>Cancelar la suscripción de inmediato (motivo fraude o contracargo)</span></label>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Volver</button>
          <button className="btn danger solid" disabled={busy || note.trim().length < 5}>{busy ? "Registrando…" : "Registrar"}</button>
        </div>
      </form>
    </Modal>
  );
}
