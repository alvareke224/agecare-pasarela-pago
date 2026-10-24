// Checkout de prueba: reproduce lo que hará la app móvil con /api/v1/billing.
// El token de usuario de la app lo emite la API principal; en dev: python -m scripts.app_token --email …
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { billingAdmin, billingApp, redirectToWebpay, type MeOut, type PlanCode, type PlanOffer } from "../../api/billing";
import { usePageTitle } from "../../components/Layout";
import { ErrorBox, PlanTag, SubscriptionChip } from "../../components/ui";
import { date, money } from "../../format";

const TOKEN_KEY = "agecare.checkout.appToken";
const readToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } };
const saveToken = (t: string) => { try { sessionStorage.setItem(TOKEN_KEY, t); } catch { /* sin almacenamiento */ } };
const UNIT: Record<string, string> = { user: "por usuario", family: "por familia", provider: "por proveedor" };

const RESULTS: Record<string, [string, string, string]> = {
  success: ["ok", "✔", "Tarjeta inscrita y pago aprobado. La suscripción está activa."],
  charge_failed: ["crit", "✕", "La tarjeta se inscribió, pero el emisor rechazó el pago. Prueba con otra tarjeta."],
  charge_pending: ["warn", "◌", "Transbank no respondió a tiempo. Confirmaremos el pago en unos minutos."],
  rejected: ["crit", "✕", "Webpay rechazó la inscripción de la tarjeta."],
  aborted: ["warn", "▲", "Anulaste la inscripción en Webpay. No se hizo ningún cobro."],
  error: ["crit", "✕", "No pudimos confirmar la inscripción. Intenta nuevamente."],
};

