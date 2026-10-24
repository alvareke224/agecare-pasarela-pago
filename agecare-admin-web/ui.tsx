import { useEffect, useRef, type ReactNode } from "react";
import { ApiError } from "../api/client";
import type { ChargeKind, ChargeStatus, Subscription, SubscriptionStatus } from "../api/billing";
import { date } from "../format";

// Los estados siempre llevan icono + etiqueta (regla del wireframe: nunca solo color)
const SUB_CHIPS: Record<SubscriptionStatus, [string, string]> = {
  active: ["ok", "✔ Activa"],
  past_due: ["warn", "▲ En mora"],
  incomplete: ["neutral", "● Pendiente de pago"],
  canceled: ["neutral", "✕ Cancelada"],
  expired: ["neutral", "○ Expirada"],
};
const CHARGE_CHIPS: Record<ChargeStatus, [string, string]> = {
  authorized: ["ok", "✔ Aprobado"],
  failed: ["crit", "✕ Rechazado"],
  pending: ["warn", "◌ En proceso"],
  unknown: ["warn", "◌ Por conciliar"],
  refunded: ["neutral", "↩ Reembolsado"],
  partially_refunded: ["serious", "↩ Reembolso parcial"],
};
export const KIND_NAMES: Record<ChargeKind, string> = {
  initial: "Primer pago", renewal: "Renovación", retry: "Reintento", manual: "Reintento manual",
};
export const PLAN_COLORS: Record<string, string> = { gold: "var(--s4)", platinum: "var(--s1)", provider: "var(--s3)", free: "var(--neutral-mark)" };
export const CANCEL_REASONS: Record<string, string> = {
  user_request: "Solicitud del usuario", payment_failed: "Pagos rechazados", admin: "Decisión de AgeCare",
  fraud: "Fraude o contracargo", plan_change: "Cambio de plan",
};

export function SubscriptionChip({ sub }: { sub: Pick<Subscription, "status" | "cancel_at_period_end" | "current_period_end"> }) {
  if (sub.status === "active" && sub.cancel_at_period_end)
    return <span className="chip serious">◷ Termina el {date(sub.current_period_end)}</span>;
  const [cls, label] = SUB_CHIPS[sub.status];
  return <span className={`chip ${cls}`}>{label}</span>;
}

export function ChargeChip({ status }: { status: ChargeStatus }) {
  const [cls, label] = CHARGE_CHIPS[status];
  return <span className={`chip ${cls}`}>{label}</span>;
}

export function PlanTag({ code, name }: { code: string; name: string }) {
  return <span><span className="swatch" style={{ background: PLAN_COLORS[code] }} />{name}</span>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="error-box" role="alert">
      {e ? e.message : "Ocurrió un error inesperado."}
      {e?.details?.length ? <ul style={{ margin: "6px 0 0 16px" }}>{e.details.map((d) => <li key={d.field}>{d.field}: {d.issue}</li>)}</ul> : null}
      {e?.requestId && <div className="rid">request_id {e.requestId} · {e.code}</div>}
    </div>
  );
}

export function Kpi({ label, value, delta, deltaCls, note }: { label: string; value: ReactNode; delta?: ReactNode; deltaCls?: string; note?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta !== undefined && <div className={`kpi-delta ${deltaCls ?? ""}`}>{delta}</div>}
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <span>{total.toLocaleString("es-CL")} resultados · página {page} de {pages}</span>
      <div className="actions">
        <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>← Anterior</button>
        <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Siguiente →</button>
      </div>
    </div>
  );
}

export function Modal({ title, sub, onClose, children }: { title: string; sub?: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, textarea, select, button")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <h2>{title}</h2>
        {sub && <div className="sub">{sub}</div>}
        {children}
      </div>
    </div>
  );
}

export function SkeletonRows({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <tbody>
      {Array.from({ length: rows }, (_, i) => (
        <tr key={i}>{Array.from({ length: cols }, (_, j) => <td key={j}><div className="skeleton" /></td>)}</tr>
      ))}
    </tbody>
  );
}
