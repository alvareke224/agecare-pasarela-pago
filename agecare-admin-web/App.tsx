import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import ChargeDetail from "./pages/billing/ChargeDetail";
import Charges from "./pages/billing/Charges";
import SubscriptionDetail from "./pages/billing/SubscriptionDetail";
import Subscriptions from "./pages/billing/Subscriptions";
import Summary from "./pages/billing/Summary";
import Checkout from "./pages/checkout/Checkout";

function Protected({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="center-page"><div className="muted">Cargando…</div></div>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route path="/facturacion" element={<Summary />} />
        <Route path="/facturacion/suscripciones" element={<Subscriptions />} />
        <Route path="/facturacion/suscripciones/:id" element={<SubscriptionDetail />} />
        <Route path="/facturacion/cobros" element={<Charges />} />
        <Route path="/facturacion/cobros/:id" element={<ChargeDetail />} />
        <Route path="/checkout" element={<Checkout />} />
        <Route path="/checkout/resultado" element={<Checkout />} />
      </Route>
      <Route path="*" element={<Navigate to="/facturacion" replace />} />
    </Routes>
  );
}
