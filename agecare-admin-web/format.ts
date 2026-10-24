// Formatos es-CL (CLP sin decimales, fechas en hora de Santiago)
const TZ = "America/Santiago";

export const money = (n: number | null | undefined, currency = "CLP") =>
  n === null || n === undefined
    ? "—"
    : new Intl.NumberFormat("es-CL", { style: "currency", currency, maximumFractionDigits: 0 }).format(n);

export const num = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("es-CL"));

export const pct = (n: number | null | undefined, digits = 1) =>
  n === null || n === undefined ? "—" : `${(n * 100).toLocaleString("es-CL", { maximumFractionDigits: digits })}%`;

export const date = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric", timeZone: TZ }) : "—";

export const dateTime = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString("es-CL", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: TZ })
    : "—";

export function relativeDays(iso: string | null | undefined): string {
  if (!iso) return "";
  const days = Math.round((new Date(iso).getTime() - Date.now()) / 86_400_000);
  if (days === 0) return "hoy";
  if (days === 1) return "mañana";
  if (days === -1) return "ayer";
  return days > 0 ? `en ${days} días` : `hace ${-days} días`;
}

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
