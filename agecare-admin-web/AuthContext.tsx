import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ADMIN, ApiError, api, refreshSession, request, setLogoutHandler, setTokens, storedRefresh } from "../api/client";

export type AdminRole = "admin" | "analyst" | "support" | "editor" | "moderator";
export interface Me {
  id: string;
  full_name: string;
  email: string;
  role: AdminRole;
  permissions: string[];
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string, otp?: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (perm: "billing" | "billing.write" | "billing_refunds") => boolean;
}

const Ctx = createContext<AuthState | null>(null);
export const ROLE_NAMES: Record<AdminRole, string> = {
  admin: "Administrador", analyst: "Analista", support: "Soporte", editor: "Editor", moderator: "Moderador",
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => setMe(await api.get<Me>(`${ADMIN}/auth/me`)), []);

  useEffect(() => {
    setLogoutHandler(() => setMe(null));
    (async () => {
      if (storedRefresh() && (await refreshSession())) {
        try { await loadMe(); } catch { setTokens(null); }
      }
      setLoading(false);
    })();
  }, [loadMe]);

  const login = useCallback(async (email: string, password: string, otp?: string) => {
    const r = await request<{ access_token: string; refresh_token: string }>(
      "POST", `${ADMIN}/auth/login`, { email, password, ...(otp ? { otp_code: otp } : {}) }, "");
    setTokens({ access: r.access_token, refresh: r.refresh_token });
    await loadMe();
  }, [loadMe]);

  const logout = useCallback(async () => {
    try { await api.post(`${ADMIN}/auth/logout`, { refresh_token: storedRefresh() }); } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setTokens(null);
    setMe(null);
  }, []);

  const can = useCallback((perm: "billing" | "billing.write" | "billing_refunds") => {
    if (!me) return false;
    if (perm === "billing.write") return me.permissions.includes("billing") && me.role === "admin";
    return me.permissions.includes(perm);
  }, [me]);

  const value = useMemo(() => ({ me, loading, login, logout, can }), [me, loading, login, logout, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth fuera de AuthProvider");
  return v;
}
