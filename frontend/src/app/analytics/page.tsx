"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { getAuthHeaders, postTradesRefreshCycle } from "@/lib/api_client";
import { formatDateTimeIST } from "@/lib/datetime_ist";

type BuildupType = "Long Buildup" | "Short Buildup" | "Short Covering" | "Long Unwinding" | "—";

type StrikeRow = {
  strike: number;
  call: {
    buildup: BuildupType;
    oiChgPct?: number;
    theta: number;
    delta: number;
    iv: number;
    ivr?: number;
    volume: string;
    oi: string;
    ltpChg: number;
    ltp: number;
  };
  put: {
    pcr: number;
    ltp: number;
    ltpChg: number;
    oi: string;
    oiChgPct?: number;
    volume: string;
    iv: number;
    ivr?: number;
    delta: number;
    theta: number;
    buildup: BuildupType;
  };
};

type OptionChainResponse = {
  spot: number;
  spotChgPct: number;
  vix: number | null;
  synFuture: number | null;
  pcr: number;
  pcrVol: number;
  updated: string | null;
  chain: StrikeRow[];
  error?: string;
  from_cache?: boolean;
  cached_at?: string;
  using_live_broker?: boolean;
  broker_session_ok?: boolean;
  credentials_present?: boolean;
  active_broker?: string | null;
  market_data_quote_source?: string | null;
  session_hint?: string | null;
};

type IndicesSpot = { spot: number; spotChgPct: number };
type IndicesData = { NIFTY: IndicesSpot; BANKNIFTY: IndicesSpot; SENSEX: IndicesSpot };

const apiBase = typeof window !== "undefined" ? "" : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const INSTRUMENTS = [
  { label: "NIFTY 50", value: "NIFTY" },
  { label: "BANK NIFTY", value: "BANKNIFTY" },
  { label: "FIN NIFTY", value: "FINNIFTY" },
  { label: "SENSEX", value: "SENSEX" },
];
const STRIKE_RANGE_OPTIONS = [5, 10, 15, 20];

