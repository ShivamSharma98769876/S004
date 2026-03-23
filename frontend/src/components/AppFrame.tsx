"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { clearAuth, getAuth, isAdmin } from "@/lib/api_client";
import { ADMIN_NAV_ITEMS, DESIGN_TOKENS, REPORTS_NAV_ITEMS, USER_NAV_ITEMS } from "@/design/tokens";
import RiskStatusBanner from "@/components/RiskStatusBanner";

type AppFrameProps = {
  title: string;
  subtitle: string;
  children: ReactNode;
};

export default function AppFrame({ title, subtitle, children }: AppFrameProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [lightTheme, setLightTheme] = useState(false);
  const [denseMode, setDenseMode] = useState(false);
  const admin = isAdmin();
  const user = getAuth();

  const handleLogout = () => {
    clearAuth();
    router.push("/login");
    router.refresh();
  };
  const layoutVars = {
    ["--layout-max-width" as string]: "100%",
    ["--sidebar-width" as string]: `${DESIGN_TOKENS.layout.sidebarWidthPx}px`,
    ["--wide-sidebar-breakpoint" as string]: `${DESIGN_TOKENS.layout.wideSidebarMinWidthPx}px`,
  } as CSSProperties;

  useEffect(() => {
    const storedTheme = window.localStorage.getItem(DESIGN_TOKENS.storage.themeKey);
    const storedDensity = window.localStorage.getItem(DESIGN_TOKENS.storage.densityKey);

    if (storedTheme === "light") {
      setLightTheme(true);
      document.documentElement.dataset.theme = "light";
    }
    if (storedDensity === "dense") {
      setDenseMode(true);
      document.documentElement.dataset.density = "dense";
    }
  }, []);

  const toggleTheme = () => {
    const next = !lightTheme;
    setLightTheme(next);
    document.documentElement.dataset.theme = next ? "light" : "dark";
    window.localStorage.setItem(DESIGN_TOKENS.storage.themeKey, next ? "light" : "dark");
  };

  const toggleDensity = () => {
    const next = !denseMode;
    setDenseMode(next);
    document.documentElement.dataset.density = next ? "dense" : "comfortable";
    window.localStorage.setItem(DESIGN_TOKENS.storage.densityKey, next ? "dense" : "comfortable");
  };

  return (
    <main className="app-layout" style={layoutVars}>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-title">{DESIGN_TOKENS.appName}</div>
          <div className="brand-subtitle">{DESIGN_TOKENS.appSubtitle}</div>
        </div>
        <nav className="sidebar-nav" aria-label="Primary">
          {USER_NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} className={`sidebar-link${active ? " active" : ""}`}>
                <span className="sidebar-icon">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
          <div className="sidebar-nav-group">Reports</div>
          {REPORTS_NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} className={`sidebar-link${active ? " active" : ""}`}>
                <span className="sidebar-icon">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
          {admin && (
            <>
              <div className="sidebar-nav-group">Admin</div>
              {ADMIN_NAV_ITEMS.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link key={item.href} href={item.href} className={`sidebar-link${active ? " active" : ""}`}>
                    <span className="sidebar-icon">{item.icon}</span>
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </>
          )}
        </nav>
      </aside>

      <section className="page-grid">
        <header className="topbar">
          <div className="brand-block">
            <div className="brand-title">{DESIGN_TOKENS.appName}</div>
            <div className="brand-subtitle">{DESIGN_TOKENS.appSubtitle}</div>
          </div>
          <div className="topbar-right">
            <nav className="topnav">
              {USER_NAV_ITEMS.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link key={item.href} href={item.href} className={`topnav-link${active ? " active" : ""}`}>
                    {item.label}
                  </Link>
                );
              })}
              <span className="topnav-group-label">Reports</span>
              {REPORTS_NAV_ITEMS.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link key={item.href} href={item.href} className={`topnav-link${active ? " active" : ""}`}>
                    {item.label}
                  </Link>
                );
              })}
              {admin && (
                <>
                  <span className="topnav-group-label">Admin</span>
                  {ADMIN_NAV_ITEMS.map((item) => {
                    const active = pathname === item.href;
                    return (
                      <Link key={item.href} href={item.href} className={`topnav-link${active ? " active" : ""}`}>
                        {item.label}
                      </Link>
                    );
                  })}
                </>
              )}
            </nav>
            <div className="topbar-user">
              <span className="topbar-username">{user?.username ?? ""}</span>
              <span className="topbar-role">{admin ? "Admin" : "User"}</span>
              <button type="button" className="toggle-button" onClick={handleLogout} aria-label="Sign out">
                Sign out
              </button>
            </div>
            <div className="toggle-group">
              <button className="toggle-button" onClick={toggleTheme} aria-label="Toggle light and dark theme">
                {lightTheme ? "Dark" : "Light"}
              </button>
              <button
                className="toggle-button"
                onClick={toggleDensity}
                aria-label="Toggle dense and comfortable layout"
              >
                {denseMode ? "Comfortable" : "Dense"}
              </button>
            </div>
          </div>
        </header>

        <section className="page-header">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </section>

        <RiskStatusBanner />

        {children}
      </section>
    </main>
  );
}
