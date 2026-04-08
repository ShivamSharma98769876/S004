"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import {
  DEFAULT_TRADING_SETUP,
  loadTradingSetup,
  saveTradingSetup,
  type MasterSetup,
  type TradeMode,
  type TradingSetup,
} from "@/lib/trading_setup";
import { apiJson, isAdmin, postTradesRefreshCycle } from "@/lib/api_client";
import { backendInstantMs, formatClockNowIST, formatTimeIST } from "@/lib/datetime_ist";

type ClosedTrade = {
  symbol: string;
  strike: number;
  type: "CE" | "PE";
  mode: "PAPER" | "LIVE";
  buyTime: string;
  entry: number;
  sellTime: string;
  exit: number;
  qty: number;
  pnl: number;
  reason: string;
};

const CLOSED: ClosedTrade[] = [
  {
    symbol: "NIFTY2631723400PE",
    strike: 23400,
    type: "PE",
    mode: "PAPER",
    buyTime: "09:26:37",
    entry: 192.9,
    sellTime: "09:39:07",
    exit: 203.3,
    qty: 65,
    pnl: 604.53,
    reason: "Target Hit",
  },
  {
    symbol: "NIFTY2631723400PE",
    strike: 23400,
    type: "PE",
    mode: "PAPER",
    buyTime: "09:26:58",
    entry: 191.0,
    sellTime: "09:35:47",
    exit: 201.7,
    qty: 65,
    pnl: 624.24,
    reason: "Target Hit",
  },
];

