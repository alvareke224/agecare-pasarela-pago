import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { ROLE_NAMES, useAuth } from "../auth/AuthContext";
import { initials } from "../format";

// Título y migas de pan de cada página (las páginas los fijan con usePageTitle)
type Head = { title: string; crumb: string };
const HeadCtx = createContext<(h: Head) => void>(() => {});
export function usePageTitle(title: string, crumb: string) {
  const set = useContext(HeadCtx);
  useEffect(() => {
    set({ title, crumb });
    document.title = `${title} · AgeCare Consola`;
  }, [set, title, crumb]);
}

// Los demás módulos de la consola (EP-13) los desarrollan otros equipos: se muestran deshabilitados
const OTHER_MODULES = [
  ["📈", "Uso comercial"], ["🖥️", "Estado operativo"], ["👥", "Usuarios y soporte"], ["🧩", "Uso por funcionalidad"],
  ["🎫", "Tickets de soporte"], ["📰", "Curación de contenido"], ["🛍️", "Catálogos marketplace"], ["🛡️", "Moderación"],
  ["⚙️", "Configuración"], ["📜", "Legales y privacidad"],
];

export default function Layout() {
  const { me, logout, can } = useAuth();
  const [head, setHead] = useState<Head>({ title: "Facturación", crumb: "" });
  if (!me) return null;
  return (
    <HeadCtx.Provider value={setHead}>
      <div className="shell">
        <aside className="sidebar">
          <div className="logo">
            <div className="logo-mark">A</div>
            <div>
              <div className="logo-name">AgeCare</div>
              <div className="logo-sub">Consola de administración</div>
            </div>
          </div>
          <nav className="nav" aria-label="Módulos">
            <div className="nav-group">Comercial</div>
            {can("billing") && (
              <>
                <NavLink end to="/facturacion" className="nav-item"><span className="ico">💳</span>Resumen de facturación</NavLink>
                <NavLink to="/facturacion/suscripciones" className="nav-item"><span className="ico">🔁</span>Suscripciones</NavLink>
                <NavLink to="/facturacion/cobros" className="nav-item"><span className="ico">🧾</span>Cobros y reembolsos</NavLink>
                <NavLink to="/checkout" className="nav-item"><span className="ico">🧪</span>Checkout de prueba</NavLink>
              </>
            )}
            <div className="nav-group">Otros módulos</div>
            {OTHER_MODULES.map(([ico, name]) => (
              <span key={name} className="nav-item disabled" aria-disabled="true" title="Módulo de la consola desarrollado por otro equipo">
                <span className="ico">{ico}</span>{name}<span className="soon">v1</span>
              </span>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="sf-avatar" aria-hidden="true">{initials(me.full_name)}</div>
            <div style={{ minWidth: 0 }}>
              <div className="sf-name">{me.full_name}</div>
              <div className="sf-role">{ROLE_NAMES[me.role]} · Wellq Co</div>
            </div>
            <button className="sf-out" onClick={() => void logout()}>Salir</button>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div>
              <div className="tb-title">{head.title}</div>
              <div className="tb-crumb">{head.crumb}</div>
            </div>
            <div className="spacer" />
          </header>
          <main className="content">
            {can("billing") ? <Outlet /> : (
              <div className="card empty">Tu rol no tiene acceso al módulo de Facturación.</div>
            )}
          </main>
        </div>
      </div>
    </HeadCtx.Provider>
  );
}
