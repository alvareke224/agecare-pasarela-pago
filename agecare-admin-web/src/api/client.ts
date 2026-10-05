// Cliente HTTP de la consola: JWT de staff, refresh rotatorio y formato de error estándar
// ({"error": {code, message, details, request_id}}) de la Especificación de Endpoints.

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";
export const ADMIN = `${BASE}/api/v1/admin`;
export const BILLING_APP = `${BASE}/api/v1/billing`;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: { field: string; issue: string }[] | null = null,
    public requestId: string | null = null,
  ) {
    super(message);
  }
}

type Tokens = { access: string; refresh: string };
let tokens: Tokens | null = null;
let onLogout: (() => void) | null = null;
const STORE_KEY = "agecare.admin.refresh";

// El refresh token vive en sessionStorage (se pierde al cerrar la pestaña). Si el
// almacenamiento no está disponible, la sesión dura lo que la pestaña en memoria.
function persist(refresh: string | null) {
  try {
    if (refresh) sessionStorage.setItem(STORE_KEY, refresh);
    else sessionStorage.removeItem(STORE_KEY);
  } catch {
    /* almacenamiento bloqueado: sin persistencia */
  }
}
export function storedRefresh(): string | null {
  try {
    return sessionStorage.getItem(STORE_KEY);
  } catch {
    return null;
  }
}

export function setTokens(t: Tokens | null) {
  tokens = t;
  persist(t?.refresh ?? null);
}
export function setLogoutHandler(fn: () => void) {
  onLogout = fn;
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function toError(status: number, body: unknown): ApiError {
  const e = (body as { error?: { code: string; message: string; details: never; request_id: string } })?.error;
  if (e) return new ApiError(status, e.code, e.message, e.details ?? null, e.request_id ?? null);
  return new ApiError(status, "NETWORK", status === 0 ? "No hay conexión con la API." : `Error HTTP ${status}`);
}

let refreshing: Promise<boolean> | null = null;
export async function refreshSession(refresh = tokens?.refresh ?? storedRefresh()): Promise<boolean> {
  if (!refresh) return false;
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${ADMIN}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) return false;
      const body = (await res.json()) as { access_token: string; refresh_token: string };
      setTokens({ access: body.access_token, refresh: body.refresh_token });
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

export async function request<T>(method: string, url: string, body?: unknown, auth: "admin" | string = "admin",
                                 retry = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const bearer = auth === "admin" ? tokens?.access : auth;
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "NETWORK", "No hay conexión con la API. Revisa que el backend esté levantado.");
  }
  if (res.status === 401 && auth === "admin" && retry && tokens) {
    if (await refreshSession()) return request<T>(method, url, body, auth, false);
    setTokens(null);
    onLogout?.();
  }
  const data = await parse(res);
  if (!res.ok) throw toError(res.status, data);
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