function fmtPct(n: number): string {
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`;
}

function formatVolOi(val: string): string {
  const n = Number(val);
  if (!Number.isFinite(n)) return val;
  if (n >= 10000000) return `${(n / 10000000).toFixed(2)}Cr`;
  if (n >= 100000) return `${(n / 100000).toFixed(2)}L`;
  return n.toLocaleString("en-IN");
}

export default function AnalyticsPage() {
  const [instrument, setInstrument] = useState("NIFTY");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiry, setExpiry] = useState("");
  const [expiriesLoading, setExpiriesLoading] = useState(true);
  const [strikesUp, setStrikesUp] = useState(10);
  const [strikesDown, setStrikesDown] = useState(10);

  const [refreshSeconds, setRefreshSeconds] = useState(30);
  const [requireLiveBroker, setRequireLiveBroker] = useState(false);
  const [recentWindowFetches, setRecentWindowFetches] = useState(10);
  const [indicesData, setIndicesData] = useState<IndicesData | null>(null);
  const [data, setData] = useState<OptionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimitMessage, setRateLimitMessage] = useState<string | null>(null);
  const [chainSortDir, setChainSortDir] = useState<"asc" | "desc">("asc");
  const [brokerSessionOk, setBrokerSessionOk] = useState<boolean | null>(null);
  const [credentialsPresent, setCredentialsPresent] = useState<boolean | null>(null);
  const [expirySource, setExpirySource] = useState<string | null>(null);
  const [sessionHint, setSessionHint] = useState<string | null>(null);
  const [marketDataQuoteSource, setMarketDataQuoteSource] = useState<string | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const chainInFlightRef = useRef(false);

  const sortedChain = useMemo(() => {
    const c = data?.chain ?? [];
    if (chainSortDir === "asc") return c;
    return [...c].reverse();
  }, [data?.chain, chainSortDir]);

  const handleChainSort = () => {
    setChainSortDir((d) => (d === "asc" ? "desc" : "asc"));
  };

  const fetchChain = useCallback(
    async (showSpinner = true) => {
      if (!expiry) return;
      if (chainInFlightRef.current) return;
      chainInFlightRef.current = true;
      if (showSpinner) setLoading(true);
      setError(null);
      try {
        try {
          await Promise.race([
            postTradesRefreshCycle(),
            new Promise((resolve) => window.setTimeout(resolve, 700)),
          ]);
        } catch {
          /* still load chain */
        }
        const params = new URLSearchParams({
          instrument,
          expiry,
          strikes_up: String(strikesUp),
          strikes_down: String(strikesDown),
        });
        const res = await fetch(`${apiBase}/api/analytics/option-chain?${params}`, { headers: getAuthHeaders() });
        const json = await res.json();
        if (!res.ok) {
          if (res.status === 429) {
            setRateLimitMessage(json?.detail || "Market data rate limit. Showing previous data.");
          } else if (res.status === 401) {
            setError(
              typeof json?.detail === "string"
                ? json.detail
                : "Market data session invalid. Check Settings → Brokers, then refresh."
            );
            setBrokerSessionOk(false);
          } else {
            setError(json?.detail || res.statusText || "Failed to load option chain");
          }
          return;
        }
        setData(json);
        setLastRefreshAt(json?.updated ?? new Date().toISOString());
        setRateLimitMessage(json.from_cache ? "Showing cached data (rate limited)." : null);
        if (typeof json.broker_session_ok === "boolean") setBrokerSessionOk(json.broker_session_ok);
        if (typeof json.credentials_present === "boolean") setCredentialsPresent(json.credentials_present);
        if (typeof json.session_hint === "string") setSessionHint(json.session_hint);
        if (typeof json.market_data_quote_source === "string") setMarketDataQuoteSource(json.market_data_quote_source);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Network error");
      } finally {
        chainInFlightRef.current = false;
        if (showSpinner) setLoading(false);
      }
    },
    [instrument, expiry, strikesUp, strikesDown]
  );

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/analytics/config`, { headers: getAuthHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((json: { option_chain_refresh_seconds?: number; require_live_broker?: boolean; recent_window_fetches?: number } | null) => {
        if (cancelled || !json) return;
        const sec = json.option_chain_refresh_seconds;
        if (typeof sec === "number" && sec >= 5 && sec <= 300) setRefreshSeconds(sec);
        if (typeof json.require_live_broker === "boolean") setRequireLiveBroker(json.require_live_broker);
        if (typeof json.recent_window_fetches === "number") setRecentWindowFetches(json.recent_window_fetches);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setExpiriesLoading(true);
    setExpiries([]);
    fetch(`${apiBase}/api/analytics/expiries?instrument=${encodeURIComponent(instrument)}`, { headers: getAuthHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then(
        (
          json: {
            expiries?: string[];
            broker_session_ok?: boolean;
            credentials_present?: boolean;
            expiry_source?: string;
            session_hint?: string;
            market_data_quote_source?: string;
          } | null
        ) => {
        if (cancelled) return;
        if (!json) {
          setExpiries([]);
          setExpiry("");
          setBrokerSessionOk(false);
          setCredentialsPresent(null);
          setExpirySource(null);
          setSessionHint(null);
          setMarketDataQuoteSource(null);
          return;
        }
        const list = Array.isArray(json.expiries) ? json.expiries : [];
        setExpiries(list);
        setExpiry((prev) => (list.includes(prev) ? prev : list[0] || ""));
        setBrokerSessionOk(typeof json.broker_session_ok === "boolean" ? json.broker_session_ok : false);
        setCredentialsPresent(typeof json.credentials_present === "boolean" ? json.credentials_present : null);
        setExpirySource(json.expiry_source ?? null);
        setSessionHint(typeof json.session_hint === "string" ? json.session_hint : null);
        setMarketDataQuoteSource(typeof json.market_data_quote_source === "string" ? json.market_data_quote_source : null);
      }
      )
      .catch(() => {
        if (!cancelled) setExpiries([]);
      })
      .finally(() => {
        if (!cancelled) setExpiriesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [instrument]);

  useEffect(() => {
    fetchChain(true);
  }, [fetchChain]);

  useEffect(() => {
    const t = setInterval(() => fetchChain(false), refreshSeconds * 1000);
    return () => clearInterval(t);
  }, [fetchChain, refreshSeconds]);

  const fetchIndices = useCallback(() => {
    fetch(`${apiBase}/api/analytics/indices`, { headers: getAuthHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((json: IndicesData | null) => {
        if (json) setIndicesData(json);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    fetchIndices();
    const t = setInterval(fetchIndices, 30000);
    return () => clearInterval(t);
  }, [fetchIndices]);

  const chain = data?.chain || [];
  const spot = data?.spot || 0;
  const atmStrike = useMemo(() => {
    const step = instrument === "NIFTY" ? 50 : 100;
    return spot ? Math.round(spot / step) * step : 0;
  }, [instrument, spot]);
  const updated = data?.updated ? formatDateTimeIST(data.updated, "—", { seconds: true }) : "—";

  return (
    <AdminGuard>
      <AppFrame
        title="Option Chain Analytics"
        subtitle="NiftyAlgo-style live option chain with expiries, Greeks, buildup, and market strip."
      >
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "0.6rem" }}>
        <span className="summary-label">
          Last refresh: {lastRefreshAt ? formatDateTimeIST(lastRefreshAt, "—", { seconds: true }) : "—"}
        </span>
      </div>
      <section className="analytics-market-strip panel-accent-chain">
        <div className="market-strip-label">NSE MARKET</div>
        <div className="market-strip-items">
          <div>
            <div className="summary-label">NIFTY 50</div>
            <div className={instrument === "NIFTY" ? "metric-positive" : "metric-neutral"}>
              {indicesData?.NIFTY?.spot
                ? `${indicesData.NIFTY.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${fmtPct(indicesData.NIFTY.spotChgPct || 0)}`
                : "—"}
            </div>
          </div>
          <div>
            <div className="summary-label">BANK NIFTY</div>
            <div className={instrument === "BANKNIFTY" ? "metric-positive" : "metric-neutral"}>
              {indicesData?.BANKNIFTY?.spot
                ? `${indicesData.BANKNIFTY.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${fmtPct(indicesData.BANKNIFTY.spotChgPct || 0)}`
                : "—"}
            </div>
          </div>
          <div>
            <div className="summary-label">SENSEX</div>
            <div className={instrument === "SENSEX" ? "metric-positive" : "metric-neutral"}>
              {indicesData?.SENSEX?.spot
                ? `${indicesData.SENSEX.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${fmtPct(indicesData.SENSEX.spotChgPct || 0)}`
                : "—"}
            </div>
          </div>
        </div>
      </section>

      {rateLimitMessage && <div className="notice warning">{rateLimitMessage}</div>}
      {error && <div className="notice error">{error}</div>}
      {requireLiveBroker && brokerSessionOk === false && sessionHint && (
        <div className="notice error" role="alert">
          {sessionHint}
        </div>
      )}
      {requireLiveBroker && brokerSessionOk === false && !sessionHint && (
        <div className="notice error" role="alert">
          {credentialsPresent === false
            ? "No market-data credentials resolved for option chain (Zerodha). Connect under Settings → Brokers or use admin shared Zerodha for paper."
            : "Market data session is not valid. Reconnect Zerodha under Settings → Brokers, then refresh."}
        </div>
      )}
      {requireLiveBroker && brokerSessionOk && expirySource === "estimated_weeklies" && (
        <div className="notice warning">
          Using estimated weekly dates for expiry — real NFO dates could not be loaded. If this persists, fix the Zerodha market-data session under Settings → Brokers.
        </div>
      )}
      {requireLiveBroker && brokerSessionOk && expirySource === "zerodha_nfo" && (
        <div className="notice warning">
          Expiries loaded from broker NFO. OI/Volume change columns use the last {recentWindowFetches} in-memory fetches only.
        </div>
      )}
      {!requireLiveBroker && (
        <div className="notice info">
          Live market data not required (OPTION_CHAIN_REQUIRE_LIVE=0). Chain may use synthetic data when Zerodha quotes are unavailable.
        </div>
      )}

      <section className="controls">
        <select className="control-select" value={instrument} onChange={(e) => setInstrument(e.target.value)}>
          {INSTRUMENTS.map((x) => (
            <option key={x.value} value={x.value}>
              {x.label}
            </option>
          ))}
        </select>

        <select
          className="control-select"
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
          disabled={expiriesLoading || expiries.length === 0}
        >
          {expiriesLoading && <option>Loading expiries...</option>}
          {!expiriesLoading && expiries.length === 0 && <option>No expiries available</option>}
          {!expiriesLoading &&
            expiries.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
        </select>

        <select className="control-select" value={strikesDown} onChange={(e) => setStrikesDown(Number(e.target.value))}>
          {STRIKE_RANGE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n} down
            </option>
          ))}
        </select>

        <select className="control-select" value={strikesUp} onChange={(e) => setStrikesUp(Number(e.target.value))}>
          {STRIKE_RANGE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n} up
            </option>
          ))}
        </select>

        <button className="action-button" onClick={() => fetchChain(true)}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        {brokerSessionOk !== null && (
          <span
            className={`chip ${brokerSessionOk ? "chip-status-active" : "chip-status-paused"}`}
            title={sessionHint || "Zerodha Kite used for indices and option chain market data"}
          >
            Market data
            {marketDataQuoteSource === "user_zerodha"
              ? " (your Zerodha)"
              : marketDataQuoteSource === "user_fyers"
                ? " (your FYERS)"
              : marketDataQuoteSource === "platform_shared"
                ? " (shared Zerodha)"
              : marketDataQuoteSource === "platform_only_unavailable"
                ? " (admin shared — unavailable)"
                : marketDataQuoteSource === "pool_or_env"
                  ? " (server)"
                  : ""}
            : {brokerSessionOk ? "OK" : "unavailable"}
          </span>
        )}
        {data?.using_live_broker === false && brokerSessionOk && (
          <span className="chip chip-status-paused" title="This response used synthetic chain">
            Chain: synthetic
          </span>
        )}
        {!loading && chain.length > 0 && (
          <span className="live-pill">
            <span className="live-dot" />
            Live
          </span>
        )}
      </section>

      <section className="summary-grid">
        <div className="summary-card panel-accent-risk">
          <div className="summary-label">SPOT</div>
          <div className="summary-value">{spot ? spot.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—"}</div>
        </div>
        <div className="summary-card panel-accent-risk">
          <div className="summary-label">VIX</div>
          <div className="summary-value">{data?.vix ?? "—"}</div>
        </div>
        <div className="summary-card panel-accent-risk">
          <div className="summary-label">SYN FUTURE</div>
          <div className="summary-value">{data?.synFuture ?? "—"}</div>
        </div>
        <div className="summary-card panel-accent-risk">
          <div className="summary-label">PCR</div>
          <div className="summary-value">{(data?.pcr ?? 0).toFixed(2)}</div>
        </div>
      </section>

      <section className="analytics-legend">
        <span className="summary-label">Legend:</span>
        <span className="chip chip-legend itm">ITM</span>
        <span className="chip chip-legend atm">ATM</span>
        <span className="chip chip-legend long">Long Buildup</span>
        <span className="chip chip-legend short">Short Buildup</span>
        <span className="chip chip-legend cover">Short Covering</span>
        <span className="chip chip-legend unwind">Long Unwinding</span>
      </section>

      <section className="table-card panel-accent-chain">
        <div className="panel-title analytics-panel-title">
          <span>Option Chain</span>
          <span className="updated-label">Updated: {updated}</span>
        </div>
        <div className="table-wrap">
          <table className="market-table analytics-table">
            <colgroup>
              <col className="col-buildup" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-vol" />
              <col className="col-vol" />
              <col className="col-pct" />
              <col className="col-pct" />
              <col className="col-price" />
              <col className="col-strike" />
              <col className="col-pct" />
              <col className="col-price" />
              <col className="col-pct" />
              <col className="col-vol" />
              <col className="col-pct" />
              <col className="col-vol" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-greek" />
              <col className="col-buildup" />
            </colgroup>
            <thead>
              <tr>
                <th colSpan={10}>CALLS</th>
                <th className="sortable-th" onClick={handleChainSort}>STRIKE {chainSortDir === "asc" ? "↑" : "↓"}</th>
                <th colSpan={11}>PUTS</th>
              </tr>
              <tr>
                <th title="Buildup — OI change type: Long Buildup, Short Buildup, Short Covering, Long Unwinding">BUILDUP</th>
                <th title="Theta — Time decay; rate of option price change per day">THETA</th>
                <th title="Delta — Sensitivity of option price to ₹1 move in underlying">DELTA</th>
                <th title="Implied Volatility %">IV%</th>
                <th title="IV Rank (0-100). IVR&lt;20 = cheap options, +1 score bonus">IVR%</th>
                <th title="Volume">VOLUME</th>
                <th title="Open Interest">OI</th>
                <th title="OI Change % — Change in open interest vs previous">OI CHG%</th>
                <th title="LTP Change % — Change in last traded price">LTP CHG%</th>
                <th title="Last Traded Price — Most recent transaction price">LTP</th>
                <th />
                <th title="Put-Call Ratio — Put OI ÷ Call OI at this strike">PCR</th>
                <th title="Last Traded Price — Most recent transaction price">LTP</th>
                <th title="LTP Change % — Change in last traded price">LTP CHG%</th>
                <th title="Open Interest — Total outstanding contracts">OI</th>
                <th title="OI Change % — Change in open interest vs previous">OI CHG%</th>
                <th title="Volume">VOLUME</th>
                <th title="Implied Volatility %">IV%</th>
                <th title="IV Rank (0-100). IVR&lt;20 = cheap options, +1 score bonus">IVR%</th>
                <th title="Delta">DELTA</th>
                <th title="Theta — Time decay; rate of option price change per day">THETA</th>
                <th title="Buildup — OI change type: Long Buildup, Short Buildup, Short Covering, Long Unwinding">BUILDUP</th>
              </tr>
            </thead>
            <tbody>
              {chain.length === 0 && !loading && (
                <tr>
                  <td colSpan={22} className="empty-state">
                    No option chain data for this selection.
                  </td>
                </tr>
              )}
              {sortedChain.map((row) => {
                const atm = row.strike === atmStrike;
                const strikeClass = atm ? "atm-strike" : "";
                return (
                  <tr key={row.strike}>
                    <td className={`buildup-cell ${row.call.buildup.replace(" ", "-").toLowerCase()}`}>{row.call.buildup}</td>
                    <td className="num-cell">{row.call.theta?.toFixed(2) || "—"}</td>
                    <td className="num-cell">{row.call.delta?.toFixed(3) || "—"}</td>
                    <td className="num-cell">{row.call.iv ? `${row.call.iv.toFixed(2)}%` : "—"}</td>
                    <td className="num-cell" title={typeof row.call.ivr === "number" && row.call.ivr < 20 ? "IVR<20: +1 score bonus" : ""}>
                      {typeof row.call.ivr === "number" ? row.call.ivr.toFixed(1) : "—"}
                    </td>
                    <td className="num-cell">{formatVolOi(row.call.volume)}</td>
                    <td className="num-cell">{formatVolOi(row.call.oi)}</td>
                    <td className="num-cell">{typeof row.call.oiChgPct === "number" ? fmtPct(row.call.oiChgPct) : "—"}</td>
                    <td className="num-cell">{fmtPct(row.call.ltpChg || 0)}</td>
                    <td className="num-cell">{row.call.ltp.toFixed(2)}</td>
                    <td className={`strike-cell ${strikeClass}`}>{row.strike}</td>
                    <td className="num-cell">{row.put.pcr.toFixed(2)}</td>
                    <td className="num-cell">{row.put.ltp.toFixed(2)}</td>
                    <td className="num-cell">{fmtPct(row.put.ltpChg || 0)}</td>
                    <td className="num-cell">{formatVolOi(row.put.oi)}</td>
                    <td className="num-cell">{typeof row.put.oiChgPct === "number" ? fmtPct(row.put.oiChgPct) : "—"}</td>
                    <td className="num-cell">{formatVolOi(row.put.volume)}</td>
                    <td className="num-cell">{row.put.iv ? `${row.put.iv.toFixed(2)}%` : "—"}</td>
                    <td className="num-cell" title={typeof row.put.ivr === "number" && row.put.ivr < 20 ? "IVR<20: +1 score bonus" : ""}>
                      {typeof row.put.ivr === "number" ? row.put.ivr.toFixed(1) : "—"}
                    </td>
                    <td className="num-cell">{row.put.delta?.toFixed(3) || "—"}</td>
                    <td className="num-cell">{row.put.theta?.toFixed(2) || "—"}</td>
                    <td className={`buildup-cell ${row.put.buildup.replace(" ", "-").toLowerCase()}`}>{row.put.buildup}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </AppFrame>
    </AdminGuard>
  );
}
