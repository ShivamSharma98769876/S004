export const DESIGN_TOKENS = {
  appName: "StockSage S004",
  appSubtitle: "Options Decision Platform",
  storage: {
    themeKey: "s004_theme",
    densityKey: "s004_density",
  },
  layout: {
    maxWidthPx: 1320,
    sidebarWidthPx: 216,
    wideSidebarMinWidthPx: 1180,
  },
} as const;

export type AppNavItem = {
  href: string;
  label: string;
  icon: string;
};

/** Nav items visible to all users */
export const USER_NAV_ITEMS: readonly AppNavItem[] = [
  { href: "/landing", label: "Home", icon: "HM" },
  { href: "/dashboard", label: "Dashboard", icon: "DB" },
  { href: "/trades", label: "Trades", icon: "TR" },
  { href: "/marketplace", label: "Strategies", icon: "MP" },
  { href: "/risk", label: "Risk", icon: "RK" },
  { href: "/settings", label: "Settings", icon: "ST" },
  { href: "/settings/brokers", label: "Brokers", icon: "BR" },
] as const;

/** Reports section – sub-items under Reports menu */
export const REPORTS_NAV_ITEMS: readonly AppNavItem[] = [
  { href: "/observability", label: "Observability", icon: "OB" },
  { href: "/reports", label: "Performance Snapshot", icon: "PS" },
  { href: "/reports/performance-analytics", label: "Performance Analytics", icon: "PA" },
  { href: "/reports/strategy-performance", label: "Strategy Performance", icon: "SP" },
] as const;

/** Nav items visible only to Admin */
export const ADMIN_NAV_ITEMS: readonly AppNavItem[] = [
  { href: "/admin/users", label: "Users", icon: "US" },
  { href: "/admin/evolution", label: "Evolution", icon: "EV" },
  { href: "/admin/evaluation", label: "Evaluation", icon: "FV" },
  { href: "/admin/strategy-eod", label: "Strategy EOD", icon: "EO" },
  { href: "/analytics", label: "Analytics", icon: "AN" },
] as const;

/** All nav items (for backward compatibility) */
export const APP_NAV_ITEMS: readonly AppNavItem[] = [
  ...USER_NAV_ITEMS,
  ...REPORTS_NAV_ITEMS,
  ...ADMIN_NAV_ITEMS,
] as const;
