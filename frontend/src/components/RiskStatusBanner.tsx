"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { apiJson, getAuth } from "@/lib/api_client";

type RiskStatus = {
  platformTradingPaused: boolean;
  platformPauseReason: string | null;
  todayRealizedPnl: number;
  maxLossDay: number;
  maxProfitDay: number;
  newTradesAllowed: boolean;
  blockReasonCode: string | null;
};

export default function RiskStatusBanner() {
  const pathname = usePathname();
  const [data, setData] = useState<RiskStatus | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const auth = getAuth();
    if (!auth?.user_id || pathname === "/login") return;
    let cancelled = false;
    apiJson<RiskStatus>("/api/dashboard/risk-status")
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch(() => {
        if (!cancelled) setErr(true);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  if (pathname === "/login" || err || !data) return null;

  if (data.platformTradingPaused) {
    return (
      <div className="risk-banner risk-banner-danger" role="status">
        <strong>Trading paused (platform)</strong>
        <span className="risk-banner-detail">
          {data.platformPauseReason || "Administrator has paused all new trades."}
        </span>
        {getAuth()?.role === "ADMIN" && (
          <Link href="/admin/users" className="risk-banner-link">
            Platform controls
          </Link>
        )}
      </div>
    );
  }

  if (!data.newTradesAllowed && data.blockReasonCode) {
    const loss = data.blockReasonCode === "DAILY_LOSS_LIMIT_REACHED";
    const profit = data.blockReasonCode === "DAILY_PROFIT_LIMIT_REACHED";
    return (
      <div className={`risk-banner ${loss ? "risk-banner-warn" : "risk-banner-info"}`} role="status">
        <strong>{loss ? "Daily loss limit reached" : profit ? "Daily profit cap reached" : "New trades blocked"}</strong>
        <span className="risk-banner-detail">
          Today&apos;s realized P&amp;L: ₹{data.todayRealizedPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
          {loss && data.maxLossDay > 0 && (
            <> · Limit −₹{data.maxLossDay.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</>
          )}
          {profit && data.maxProfitDay > 0 && (
            <> · Cap +₹{data.maxProfitDay.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</>
          )}
        </span>
        <Link href="/settings" className="risk-banner-link">
          Risk settings
        </Link>
      </div>
    );
  }

  return null;
}
