import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorBox } from "../components/ui";

export default function Login() {
  const { me, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needOtp, setNeedOtp] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to={(location.state as { from?: string } | null)?.from ?? "/facturacion"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password, needOtp ? otp : undefined);
    } catch (err) {
      if (err instanceof ApiError && err.code === "OTP_REQUIRED") setNeedOtp(true);
      else setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card auth-card" onSubmit={submit} style={{ padding: 26 }}>
        <div className="logo">
          <div className="logo-mark">A</div>
          <div>
            <div className="logo-name">AgeCare</div>
            <div className="logo-sub">Consola de administración · Wellq Co</div>
          </div>
        </div>
        <ErrorBox error={error} />
        <div className="field">
          <label htmlFor="email">Correo corporativo</label>
          <input id="email" className="input" type="email" autoComplete="username" required value={email}
                 onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Contraseña</label>
          <input id="password" className="input" type="password" autoComplete="current-password" required minLength={8}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {needOtp && (
          <div className="field">
            <label htmlFor="otp">Código de verificación (MFA)</label>
            <input id="otp" className="input" inputMode="numeric" pattern="\d{6}" maxLength={6} required autoFocus
                   value={otp} onChange={(e) => setOtp(e.target.value)} />
            <span className="hint">Ingresa el código de 6 dígitos de tu app autenticadora.</span>
          </div>
        )}
        <button className="btn primary" style={{ width: "100%", justifyContent: "center" }} disabled={busy}>
          {busy ? "Ingresando…" : "Ingresar"}
        </button>
      </form>
    </div>
  );
}