function formatInr(n: number): string {
  return `INR ${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function formatTime(iso: string | null | undefined): string {
  return formatTimeIST(iso, { seconds: true, fallback: "--:--:--", appendIstLabel: true });
}

function formatTimeShort(iso: string | null | undefined): string {
  return formatTimeIST(iso, { fallback: "00:00", appendIstLabel: true });
}

type IntradayPoint = { time: string; pnl: number };

function chartClosedTimeLabelIST(closedAt: unknown): string {
  if (closedAt == null || closedAt === "") return "—";
  const s = String(closedAt).trim();
  if (/^\d{1,2}:\d{2}/.test(s) && !/^\d{4}-\d{2}-\d{2}/.test(s)) {
    return s.slice(0, 5);
  }
  return formatTimeIST(s, { seconds: false, appendIstLabel: false });
}

function IntradayPnlChart({ data, closedTradesCount }: { data: IntradayPoint[]; closedTradesCount: number }) {
  const w = 620;
  const h = 230;
  const padLeft = 58;
  const padRight = 20;
  const padTop = 28;
  const padBottom = 40;
  const plotW = w - padLeft - padRight;
  const plotH = h - padTop - padBottom;

  const maxPnl = data.length ? Math.max(...data.map((d) => d.pnl), 0) : 0;
  const minPnl = data.length ? Math.min(...data.map((d) => d.pnl), 0) : 0;
  const range = Math.max(500, maxPnl - minPnl, Math.abs(maxPnl), Math.abs(minPnl));
  const step = range <= 1000 ? 250 : range <= 5000 ? 1000 : range <= 20000 ? 5000 : 10000;
  const yMax = Math.ceil(Math.max(maxPnl, step) / step) * step;
  const yMin = Math.floor(minPnl / step) * step;
  const yRange = Math.max(step, yMax - yMin);

  const xScale = (i: number) => padLeft + (data.length > 1 ? (i / (data.length - 1)) * plotW : 0);
  const yScale = (pnl: number) => padTop + plotH - ((pnl - yMin) / yRange) * plotH;

  const yTicks: number[] = [];
  for (let t = Math.ceil(yMin / step) * step; t <= yMax; t += step) yTicks.push(t);
  if (yTicks.length === 0) yTicks.push(0, step);
  if (!yTicks.includes(0) && yMin < 0 && yMax > 0) yTicks.push(0);
  yTicks.sort((a, b) => a - b);

  const linePoints = data.map((d, i) => `${xScale(i)},${yScale(d.pnl)}`).join(" ");
  const areaPoints = `${padLeft},${padTop + plotH} ${data.map((d, i) => `${xScale(i)},${yScale(d.pnl)}`).join(" ")} ${padLeft + plotW},${padTop + plotH}`;

  const maxLabels = 8;
  const showLabelEvery = Math.max(1, Math.ceil(data.length / maxLabels));
  const highPnl = data.length ? Math.max(...data.map((d) => d.pnl)) : 0;
  const lowPnl = data.length ? Math.min(...data.map((d) => d.pnl)) : 0;

  return (
    <div className="dash-chart-container">
      <svg viewBox={`0 0 ${w} ${h}`} className="dash-chart">
        {/* Y-axis labels */}
        {yTicks.map((tick) => (
          <text key={tick} x={padLeft - 10} y={yScale(tick) + 5} className="dash-chart-label" textAnchor="end">
            ₹{tick.toLocaleString("en-IN")}
          </text>
        ))}
        {/* Zero line when P&L goes negative */}
        {yMin < 0 && yMax > 0 && (
          <line
            x1={padLeft}
            y1={yScale(0)}
            x2={padLeft + plotW}
            y2={yScale(0)}
            stroke="rgba(255,255,255,0.25)"
            strokeWidth="1"
            strokeDasharray="4 3"
          />
        )}
        {/* Area fill - darker blue under the line */}
        <polygon points={areaPoints} fill="rgba(21, 70, 130, 0.4)" />
        {/* Line */}
        <polyline fill="none" stroke="#2bc48a" strokeWidth="2.5" points={linePoints} />
        {/* Data point circles */}
        {data.map((d, i) => (
          <circle
            key={`dot-${d.time}-${i}`}
            cx={xScale(i)}
            cy={yScale(d.pnl)}
            r="5"
            fill="#1f6feb"
            className="dash-chart-dot"
          >
            <title>
              {d.time} — {d.pnl >= 0 ? "+" : ""}₹{d.pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
            </title>
          </circle>
        ))}
        {/* X-axis time labels - limited to avoid overlap */}
        {data.map((d, i) =>
          i % showLabelEvery === 0 || i === data.length - 1 ? (
            <text key={`label-${d.time}-${i}`} x={xScale(i)} y={h - 12} className="dash-chart-label dash-chart-xlabel" textAnchor="middle">
              {d.time}
            </text>
          ) : null
        )}
      </svg>
      {/* Summary footer */}
      <div className="dash-chart-footer">
        <span>{closedTradesCount} closed trades</span>
        <span className={highPnl >= 0 ? "metric-positive" : "chip-risk-high"}>
          High: {highPnl >= 0 ? "+" : ""}₹{highPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
        </span>
        <span className={lowPnl >= 0 ? "metric-positive" : "chip-risk-high"}>
          Low: {lowPnl >= 0 ? "+" : ""}₹{lowPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
        </span>
      </div>
    </div>
  );
}

type DashboardSummary = {
  open_trades: number;
  closed_trades: number;
  gross_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  charges_today?: number;
  win_rate_pct: number;
  /** Consecutive ISO weeks (Mon-based) with at least one closed trade, ending at last active week */
  trading_week_streak?: number;
};

type DashboardFunds = {
  initial_capital: number;
  used_margin: number;
  available_cash: number;
  net_balance: number;
  bot_capital: number;
};

function tradeSideLabel(side: string | null | undefined): string {
  const s = String(side || "BUY").trim().toUpperCase();
  return s === "SELL" ? "SELL" : "BUY";
}

type BackendTrade = {
  trade_ref: string;
  symbol: string;
  strategy_name?: string | null;
  mode: string;
  side: string;
  /** Some API responses use `qty`; prefer explicit quantity when both exist. */
  qty?: number;
  quantity: number;
  entry_price: number;
  current_price: number;
  target_price?: number;
  stop_loss_price?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  opened_at?: string | null;
  closed_at?: string | null;
  current_state?: string;
  reason?: string | null;
  manual_execute?: boolean | null;
  score?: number | null;
  confidence_score?: number | null;
};

/** Panel copy is strategy-agnostic; active plan name lives in the dashboard status strip. */
const STRATEGY_SIGNALS_SUBTITLE =
  "Same entry bar as auto-execute: score ≥ autoTradeScoreThreshold, confidence ≥ 80, and signal eligible (inferred from score if needed). Inside short-premium delta band. Each ~30s tick refreshes the option chain first, then open positions and signals load from that snapshot. Scope: your active subscription (admins: all strategies, like Trades).";

const STRATEGY_SIGNALS_EMPTY =
  "No eligible strikes right now. When the engine marks a strike as eligible, it appears here. Auto-execute also uses this feed.";

function formatSignalExecuteError(raw: string): string {
  if (/recommendation\s+not\s+found/i.test(raw)) {
    return "That signal expired or was replaced. Wait for the next refresh or tap ↻ when enabled.";
  }
  return raw;
}

function IconPaperSignal() {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
      <path d="M14 2v6h6" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
      <path d="M8 13h8M8 17h6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

function IconLiveSignal() {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function formatSignalStrategyLabel(s: {
  strategy_name?: string | null;
  strategy_id?: string | null;
  strategy_version?: string | null;
}): string {
  const name = (s.strategy_name || "").trim();
  const base = name || s.strategy_id?.trim() || "—";
  const ver = (s.strategy_version || "").trim();
  return ver ? `${base} · v${ver}` : base;
}

type SignalRecommendation = {
  recommendation_id: string;
  symbol: string;
  strategy_name?: string | null;
  strategy_id?: string | null;
  strategy_version?: string | null;
  rank_value: number;
  confidence_score?: number | null;
  score?: number | null;
  score_max?: number | null;
  spot_price?: number | null;
  entry_price?: number | null;
  vwap?: number | null;
  ema9?: number | null;
  ema21?: number | null;
  rsi?: number | null;
  /** IV rank proxy for this strike within the same expiry chain (0–100). */
  ivr?: number | null;
  volume_spike_ratio?: number | null;
  timeframe?: string | null;
  refresh_interval_sec?: number | null;
  created_at?: string;
  trendpulse?: {
    tier1?: { cross?: string; htf_bias?: string; adx?: number; opening_block?: boolean };
    tier2?: {
      delta?: number;
      extrinsic_share?: number;
      extrinsic_min?: number;
      expiry?: string;
      delta_band?: number[];
    };
    cross?: string;
    htf_bias?: string;
  } | null;
  option_type?: string | null;
  delta?: number | null;
  oi?: number | null;
};

export default function DashboardPage() {
  const [setup, setSetup] = useState<TradingSetup>(DEFAULT_TRADING_SETUP);
  const [clock, setClock] = useState<string>("--:--:--");
  const [runtimeMessage, setRuntimeMessage] = useState<string>("");
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [funds, setFunds] = useState<DashboardFunds | null>(null);
  const [openTradesRows, setOpenTradesRows] = useState<BackendTrade[]>([]);
  const [closedTradesRows, setClosedTradesRows] = useState<BackendTrade[]>([]);
  const [signals, setSignals] = useState<SignalRecommendation[]>([]);
  const [signalExecuteLoading, setSignalExecuteLoading] = useState<string | null>(null);
  const [signalExecuteError, setSignalExecuteError] = useState<string | null>(null);
  const [optimisticOpenKeys, setOptimisticOpenKeys] = useState<Set<string>>(new Set());
  const [openSortCol, setOpenSortCol] = useState<string>("");
  const [openSortDir, setOpenSortDir] = useState<"asc" | "desc">("asc");
  const [closedSortCol, setClosedSortCol] = useState<string>("");
  const [closedSortDir, setClosedSortDir] = useState<"asc" | "desc">("asc");
  /** Server-resolved strategy (subscription + settings); strip must not rely on stale localStorage alone. */
  const [activeStrategyBanner, setActiveStrategyBanner] = useState<{
    strategyId: string;
    strategyVersion: string;
    displayName: string;
    positionIntent?: "long_premium" | "short_premium";
  } | null>(null);

  const refreshSignalsAndOpen = useCallback(async () => {
    setSignalExecuteError(null);
    try {
      try {
        await postTradesRefreshCycle();
      } catch {
        /* still load lists */
      }
      const [recs, open] = await Promise.all([
        apiJson<SignalRecommendation[]>("/api/trades/recommendations", "GET", undefined, {
          status: "GENERATED",
          sort_by: "rank",
          sort_dir: "asc",
          limit: 8,
          offset: 0,
          eligible_only: "true",
          ensure_refresh: "false",
          ...(isAdmin() && { all_strategies: "true" }),
        }),
        apiJson<BackendTrade[]>("/api/trades/open"),
      ]);
      setSignals(recs);
      setOpenTradesRows(open);
      setOptimisticOpenKeys(new Set());
    } catch {
      // Keep existing on failure
    }
  }, []);

  const executeSignal = useCallback(
    async (recommendationId: string, mode: "PAPER" | "LIVE", symbol: string) => {
      setSignalExecuteError(null);
      setSignalExecuteLoading(`${recommendationId}|${mode}`);
      try {
        const symKey = symbol.replace(/\s+/g, "").toUpperCase();
        try {
          await postTradesRefreshCycle();
        } catch {
          /* proceed with list fetch */
        }
        const fresh = await apiJson<SignalRecommendation[]>("/api/trades/recommendations", "GET", undefined, {
          status: "GENERATED",
          sort_by: "rank",
          sort_dir: "asc",
          limit: 24,
          offset: 0,
          eligible_only: "true",
          ensure_refresh: "false",
          ...(isAdmin() && { all_strategies: "true" }),
        });
        const row = fresh.find((r) => String(r.symbol || "").replace(/\s+/g, "").toUpperCase() === symKey);
        const idToUse = row?.recommendation_id ?? recommendationId;
        setSignals(fresh);
        await apiJson("/api/trades/execute", "POST", {
          recommendation_id: idToUse,
          mode,
          quantity: 1,
        });
        setOptimisticOpenKeys((prev) => new Set(prev).add(`${symbol}|${mode}`));
        await refreshSignalsAndOpen();
      } catch (e) {
        const raw = e instanceof Error ? e.message : "Failed to execute";
        setSignalExecuteError(formatSignalExecuteError(raw));
      } finally {
        setSignalExecuteLoading(null);
      }
    },
    [refreshSignalsAndOpen]
  );

  useEffect(() => {
    const s = loadTradingSetup();
    setSetup(s);
    setClock(formatClockNowIST());
    const timer = setInterval(() => setClock(formatClockNowIST()), 1000);
    const sync = setInterval(() => setSetup(loadTradingSetup()), 8000);
    return () => {
      clearInterval(timer);
      clearInterval(sync);
    };
  }, []);

  useEffect(() => {
    /** One cadence: chain/engine first, then dashboard summary + open + signals from the same snapshot. */
    const SYNC_POLL_MS = 30_000;

    const applyEngine = (
      engine: {
        engineRunning: boolean;
        mode: TradeMode | string;
        brokerConnected?: boolean;
        sharedApiConnected?: boolean;
        isAdmin?: boolean;
        kiteStatus?: "connected" | "shared" | "none";
        platformApiOnline?: boolean;
        maxTradesDay?: number;
        activeStrategy?: {
          strategyId: string;
          strategyVersion: string;
          displayName: string;
          positionIntent?: "long_premium" | "short_premium";
        };
      } | null,
    ) => {
      if (!engine) return;
      setActiveStrategyBanner(engine.activeStrategy ?? null);
      const prev = loadTradingSetup();
      const ks = engine.kiteStatus;
      const kiteStatus: MasterSetup["kiteStatus"] =
        ks === "connected" || ks === "shared" || ks === "none" ? ks : prev.master.kiteStatus;
      const sharedApi = engine.sharedApiConnected ?? prev.master.sharedApiConnected;
      const eng = engine as {
        platformApiOnline?: boolean;
        maxTradesDay?: number;
        activeStrategy?: {
          strategyId: string;
          strategyVersion: string;
          displayName: string;
          positionIntent?: "long_premium" | "short_premium";
        };
      };
      const mode: TradeMode =
        engine.mode === "LIVE" || engine.mode === "PAPER" ? engine.mode : prev.master.mode;
      const merged: TradingSetup = {
        ...prev,
        master: {
          ...prev.master,
          engineRunning: engine.engineRunning,
          mode,
          brokerConnected: engine.brokerConnected ?? prev.master.brokerConnected,
          sharedApiConnected: sharedApi,
          kiteStatus,
          platformApiOnline: eng.platformApiOnline ?? prev.master.platformApiOnline,
          maxTrades: eng.maxTradesDay ?? prev.master.maxTrades,
        },
        strategy: {
          ...prev.strategy,
          ...(eng.activeStrategy
            ? {
                strategyName: eng.activeStrategy.displayName,
                strategyVersion: eng.activeStrategy.strategyVersion,
              }
            : {}),
        },
      };
      setSetup(merged);
      saveTradingSetup(merged);
    };

    const loadCore = async () => {
      try {
        const [s, f, o, c, engine] = await Promise.all([
          apiJson<DashboardSummary>("/api/dashboard/summary"),
          apiJson<DashboardFunds>("/api/dashboard/funds"),
          apiJson<BackendTrade[]>("/api/trades/open"),
          apiJson<BackendTrade[]>("/api/trades/history", "GET", undefined, { today_only: "true" }),
          apiJson<{
            engineRunning: boolean;
            mode: TradeMode | string;
            brokerConnected?: boolean;
            sharedApiConnected?: boolean;
            isAdmin?: boolean;
            kiteStatus?: "connected" | "shared" | "none";
            platformApiOnline?: boolean;
            maxTradesDay?: number;
            activeStrategy?: {
              strategyId: string;
              strategyVersion: string;
              displayName: string;
              positionIntent?: "long_premium" | "short_premium";
            };
          }>("/api/dashboard/engine").catch(() => null),
        ]);
        setSummary(s);
        setFunds(f);
        setOpenTradesRows(o);
        setClosedTradesRows(c || []);
        applyEngine(engine);
      } catch {
        /* keep existing dashboard data */
      }
    };

    const loadSignalsOnly = async () => {
      try {
        const recs = await apiJson<SignalRecommendation[]>("/api/trades/recommendations", "GET", undefined, {
          status: "GENERATED",
          sort_by: "rank",
          sort_dir: "asc",
          limit: 8,
          offset: 0,
          eligible_only: "true",
          ensure_refresh: "false",
          ...(isAdmin() && { all_strategies: "true" }),
        });
        setSignals(recs);
      } catch {
        /* keep existing signals */
      }
    };

    const runSyncedTick = async () => {
      try {
        await postTradesRefreshCycle();
      } catch {
        /* still load dashboard slices */
      }
      await Promise.all([loadCore(), loadSignalsOnly()]);
    };

    void runSyncedTick();
    const syncT = window.setInterval(() => void runSyncedTick(), SYNC_POLL_MS);
    return () => {
      window.clearInterval(syncT);
    };
  }, []);

  const openPositions = summary ? summary.open_trades : setup.master.engineRunning ? 2 : 0;
  const closedTrades = summary ? summary.closed_trades : CLOSED.length;
  const chargesToday =
    summary?.charges_today ?? (openPositions + closedTrades) * (setup.capitalRisk.chargesPerTrade ?? 20);
  const grossPnl = summary ? summary.gross_pnl : CLOSED.reduce((a, b) => a + b.pnl, 0);
  const netPnl = (summary ? summary.realized_pnl : CLOSED.reduce((a, b) => a + b.pnl, 0)) - chargesToday;
  const unrealized =
    openTradesRows.length > 0
      ? openTradesRows.reduce((acc, t) => acc + Number(t.unrealized_pnl || 0), 0)
      : (summary ? summary.unrealized_pnl : 0);
  const targetProgress = Math.min(100, Math.round((netPnl / Math.max(setup.capitalRisk.maxProfitDay, 1)) * 100));
  const winRate = summary ? summary.win_rate_pct : closedTrades ? 100 : 0;
  const avgPerTrade = closedTrades ? netPnl / closedTrades : 0;
  const tradingWeekStreak = Math.max(0, Math.floor(summary?.trading_week_streak ?? 0));
  const tradingWeekStreakLabel =
    tradingWeekStreak === 0 ? "+0W" : `+${tradingWeekStreak}W`;
  const estimatedMarginUsed =
    funds?.used_margin ??
    (setup.master.engineRunning
      ? Math.min(setup.capitalRisk.maxInvestmentPerTrade, setup.tradingParameters.lots * setup.tradingParameters.lotSize * 42)
      : 0);
  const availableCash = funds?.available_cash ?? setup.capitalRisk.initialCapital - estimatedMarginUsed;

  const intradayPnlData = useMemo(() => {
    const trades = closedTradesRows.length
      ? [...closedTradesRows].sort(
          (a, b) =>
            backendInstantMs((a as any).closed_at) - backendInstantMs((b as any).closed_at)
        )
      : [];
    const points: IntradayPoint[] = [];
    points.push({ time: "09:15", pnl: 0 });
    let cumulativeGross = 0;
    for (const t of trades) {
      const pnl = Number((t as any).realized_pnl ?? (t as any).pnl ?? 0);
      cumulativeGross += pnl;
      const closedAt = (t as any).closed_at ?? (t as any).sellTime;
      points.push({ time: chartClosedTimeLabelIST(closedAt), pnl: cumulativeGross });
    }
    const nSummary = summary?.closed_trades;
    const grossFromSummary = summary != null ? Number(summary.realized_pnl) : NaN;
    const tradeRowsMatchDay =
      summary != null &&
      trades.length > 0 &&
      ((typeof nSummary === "number" && trades.length === nSummary) ||
        Math.abs(cumulativeGross - grossFromSummary) < 0.05);
    if (trades.length > 0 && tradeRowsMatchDay && Math.abs(netPnl - cumulativeGross) > 0.01) {
      const nowLabel = formatTimeIST(Date.now(), { seconds: false, appendIstLabel: false });
      points.push({ time: nowLabel, pnl: netPnl });
    } else if (trades.length > 0 && tradeRowsMatchDay && points.length > 1) {
      points[points.length - 1] = { ...points[points.length - 1], pnl: netPnl };
    }
    if (points.length === 1) {
      points.push({ time: "—", pnl: 0 });
    }
    return points;
  }, [closedTradesRows, summary, netPnl]);

  const sortedOpenTrades = useMemo(() => {
    if (!openSortCol) return openTradesRows;
    const arr = [...openTradesRows];
    const mult = openSortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: string | number | boolean | undefined = a[openSortCol as keyof BackendTrade];
      let bv: string | number | boolean | undefined = b[openSortCol as keyof BackendTrade];
      if (openSortCol === "manual_execute") {
        av = av === false ? 0 : av === true ? 1 : -1;
        bv = bv === false ? 0 : bv === true ? 1 : -1;
      }
      if (openSortCol === "qty" && av == null) av = a.quantity;
      if (openSortCol === "qty" && bv == null) bv = b.quantity;
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * av.localeCompare(bv);
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [openTradesRows, openSortCol, openSortDir]);

  const sortedClosedTrades = useMemo(() => {
    if (!closedSortCol) return closedTradesRows;
    const arr = [...closedTradesRows];
    const mult = closedSortDir === "asc" ? 1 : -1;
    arr.sort((a: any, b: any) => {
      let av: string | number | undefined = a[closedSortCol];
      let bv: string | number | undefined = b[closedSortCol];
      if (closedSortCol === "realized_pnl") {
        av = Number(a.realized_pnl ?? a.pnl ?? 0);
        bv = Number(b.realized_pnl ?? b.pnl ?? 0);
      } else if (closedSortCol === "opened_at" || closedSortCol === "closed_at") {
        av = backendInstantMs(String(av ?? ""));
        bv = backendInstantMs(String(bv ?? ""));
      } else if (closedSortCol === "manual_execute") {
        av = a.manual_execute === false ? 0 : a.manual_execute === true ? 1 : -1;
        bv = b.manual_execute === false ? 0 : b.manual_execute === true ? 1 : -1;
      } else if (closedSortCol === "entry_price") {
        av = Number(a.entry_price ?? a.entry ?? 0);
        bv = Number(b.entry_price ?? b.entry ?? 0);
      } else if (closedSortCol === "current_price") {
        av = Number(a.current_price ?? a.exit ?? 0);
        bv = Number(b.current_price ?? b.exit ?? 0);
      } else if (closedSortCol === "qty") {
        av = Number(a.qty ?? a.quantity ?? 0);
        bv = Number(b.qty ?? b.quantity ?? 0);
      }
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * (av || "").localeCompare(bv || "");
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [closedTradesRows, closedSortCol, closedSortDir]);

  const handleOpenSort = (col: string) => {
    if (openSortCol === col) setOpenSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setOpenSortCol(col);
      setOpenSortDir("asc");
    }
  };
  const handleClosedSort = (col: string) => {
    if (closedSortCol === col) setClosedSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setClosedSortCol(col);
      setClosedSortDir("asc");
    }
  };

  const toggleEngine = async (run: boolean) => {
    if (run) {
      if (!setup.master.platformApiOnline || !setup.master.sharedApiConnected) {
        setRuntimeMessage("Cannot start: API services are offline.");
        return;
      }
      const kiteStatus = setup.master.kiteStatus;
      const kiteOk =
        kiteStatus === "connected" ||
        kiteStatus === "shared" ||
        (kiteStatus == null && (setup.master.brokerConnected || setup.master.sharedApiConnected));
      if (setup.master.mode === "LIVE" && (!setup.master.goLive || !kiteOk)) {
        setRuntimeMessage("Cannot start LIVE mode without Go Live ON and Kite (Connected or Shared) available.");
        return;
      }
      setRuntimeMessage("");
    } else {
      setRuntimeMessage("Engine stopped manually.");
    }
    const previous = setup;
    const next = { ...setup, master: { ...setup.master, engineRunning: run } };
    setSetup(next);
    saveTradingSetup(next);
    try {
      await apiJson("/api/dashboard/engine", "PUT", { engineRunning: run, mode: setup.master.mode });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to sync engine status with server.";
      setRuntimeMessage(msg || "Failed to sync engine status with server.");
      setSetup(previous);
      saveTradingSetup(previous);
    }
  };

  const toggleGoLive = () => {
    const next = { ...setup, master: { ...setup.master, goLive: !setup.master.goLive } };
    setSetup(next);
    saveTradingSetup(next);
  };

  return (
    <AppFrame title="Dashboard" subtitle="Execution console, risk intelligence, and strategy runtime visibility.">
      <section className="dash-top-strip">
        <div className="dash-mini-grid">
          <div>
            <div className="summary-label">INITIAL</div>
            <div className="metric-positive">
              INR {(funds?.initial_capital ?? setup.capitalRisk.initialCapital).toLocaleString("en-IN")}
            </div>
          </div>
          <div>
            <div className="summary-label">USED</div>
            <div className="chip-risk-medium">INR {estimatedMarginUsed.toLocaleString("en-IN")}</div>
          </div>
          <div>
            <div className="summary-label">AVAILABLE</div>
            <div className="metric-positive">INR {availableCash.toLocaleString("en-IN")}</div>
          </div>
          <div>
            <div className="summary-label">NIFTY</div>
            <div className="metric-neutral">23,639.15</div>
          </div>
          <div>
            <div className="summary-label">BANKNIFTY</div>
            <div className="metric-neutral">55,100.95</div>
          </div>
          <div>
            <div className="summary-label">SENSEX</div>
            <div className="metric-neutral">76,034.42</div>
          </div>
        </div>
      </section>

      <section className="dash-actions">
        <button className={`action-button ${setup.master.goLive ? "resume" : ""}`} onClick={toggleGoLive}>
          {setup.master.goLive ? "Go Live: ON" : "Go Live"}
        </button>
        <button
          className={`action-button ${setup.master.engineRunning ? "pause" : "resume"}`}
          onClick={() => toggleEngine(!setup.master.engineRunning)}
        >
          {setup.master.engineRunning ? "Stop Trading" : "Start Trading"}
        </button>
        <span
          className={`dash-status-chip ${
            setup.master.kiteStatus === "connected" || setup.master.kiteStatus === "shared"
              ? "chip-status-active"
              : "chip-status-paused"
          }`}
          title={
            setup.master.kiteStatus === "connected"
              ? "Direct Kite connection (Admin)"
              : setup.master.kiteStatus === "shared"
                ? "Using shared Kite API (Admin's broker)"
                : "Connect Kite in Settings (Admin) or ensure Shared API is online"
          }
        >
          Kite:{" "}
          {setup.master.kiteStatus === "connected"
            ? "Connected"
            : setup.master.kiteStatus === "shared"
              ? "Shared"
              : "Not Available"}
        </span>
      </section>

      {!!runtimeMessage && <div className="notice warning">{runtimeMessage}</div>}

      <div className="notice" style={{ marginBottom: 12, opacity: 0.9 }}>
        <strong>Auto-execute:</strong> Uses the same thresholds as SIGNALS (catalog autoTradeScoreThreshold, confidence ≥ 80). Opens at most a few per cycle when the engine is running; check Admin decision logs if nothing fires.{" "}
        <strong>Paper</strong> — tracked only; <strong>Live</strong> — executed in real broker (Admin&apos;s Kite). Config per user in Settings.
      </div>

      <section className="dash-statusbar">
        <span>
          STATUS: <b>{setup.master.engineRunning ? "RUNNING" : "STOPPED"}</b>
        </span>
        <span>
          Mode: <b>{setup.master.mode}</b>
        </span>
        <span>
          Trades today: <b>{closedTrades}</b>
        </span>
        <span>
          Max: <b>{setup.master.maxTrades}</b>
        </span>
        <span>
          Kite:{" "}
          <b>
            {setup.master.kiteStatus === "connected"
              ? "Connected"
              : setup.master.kiteStatus === "shared"
                ? "Shared"
                : "Not Available"}
          </b>
        </span>
        <span>
          Trading: <b>{setup.master.engineRunning ? "Active" : "Inactive"}</b>
        </span>
        <span>
          Shared API: <b>{setup.master.sharedApiConnected ? "Online" : "Offline"}</b>
        </span>
        <span>
          Platform API: <b>{setup.master.platformApiOnline ? "Online" : "Offline"}</b>
        </span>
        <span
          title={
            activeStrategyBanner
              ? `${activeStrategyBanner.strategyId} · v${activeStrategyBanner.strategyVersion}`
              : "Open Settings and save after changing subscription so local cache matches the server."
          }
        >
          Strategy:{" "}
          <b>{activeStrategyBanner?.displayName ?? setup.strategy.strategyName}</b>
        </span>
        <span className="dash-clock" suppressHydrationWarning>{clock}</span>
      </section>

      <section className="dash-kpi-grid">
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">OPEN TRADES</div>
          <div className="summary-value">{openPositions}</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">CLOSED TRADES</div>
          <div className="summary-value metric-positive">{closedTrades}</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">GROSS P&L</div>
          <div className={grossPnl >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>
            {formatInr(grossPnl)}
          </div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">CHARGES TODAY</div>
          <div className="summary-value chip-risk-medium">-{formatInr(chargesToday)}</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">NET P&L (REALIZED)</div>
          <div className={netPnl >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>{formatInr(netPnl)}</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">UNREALIZED P&L</div>
          <div className={unrealized >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>
            {formatInr(unrealized)}
          </div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">WIN RATE TODAY</div>
          <div className="summary-value metric-positive">{winRate}%</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">TARGET PROGRESS</div>
          <div className={targetProgress >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>{targetProgress}%</div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${targetProgress}%` }} />
          </div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">AVG P&L / TRADE</div>
          <div className={avgPerTrade >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>{formatInr(avgPerTrade)}</div>
        </div>
        <div className="dash-kpi-card panel-accent-risk">
          <div className="summary-label">CURRENT STREAK</div>
          <div
            className="summary-value metric-positive"
            title="Consecutive weeks (IST, Mon start) with at least one closed trade; closed_at stored as UTC-naive, grouped by IST date"
          >
            {tradingWeekStreakLabel}
          </div>
        </div>
      </section>

      <section className="dash-chart-grid">
        <div className="table-card panel-accent-chain">
          <div className="dash-chart-header">
            <div className="panel-title">INTRADAY P&L</div>
            <div className={`dash-chart-pnl-box ${netPnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>
              {netPnl >= 0 ? "+" : ""}₹{netPnl.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>
          <div className="dash-chart-wrap">
            <IntradayPnlChart data={intradayPnlData} closedTradesCount={closedTrades} />
          </div>
        </div>
        <div className="table-card panel-accent-signals">
          <div className="dash-signals-head">
            <div className="dash-signals-head-text">
              <div className="panel-title">SIGNALS</div>
              <p className="dash-signals-sub muted">{STRATEGY_SIGNALS_SUBTITLE}</p>
            </div>
            <button type="button" className="dash-signal-refresh" title="Auto-refresh enabled" aria-label="Auto refresh enabled">
              ↻
            </button>
          </div>
          <div className="dash-signals">
            {signalExecuteError && (
              <div className="notice error dash-signals-notice" role="alert">
                {signalExecuteError}
              </div>
            )}
            {!setup.master.engineRunning ? (
              <div className="dash-signal-empty">Engine not running — click Start Trading.</div>
            ) : signals.length === 0 ? (
              <div className="dash-signal-empty">{STRATEGY_SIGNALS_EMPTY}</div>
            ) : (
              signals.map((s) => {
                const busyPaper = signalExecuteLoading === `${s.recommendation_id}|PAPER`;
                const busyLive = signalExecuteLoading === `${s.recommendation_id}|LIVE`;
                const busyAny = busyPaper || busyLive;
                const hasOpenPaper =
                  openTradesRows.some(
                    (t) => t.symbol === s.symbol && String(t.mode || "").toUpperCase() === "PAPER"
                  ) || optimisticOpenKeys.has(`${s.symbol}|PAPER`);
                const hasOpenLive =
                  openTradesRows.some(
                    (t) => t.symbol === s.symbol && String(t.mode || "").toUpperCase() === "LIVE"
                  ) || optimisticOpenKeys.has(`${s.symbol}|LIVE`);
                return (
                  <div key={s.recommendation_id} className="dash-signal-card">
                    <div className="dash-signal-row-top">
                      <div className="dash-signal-symbol-block">
                        <div className="dash-signal-symbol">{s.symbol}</div>
                        <div className="dash-signal-strategy-line">
                          <span className="dash-signal-strategy-label">Strategy</span>
                          <span className="dash-signal-strategy-name" title={formatSignalStrategyLabel(s)}>
                            {formatSignalStrategyLabel(s)}
                          </span>
                        </div>
                      </div>
                      <div className="dash-signal-mode-btns">
                        <button
                          type="button"
                          className="action-button dash-signal-mode-icon-btn"
                          onClick={() => executeSignal(s.recommendation_id, "PAPER", s.symbol)}
                          disabled={busyAny || hasOpenPaper}
                          title={hasOpenPaper ? "Paper trade already open for this strike" : "Open Paper trade"}
                          aria-label={hasOpenPaper ? "Paper: position already open" : "Open Paper trade"}
                        >
                          {busyPaper ? <span className="dash-signal-mode-busy">…</span> : <IconPaperSignal />}
                        </button>
                        <button
                          type="button"
                          className="action-button resume dash-signal-mode-icon-btn"
                          onClick={() => executeSignal(s.recommendation_id, "LIVE", s.symbol)}
                          disabled={busyAny || hasOpenLive}
                          title={hasOpenLive ? "Live trade already open for this strike" : "Open Live trade"}
                          aria-label={hasOpenLive ? "Live: position already open" : "Open Live trade"}
                        >
                          {busyLive ? <span className="dash-signal-mode-busy">…</span> : <IconLiveSignal />}
                        </button>
                      </div>
                    </div>
                    <div className="dash-signal-row dash-signal-row--grid3">
                      <span className="dash-signal-key">LTP</span>
                      <span className="dash-signal-val signal-ltp">{s.entry_price?.toFixed(2) ?? "—"}</span>
                      <span className="dash-signal-key">E9</span>
                      <span className="dash-signal-val">{s.ema9?.toFixed(2) ?? "—"}</span>
                      <span className="dash-signal-key">E21</span>
                      <span className="dash-signal-val">{s.ema21?.toFixed(2) ?? "—"}</span>
                    </div>
                    {s.trendpulse?.tier1 ? (
                      <div className="dash-signal-row dash-signal-row--full">
                        <span className="dash-signal-key">Tier 1</span>
                        <span className="dash-signal-val dash-signal-val--wrap" title="Index signal">
                          {s.trendpulse.tier1.cross ?? s.trendpulse.cross ?? "—"} · HTF {s.trendpulse.tier1.htf_bias ?? s.trendpulse.htf_bias ?? "—"}
                          {s.trendpulse.tier1.adx != null ? ` · ADX ${Number(s.trendpulse.tier1.adx).toFixed(1)}` : ""}
                        </span>
                      </div>
                    ) : null}
                    {s.trendpulse?.tier2 ? (
                      <div className="dash-signal-row dash-signal-row--full">
                        <span className="dash-signal-key">Tier 2</span>
                        <span className="dash-signal-val dash-signal-val--wrap" title="Strike filters">
                          Δ {s.trendpulse.tier2.delta != null ? s.trendpulse.tier2.delta.toFixed(2) : s.delta != null ? s.delta.toFixed(2) : "—"}
                          {s.trendpulse.tier2.extrinsic_share != null
                            ? ` · TV ${(s.trendpulse.tier2.extrinsic_share * 100).toFixed(0)}%`
                            : ""}
                          {s.option_type ? ` · ${s.option_type}` : ""}
                          {s.trendpulse.tier2.expiry ? ` · ${s.trendpulse.tier2.expiry}` : ""}
                        </span>
                      </div>
                    ) : null}
                    <div className="dash-signal-row dash-signal-row--grid2">
                      <span className="dash-signal-key">Score</span>
                      <span className="dash-signal-val">{s.score != null ? (s.score_max != null ? `${s.score}/${s.score_max}` : String(s.score)) : "—"}</span>
                      <span className="dash-signal-key">Conf.</span>
                      <span className="dash-signal-val">
                        {s.confidence_score != null ? (
                          <span className="chip chip-status-active">{Number(s.confidence_score).toFixed(2)}</span>
                        ) : (
                          "—"
                        )}
                      </span>
                    </div>
                    <div className="dash-signal-row dash-signal-row--grid3">
                      <span className="dash-signal-key">RSI</span>
                      <span className="dash-signal-val">{s.rsi?.toFixed(2) ?? "—"}</span>
                      <span className="dash-signal-key">VWAP</span>
                      <span className="dash-signal-val">{s.vwap?.toFixed(2) ?? "—"}</span>
                      <span className="dash-signal-key">IVR</span>
                      <span
                        className="dash-signal-val"
                        title="IV rank proxy within this expiry chain (0–100; higher vs other strikes)"
                      >
                        {typeof s.ivr === "number" ? s.ivr.toFixed(1) : "—"}
                      </span>
                    </div>
                    <div className="dash-signal-row-bottom">
                      <span>
                        <span className="dash-signal-tf">{s.timeframe ?? "3m"} TF</span>
                        <span className="dash-signal-key"> · refreshes every {s.refresh_interval_sec ?? 20}s</span>
                      </span>
                      <span className="dash-signal-time">
                        {s.created_at
                          ? formatTimeIST(s.created_at, { fallback: "—" })
                          : formatTimeIST(new Date(), { fallback: "—" })}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </section>

      <section className="table-card panel-accent-risk">
        <div className="panel-title">OPEN POSITIONS</div>
        <div className="table-wrap">
          <table className="market-table">
            <colgroup>
              <col className="col-symbol" />
            </colgroup>
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleOpenSort("symbol")}>SYMBOL {openSortCol === "symbol" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th>STRATEGY</th>
                <th title="BUY = long option; SELL = short premium">SIDE</th>
                <th className="sortable-th" onClick={() => handleOpenSort("mode")}>MODE {openSortCol === "mode" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("manual_execute")}>TAKEN BY {openSortCol === "manual_execute" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th>SCORE</th>
                <th>CONFIDENCE</th>
                <th className="sortable-th" onClick={() => handleOpenSort("entry_price")}>ENTRY {openSortCol === "entry_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("current_price")}>LTP {openSortCol === "current_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("qty")}>QTY {openSortCol === "qty" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th
                  className="sortable-th"
                  title="Long: SL below entry, target above. Short (sell premium): SL above entry, target below."
                  onClick={() => handleOpenSort("stop_loss_price")}
                >
                  SL {openSortCol === "stop_loss_price" && (openSortDir === "asc" ? "↑" : "↓")}
                </th>
                <th
                  className="sortable-th"
                  title="Long: SL below entry, target above. Short (sell premium): SL above entry, target below."
                  onClick={() => handleOpenSort("target_price")}
                >
                  TARGET {openSortCol === "target_price" && (openSortDir === "asc" ? "↑" : "↓")}
                </th>
                <th className="sortable-th" onClick={() => handleOpenSort("unrealized_pnl")}>P&L {openSortCol === "unrealized_pnl" && (openSortDir === "asc" ? "↑" : "↓")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedOpenTrades.length === 0 ? (
                <tr>
                  <td colSpan={13} className="empty-state">
                    No open positions
                  </td>
                </tr>
              ) : (
                sortedOpenTrades.map((t) => (
                  <tr key={t.trade_ref}>
                    <td>{t.symbol}</td>
                    <td className="summary-label">{t.strategy_name || "—"}</td>
                    <td>
                      <span className="chip chip-status-paused">{tradeSideLabel(t.side)}</span>
                    </td>
                    <td>
                      <span className={`chip ${String(t.mode || "").toUpperCase() === "LIVE" ? "chip-status-active" : "chip-status-paused"}`}>
                        {String(t.mode || "PAPER").toUpperCase()}
                      </span>
                    </td>
                    <td>
                      <span className={`chip ${t.manual_execute === false ? "chip-status-active" : "chip-status-paused"}`}>
                        {t.manual_execute === false ? "Auto" : t.manual_execute === true ? "Manual" : "—"}
                      </span>
                    </td>
                    <td>{t.score != null ? String(t.score) : "—"}</td>
                    <td>{t.confidence_score != null ? Number(t.confidence_score).toFixed(2) : "—"}</td>
                    <td>{Number(t.entry_price).toFixed(2)}</td>
                    <td>{Number(t.current_price || t.entry_price).toFixed(2)}</td>
                    <td>{t.qty ?? t.quantity ?? 0}</td>
                    <td>{t.stop_loss_price != null ? Number(t.stop_loss_price).toFixed(2) : "--"}</td>
                    <td>{t.target_price != null ? Number(t.target_price).toFixed(2) : "--"}</td>
                    <td className={Number(t.unrealized_pnl || 0) >= 0 ? "metric-positive" : "chip-risk-high"}>
                      {Number(t.unrealized_pnl || 0).toFixed(2)}
                  </td>
                </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="table-card panel-accent-chain">
        <div className="panel-title">TODAY'S CLOSED TRADES</div>
        <div className="table-wrap">
          <table className="market-table">
            <colgroup>
              <col className="col-symbol" />
            </colgroup>
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleClosedSort("symbol")}>SYMBOL {closedSortCol === "symbol" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th>STRATEGY</th>
                <th title="BUY = long option; SELL = short premium">SIDE</th>
                <th className="sortable-th" onClick={() => handleClosedSort("mode")}>MODE {closedSortCol === "mode" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("manual_execute")}>TAKEN BY {closedSortCol === "manual_execute" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th>SCORE</th>
                <th>CONFIDENCE</th>
                <th className="sortable-th" onClick={() => handleClosedSort("opened_at")}>ENTRY TIME (IST) {closedSortCol === "opened_at" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("entry_price")}>ENTRY {closedSortCol === "entry_price" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("closed_at")}>EXIT TIME (IST) {closedSortCol === "closed_at" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("current_price")}>EXIT {closedSortCol === "current_price" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("qty")}>QTY {closedSortCol === "qty" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("realized_pnl")}>P&L {closedSortCol === "realized_pnl" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleClosedSort("reason")}>REASON {closedSortCol === "reason" && (closedSortDir === "asc" ? "↑" : "↓")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedClosedTrades.length === 0 ? (
                <tr>
                  <td colSpan={14} className="empty-state">
                    No closed trades today (IST calendar day)
                  </td>
                </tr>
              ) : (
                sortedClosedTrades.map((t: any, i) => {
                  const pnl = Number(t.pnl ?? t.realized_pnl ?? 0);
                  const isProfit = pnl >= 0;
                  const takenBy = t.manual_execute === false ? "Auto" : t.manual_execute === true ? "Manual" : "—";
                  return (
                    <tr key={`${t.trade_ref || t.symbol}-${i}`}>
                      <td>{t.symbol}</td>
                      <td className="summary-label">{t.strategy_name || "—"}</td>
                      <td>
                        <span className="chip chip-status-paused">{tradeSideLabel(t.side)}</span>
                      </td>
                      <td>
                        <span className="chip chip-status-active">{t.mode || "PAPER"}</span>
                      </td>
                      <td>
                        <span className={`chip ${t.manual_execute === false ? "chip-status-active" : "chip-status-paused"}`}>
                          {takenBy}
                        </span>
                      </td>
                      <td>{t.score != null ? String(t.score) : "—"}</td>
                      <td>{t.confidence_score != null ? Number(t.confidence_score).toFixed(2) : "—"}</td>
                      <td>{formatTime(t.opened_at ?? t.buyTime)}</td>
                      <td>{Number(t.entry ?? t.entry_price ?? 0).toFixed(2)}</td>
                      <td>{formatTime(t.closed_at ?? t.sellTime)}</td>
                      <td>{Number(t.exit ?? t.current_price ?? 0).toFixed(2)}</td>
                      <td>{t.qty ?? t.quantity ?? 0}</td>
                      <td className={isProfit ? "metric-positive" : "chip-risk-high"}>
                        {isProfit ? "+" : ""}
                        {pnl.toFixed(2)}
                      </td>
                      <td>{t.reason || "Manual"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </AppFrame>
  );
}