export default function Checkout() {
  usePageTitle("Checkout de prueba", "Comercial · Facturación · Flujo de la app con Webpay Oneclick");
  const [params] = useSearchParams();
  const result = params.get("result");
  const [token, setToken] = useState(readToken);
  const [plans, setPlans] = useState<PlanOffer[]>([]);
  const [plan, setPlan] = useState<PlanCode>("gold");
  const [beneficiary, setBeneficiary] = useState("");
  const [me, setMe] = useState<MeOut | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<{ email: string; family_id: string; provider_id: string } | null>(null);
  const [gateway, setGateway] = useState<string | null>(null);

  async function newDemoUser() {
    setErr(null);
    try {
      const r = await billingAdmin.devAppToken();
      setDemo(r);
      setBeneficiary("");
      setToken(r.token);
    } catch (e) { setErr(e); }
  }

  useEffect(() => { billingApp.plans().then((r) => setPlans(r.plans)).catch(setErr); }, []);
  useEffect(() => { billingAdmin.summary().then((s) => setGateway(s.gateway)).catch(() => setGateway(null)); }, []);
  useEffect(() => {
    if (!token) return;
    saveToken(token);
    billingApp.me(token).then(setMe).catch((e) => { setMe(null); setErr(e); });
  }, [token, result]);

  async function subscribe() {
    setBusy(true);
    setErr(null);
    try {
      const r = await billingApp.subscribe(token, plan, beneficiary.trim() || undefined);
      if (r.next_action === "redirect_to_webpay" && r.inscription) redirectToWebpay(r.inscription.url_webpay, r.inscription.token);
      else setMe(await billingApp.me(token));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }

  async function addCard() {
    setBusy(true);
    try { const r = await billingApp.addCard(token); redirectToWebpay(r.url_webpay, r.token); } catch (e) { setErr(e); setBusy(false); }
  }

  async function toggleCancel(id: string, scheduled: boolean) {
    try {
      await (scheduled ? billingApp.resume(token, id) : billingApp.cancel(token, id));
      setMe(await billingApp.me(token));
    } catch (e) { setErr(e); }
  }

  const res = result ? RESULTS[result] ?? RESULTS.error : null;
  return (
    <>
      {res && (
        <div className={`banner ${res[0]}`} role="status">
          <span className="b-ico" aria-hidden="true">{res[1]}</span>
          <div><div className="b-t">{res[2]}</div>{params.get("card") && <div className="b-s">Tarjeta ···· {params.get("card")}</div>}</div>
          <div className="spacer" />
          {params.get("subscription_id") && <Link className="btn sm" to={`/facturacion/suscripciones/${params.get("subscription_id")}`}>Ver en la consola</Link>}
        </div>
      )}
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">1 · Usuario de la app</div>
            <div className="card-sub">Crea un usuario ficticio (con una familia y un proveedor) o pega un JWT de la app.</div>
          </div>
          <button className="btn primary" onClick={newDemoUser}>＋ Generar usuario de prueba</button>
        </div>
        {demo && <div className="soft" style={{ fontSize: 12.5, marginBottom: 10 }}>Usuario <b>{demo.email}</b> · familia <span className="mono">{demo.family_id.slice(0, 8)}…</span> · proveedor <span className="mono">{demo.provider_id.slice(0, 8)}…</span></div>}
        <textarea className="input mono" rows={3} placeholder="eyJhbGciOi…" value={token} onChange={(e) => setToken(e.target.value.trim())} aria-label="Token de usuario de la app" />
      </div>

      <ErrorBox error={err} />

      {token && me && (
        <div className="grid g2">
          <div className="card">
            <div className="card-head"><div><div className="card-title">2 · Elegir plan</div><div className="card-sub">Precio mensual vigente · CLP</div></div></div>
            <div className="plan-grid">
              {plans.map((p) => (
                <button key={p.plan_code} type="button" className={`plan ${plan === p.plan_code ? "on" : ""}`} onClick={() => setPlan(p.plan_code)} aria-pressed={plan === p.plan_code}>
                  <div className="p-name"><PlanTag code={p.plan_code} name={p.name} /></div>
                  <div className="p-price">{money(p.price_amount, p.currency_code)}</div>
                  <div className="p-unit">al mes, {UNIT[p.billing_unit]}</div>
                </button>
              ))}
            </div>
            {plan !== "gold" && !demo && (
              <div className="field">
                <label htmlFor="ben">{plan === "platinum" ? "Familia (opcional si tienes una sola)" : "Proveedor"}</label>
                <input id="ben" className="input mono" placeholder="uuid" value={beneficiary} onChange={(e) => setBeneficiary(e.target.value)} />
              </div>
            )}
            <button className="btn primary" onClick={subscribe} disabled={busy}>
              {busy ? "Procesando…" : me.payment_method ? `Pagar con ···· ${me.payment_method.card_last4}` : "Continuar a Webpay"}
            </button>
            <div className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>
              Sin tarjeta inscrita, se abre Webpay Oneclick para inscribirla y se cobra el primer mes al volver.
            </div>
            {gateway && gateway !== "fake" && (
              <div className="soft" style={{ fontSize: 12, marginTop: 10, lineHeight: 1.6 }}>
                <b>Webpay de Transbank · ambiente de integración</b> (ningún cobro es real)<br />
                Aprobada: Visa <span className="mono">4051 8856 0044 6623</span> · CVV <span className="mono">123</span> · vencimiento futuro<br />
                Rechazada: Mastercard <span className="mono">5186 0595 5959 0568</span><br />
                Si pide autenticación: RUT <span className="mono">11.111.111-1</span> · clave <span className="mono">123</span>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <div><div className="card-title">Estado del usuario</div><div className="card-sub">GET /api/v1/billing/me</div></div>
              <span className="chip brand">Plan efectivo: {me.entitlement.plan_name}</span>
            </div>
            <dl className="dl" style={{ marginBottom: 14 }}>
              <dt>Tarjeta</dt>
              <dd>{me.payment_method ? `${me.payment_method.card_type} ···· ${me.payment_method.card_last4}` : "Sin tarjeta"} <button className="btn sm" onClick={addCard} disabled={busy}>{me.payment_method ? "Cambiar" : "Inscribir"}</button></dd>
              <dt>Vigente hasta</dt><dd>{date(me.entitlement.valid_until)}</dd>
            </dl>
            <table className="data">
              <thead><tr><th>Plan</th><th>Estado</th><th>Hasta</th><th /></tr></thead>
              <tbody>
                {me.subscriptions.map((s) => (
                  <tr key={s.id}>
                    <td><PlanTag code={s.plan_code} name={s.plan_name} /></td>
                    <td><SubscriptionChip sub={s} /></td>
                    <td className="soft">{date(s.current_period_end)}</td>
                    <td>{s.status === "active" && <button className="btn sm" onClick={() => toggleCancel(s.id, s.cancel_at_period_end)}>{s.cancel_at_period_end ? "Reanudar" : "Cancelar"}</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {me.subscriptions.length === 0 && <div className="empty">Este usuario aún no tiene suscripciones.</div>}
          </div>
        </div>
      )}
    </>
  );
}
