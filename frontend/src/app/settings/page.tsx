"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import AppFrame from "@/components/AppFrame";
import {
  DEFAULT_TRADING_SETUP,
  loadTradingSetup,
  saveTradingSetup,
  type CapitalRiskSetup,
  type MasterSetup,
  type StrategySetup,
  type TradeMode,
  type TradingParametersSetup,
  type ZerodhaCredentials,
} from "@/lib/trading_setup";
import { apiJson } from "@/lib/api_client";

export default function SettingsPage() {
  const [master, setMaster] = useState<MasterSetup>(DEFAULT_TRADING_SETUP.master);
  const [credentials, setCredentials] = useState<ZerodhaCredentials>(DEFAULT_TRADING_SETUP.credentials);
  const [capitalRisk, setCapitalRisk] = useState<CapitalRiskSetup>(DEFAULT_TRADING_SETUP.capitalRisk);
  const [tradingParams, setTradingParams] = useState<TradingParametersSetup>(DEFAULT_TRADING_SETUP.tradingParameters);
  const [strategy, setStrategy] = useState<StrategySetup>(DEFAULT_TRADING_SETUP.strategy);
  const [savedAt, setSavedAt] = useState<string>("");
  const [warning, setWarning] = useState<string>("");
  const [strategyOptions, setStrategyOptions] = useState<{ strategy_id: string; version: string; display_name: string }[]>([]);
  const [loadingStrategy, setLoadingStrategy] = useState(false);
  const [connectNotice, setConnectNotice] = useState<string>("");
  const [connectingKite, setConnectingKite] = useState(false);
  const [reveal, setReveal] = useState({
    apiSecret: false,
    password: false,
    totpSecret: false,
    accessToken: false,
  });
  const [approval, setApproval] = useState<{ approved_paper: boolean; approved_live: boolean } | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  const showToast = (message: string, type: "success" | "error" | "info" = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };
  type RemoteSettings = {
    master: MasterSetup;
    credentials: ZerodhaCredentials;
    capitalRisk: CapitalRiskSetup;
    tradingParameters: TradingParametersSetup;
    strategy: StrategySetup;
  };

  const loadSettingsForStrategy = (sid: string | null, ver: string | null) => {
    const params = sid && ver ? `?strategy_id=${encodeURIComponent(sid)}&strategy_version=${encodeURIComponent(ver)}` : "";
    return apiJson<RemoteSettings>(`/api/settings${params}`)
      .then((remote) => {
        if (!remote) return;
        setMaster(remote.master);
        setCredentials(remote.credentials);
        setCapitalRisk(remote.capitalRisk);
        setTradingParams(remote.tradingParameters);
        setStrategy(remote.strategy);
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    const localSetup = loadTradingSetup();
    setMaster(localSetup.master);
    setCredentials(localSetup.credentials);
    setCapitalRisk(localSetup.capitalRisk);
    setTradingParams(localSetup.tradingParameters);
    setStrategy(localSetup.strategy);
    setSavedAt(localSetup.updatedAt);

    apiJson<{ strategy_id: string; version: string; display_name: string }[]>("/api/settings/strategy-options")
      .then((opts) => setStrategyOptions(opts ?? []))
      .catch(() => setStrategyOptions([]));

    apiJson<{ approved_paper: boolean; approved_live: boolean }>("/api/auth/me")
      .then((me) => setApproval({ approved_paper: me.approved_paper, approved_live: me.approved_live }))
      .catch(() => setApproval(null));

    loadSettingsForStrategy(null, null);
  }, []);

  const onStrategyChange = (sid: string, ver: string) => {
    setLoadingStrategy(true);
    loadSettingsForStrategy(sid, ver)
      .finally(() => setLoadingStrategy(false));
  };

  const saveAll = async () => {
    if (capitalRisk.maxLossDay >= capitalRisk.maxProfitDay) {
      setWarning("Max Loss/Day should be lower than Max Profit/Day for healthy risk profile.");
      return;
    }
    if (tradingParams.minPremium >= tradingParams.maxPremium) {
      setWarning("Min Premium must be less than Max Premium.");
      return;
    }
    if (strategy.tradeStart >= strategy.tradeEnd) {
      setWarning("Trade Start time must be earlier than Trade End time.");
      return;
    }
    if (!Object.values(strategy.indices).some(Boolean)) {
      setWarning("Enable at least one index in Strategy Settings.");
      return;
    }
    setWarning("");
    showToast("Validation passed. Saving...", "info");
    const payload = {
      master,
      credentials,
      capitalRisk,
      tradingParameters: tradingParams,
      strategy,
      updatedAt: new Date().toISOString(),
    };
    saveTradingSetup(payload);
    try {
      await apiJson("/api/settings", "PUT", payload);
      setSavedAt(new Date().toISOString());
      showToast("Settings saved. New recommendations will use updated parameters.", "success");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Failed to save settings.", "error");
    }
  };

  const connectKite = async () => {
    setConnectNotice("");
    if (!credentials.apiKey.trim() || !credentials.apiSecret.trim()) {
      setConnectNotice("API Key and API Secret are required to connect Kite.");
      return;
    }
    const hasRequestToken = !!credentials.requestToken.trim();
    const hasAccessToken = !!credentials.accessToken.trim();
    if (!hasRequestToken && !hasAccessToken) {
      setConnectNotice("Enter Request Token (to generate Access Token) or Access Token (to connect directly).");
      return;
    }
    setConnectingKite(true);
    try {
      const resp = await apiJson<{
        status: string;
        brokerConnected: boolean;
        generatedAccessToken: boolean;
        accessToken: string;
      }>("/api/settings/zerodha/connect", "POST", {
        apiKey: credentials.apiKey,
        apiSecret: credentials.apiSecret,
        requestToken: hasRequestToken ? credentials.requestToken : "",
        accessToken: hasAccessToken ? credentials.accessToken : "",
      });
      setCredentials((c) => ({ ...c, accessToken: resp.accessToken }));
      setMaster((m) => ({ ...m, brokerConnected: !!resp.brokerConnected }));
      setConnectNotice(
        resp.generatedAccessToken
          ? "Access token generated from request token and connected."
          : "Connected using access token (no generation)."
      );
    } catch (e) {
      setMaster((m) => ({ ...m, brokerConnected: false }));
      setConnectNotice(e instanceof Error ? e.message : "Kite connection failed.");
    } finally {
      setConnectingKite(false);
    }
  };

  const hasRequestToken = !!credentials.requestToken.trim();
  const hasAccessToken = !!credentials.accessToken.trim();
  const connectButtonLabel = hasAccessToken
    ? "Connect Kite"
    : "Generate Access Token & Connect Kite";

  const disconnectKite = async () => {
    setConnectNotice("");
    setConnectingKite(true);
    try {
      await apiJson<{ status: string; brokerConnected: boolean }>("/api/settings/zerodha/disconnect", "POST");
      setCredentials((c) => ({ ...c, accessToken: "" }));
      setMaster((m) => ({ ...m, brokerConnected: false }));
      setConnectNotice("Disconnected. Access token cleared.");
    } catch (e) {
      setConnectNotice(e instanceof Error ? e.message : "Kite disconnect failed.");
    } finally {
      setConnectingKite(false);
    }
  };

  const resetDefaults = () => {
    setMaster(DEFAULT_TRADING_SETUP.master);
    setCredentials(DEFAULT_TRADING_SETUP.credentials);
    setCapitalRisk(DEFAULT_TRADING_SETUP.capitalRisk);
    setTradingParams(DEFAULT_TRADING_SETUP.tradingParameters);
    setStrategy(DEFAULT_TRADING_SETUP.strategy);
    saveTradingSetup(DEFAULT_TRADING_SETUP);
    setWarning("");
    setSavedAt(new Date().toISOString());
  };

  return (
    <AppFrame title="Trading Settings" subtitle="Configure credentials, risk controls, trade filters, and strategy runtime.">
      {!!warning && <div className="notice warning">{warning}</div>}
      {toast && (
        <div className={`settings-toast settings-toast-${toast.type}`} role="status">
          {toast.message}
        </div>
      )}
      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-label">Engine Status</div>
          <div className="summary-value">{master.engineRunning ? "RUNNING" : "STOPPED"}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Trading Mode</div>
          <div className="summary-value">{master.mode}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Initial Capital</div>
          <div className="summary-value">INR {capitalRisk.initialCapital.toLocaleString("en-IN")}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Last Saved</div>
          <div className="summary-value setup-saved-at">{savedAt ? new Date(savedAt).toLocaleString("en-IN") : "—"}</div>
        </div>
      </section>

      <section className="settings-grid-2">
        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">
              <span className="settings-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="settings-svg">
                  <path d="M7 14a5 5 0 1 1 3.9 4.86L9 21H7v-2H5v-2H3v-2h4.17l1.34-1.34A4.94 4.94 0 0 1 7 14Zm8-3a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
                </svg>
              </span>{" "}
              ZERODHA CREDENTIALS
            </span>
          </div>
          <div className="form-grid">
            <div className="field field-span-2">
              <span>Connection Status</span>
              <span className={`chip ${master.brokerConnected ? "chip-status-active" : "chip-status-paused"}`}>
                {master.brokerConnected ? "Connected" : "Disconnected"}
              </span>
            </div>
            <label className="field">
              <span>API Key</span>
              <input
                className="control-input"
                value={credentials.apiKey}
                onChange={(e) => setCredentials((c) => ({ ...c, apiKey: e.target.value }))}
              />
            </label>
            <label className="field">
              <span>API Secret</span>
              <div className="settings-input-with-toggle">
                <input
                  className="control-input"
                  type={reveal.apiSecret ? "text" : "password"}
                  value={credentials.apiSecret}
                  onChange={(e) => setCredentials((c) => ({ ...c, apiSecret: e.target.value }))}
                />
                <button
                  type="button"
                  className="settings-eye-btn"
                  onClick={() => setReveal((r) => ({ ...r, apiSecret: !r.apiSecret }))}
                >
                  {reveal.apiSecret ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <label className="field">
              <span>User ID</span>
              <input
                className="control-input"
                value={credentials.userId}
                onChange={(e) => setCredentials((c) => ({ ...c, userId: e.target.value }))}
              />
            </label>
            <label className="field">
              <span>Password</span>
              <div className="settings-input-with-toggle">
                <input
                  className="control-input"
                  type={reveal.password ? "text" : "password"}
                  value={credentials.password}
                  onChange={(e) => setCredentials((c) => ({ ...c, password: e.target.value }))}
                />
                <button
                  type="button"
                  className="settings-eye-btn"
                  onClick={() => setReveal((r) => ({ ...r, password: !r.password }))}
                >
                  {reveal.password ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <label className="field field-span-2">
              <span>TOTP Secret Key</span>
              <div className="settings-input-with-toggle">
                <input
                  className="control-input"
                  type={reveal.totpSecret ? "text" : "password"}
                  value={credentials.totpSecret}
                  onChange={(e) => setCredentials((c) => ({ ...c, totpSecret: e.target.value }))}
                />
                <button
                  type="button"
                  className="settings-eye-btn"
                  onClick={() => setReveal((r) => ({ ...r, totpSecret: !r.totpSecret }))}
                >
                  {reveal.totpSecret ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <label className="field field-span-2">
              <span>Request Token</span>
              <input
                className="control-input"
                value={credentials.requestToken}
                onChange={(e) => setCredentials((c) => ({ ...c, requestToken: e.target.value }))}
                placeholder="Paste Zerodha request token from Kite login"
              />
              <small className="summary-label">Enter to generate Access Token and connect.</small>
            </label>
            <label className="field field-span-2">
              <span>Access Token</span>
              <div className="settings-input-with-toggle">
                <input
                  className="control-input"
                  type={reveal.accessToken ? "text" : "password"}
                  value={credentials.accessToken}
                  onChange={(e) => setCredentials((c) => ({ ...c, accessToken: e.target.value }))}
                  placeholder="Or paste existing access token to connect directly"
                />
                <button
                  type="button"
                  className="settings-eye-btn"
                  onClick={() => setReveal((r) => ({ ...r, accessToken: !r.accessToken }))}
                >
                  {reveal.accessToken ? "Hide" : "Show"}
                </button>
              </div>
              <small className="summary-label">Enter to connect directly (no generation).</small>
            </label>
            <div className="field field-span-2">
              <span>Kite Connect Action</span>
              <button className="action-button" onClick={connectKite} disabled={connectingKite}>
                {connectingKite ? "Connecting..." : connectButtonLabel}
              </button>
              <button className="action-button pause" onClick={disconnectKite} disabled={connectingKite}>
                Disconnect Kite
              </button>
              {!!connectNotice && <small className="summary-label">{connectNotice}</small>}
            </div>
          </div>
        </div>

        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">
              <span className="settings-icon warn" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="settings-svg">
                  <path d="M12 2 4 5v6c0 5.25 3.4 9.74 8 11 4.6-1.26 8-5.75 8-11V5l-8-3Zm0 5.5a1 1 0 1 1 0 2 1 1 0 0 1 0-2Zm-1 4h2v6h-2v-6Z" />
                </svg>
              </span>{" "}
              CAPITAL & RISK MANAGEMENT
            </span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span>Initial Capital (INR)</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.initialCapital}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, initialCapital: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Investment/Trade (INR)</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.maxInvestmentPerTrade}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, maxInvestmentPerTrade: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Profit/Day (INR)</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.maxProfitDay}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, maxProfitDay: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Loss/Day (INR)</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.maxLossDay}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, maxLossDay: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Trades/Day</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.maxTradesDay}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, maxTradesDay: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Parallel Trades</span>
              <input
                className="control-input"
                type="number"
                value={capitalRisk.maxParallelTrades}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, maxParallelTrades: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Charges per Trade (INR)</span>
              <input
                className="control-input"
                type="number"
                min={0}
                step={0.5}
                value={capitalRisk.chargesPerTrade ?? 20}
                onChange={(e) => setCapitalRisk((r) => ({ ...r, chargesPerTrade: Number(e.target.value) }))}
              />
            </label>
            <div className="settings-note field-span-2">
              Charges today = (open trades + closed trades) × brokerage per trade, plus STT (0.1% of buy+sell value), GST (18% on brokerage), exchange/SEBI/stamp. Zerodha F&O options brokerage ≈ ₹20/order. Update the rate above as needed.
            </div>
          </div>
        </div>
      </section>

      <section className="settings-grid-2">
        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">
              <span className="settings-icon ok" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="settings-svg">
                  <path d="M10.59 13.41 9.17 12l-1.41 1.41 2.83 2.83L16.24 10l-1.41-1.41-4.24 4.82ZM19.43 12.98A7.98 7.98 0 0 0 20 10a8 8 0 1 0-8 8 7.98 7.98 0 0 0 2.98-.57L19 21.46 20.46 20l-4.03-4.02Z" />
                </svg>
              </span>{" "}
              TRADING PARAMETERS
            </span>
            <button className="settings-mini-btn" onClick={resetDefaults}>
              Reset Defaults
            </button>
          </div>
          <div className="form-grid">
            <label className="field">
              <span>Lots</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.lots}
                onChange={(e) => setTradingParams((p) => ({ ...p, lots: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Lot Size</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.lotSize}
                onChange={(e) => setTradingParams((p) => ({ ...p, lotSize: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Strike Distance (±ATM)</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.maxStrikeDistanceFromAtm}
                onChange={(e) => setTradingParams((p) => ({ ...p, maxStrikeDistanceFromAtm: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Max Premium (INR)</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.maxPremium}
                onChange={(e) => setTradingParams((p) => ({ ...p, maxPremium: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Min Premium (INR)</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.minPremium}
                onChange={(e) => setTradingParams((p) => ({ ...p, minPremium: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Min Entry Strength %</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.minEntryStrengthPct}
                onChange={(e) => setTradingParams((p) => ({ ...p, minEntryStrengthPct: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>SL Type</span>
              <select
                className="control-select"
                value={tradingParams.slType}
                onChange={(e) =>
                  setTradingParams((p) => ({ ...p, slType: e.target.value as TradingParametersSetup["slType"] }))
                }
              >
                <option value="Fixed Points">Fixed Points</option>
                <option value="Percent">Percent</option>
              </select>
            </label>
            <label className="field">
              <span>SL Points</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.slPoints}
                onChange={(e) => setTradingParams((p) => ({ ...p, slPoints: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Breakeven Trigger %</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.breakevenTriggerPct}
                onChange={(e) => setTradingParams((p) => ({ ...p, breakevenTriggerPct: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Target Points</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.targetPoints}
                onChange={(e) => setTradingParams((p) => ({ ...p, targetPoints: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Trailing SL Points</span>
              <input
                className="control-input"
                type="number"
                value={tradingParams.trailingSlPoints}
                onChange={(e) => setTradingParams((p) => ({ ...p, trailingSlPoints: Number(e.target.value) }))}
              />
            </label>
          </div>
        </div>

        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">
              <span className="settings-icon info" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="settings-svg">
                  <path d="M3 3h18v4H3V3Zm0 7h10v4H3v-4Zm0 7h18v4H3v-4Zm12-7h6v4h-6v-4Z" />
                </svg>
              </span>{" "}
              STRATEGY SETTINGS
            </span>
          </div>
          <div className="form-grid">
            <label className="field field-span-2">
              <span>Strategy</span>
              <select
                className="control-select"
                value={`${strategy.strategyName}|${strategy.strategyVersion ?? "1.0.0"}`}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v) {
                    const [sid, ver] = v.split("|");
                    setStrategy((s) => ({ ...s, strategyName: sid, strategyVersion: ver }));
                    onStrategyChange(sid, ver);
                  }
                }}
                disabled={loadingStrategy || strategyOptions.length === 0}
              >
                {strategyOptions.length === 0 ? (
                  <>
                    <option value="">No active strategies — subscribe in Marketplace</option>
                    {strategy.strategyName && (
                      <option value={`${strategy.strategyName}|${strategy.strategyVersion ?? "1.0.0"}`}>
                        {strategy.strategyName} (current, not subscribed)
                      </option>
                    )}
                  </>
                ) : (
                  strategyOptions.map((o) => (
                    <option key={`${o.strategy_id}|${o.version}`} value={`${o.strategy_id}|${o.version}`}>
                      {o.display_name} ({o.version})
                    </option>
                  ))
                )}
              </select>
              {strategyOptions.length === 0 && (
                <small className="summary-label">
                  <Link href="/marketplace" className="link-inline">Go to Strategies</Link> to subscribe, then return here.
                </small>
              )}
            </label>
            <label className="field field-span-2">
              <span>Timeframe</span>
              <div className="settings-timeframe-row">
                {(["1-min", "3-min", "5-min", "15-min"] as const).map((tf) => (
                  <label key={tf} className="settings-radio-chip">
                    <input
                      type="radio"
                      name="timeframe"
                      checked={strategy.timeframe === tf}
                      onChange={() => setStrategy((s) => ({ ...s, timeframe: tf }))}
                    />
                    <span>{tf}</span>
                  </label>
                ))}
              </div>
            </label>
            <label className="field field-span-2">
              <span>Indices</span>
              <div className="settings-index-grid">
                {(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"] as const).map((idx) => {
                  const advancedLocked = idx !== "NIFTY" && (!master.brokerConnected || !master.platformApiOnline);
                  return (
                    <label key={idx} className={`settings-index-chip${advancedLocked ? " locked" : ""}`}>
                      <input
                        type="checkbox"
                        checked={strategy.indices[idx]}
                        disabled={advancedLocked}
                        onChange={(e) =>
                          setStrategy((s) => ({
                            ...s,
                            indices: {
                              ...s.indices,
                              [idx]: e.target.checked,
                            },
                          }))
                        }
                      />
                      <span>
                        {idx} {advancedLocked ? "[LOCKED]" : ""}
                      </span>
                    </label>
                  );
                })}
              </div>
              <small className="summary-label">
                BANKNIFTY/FINNIFTY/MIDCPNIFTY unlock when Kite + Platform API are connected.
              </small>
            </label>
            <label className="field">
              <span>Trade Start</span>
              <input
                className="control-input"
                value={strategy.tradeStart}
                onChange={(e) => setStrategy((s) => ({ ...s, tradeStart: e.target.value }))}
              />
            </label>
            <label className="field">
              <span>Trade End</span>
              <input
                className="control-input"
                value={strategy.tradeEnd}
                onChange={(e) => setStrategy((s) => ({ ...s, tradeEnd: e.target.value }))}
              />
            </label>
            <label className="field">
              <span>Auto Pause After Losses</span>
              <input
                className="control-input"
                type="number"
                min={1}
                max={10}
                value={strategy.autoPauseAfterLosses}
                onChange={(e) => setStrategy((s) => ({ ...s, autoPauseAfterLosses: Number(e.target.value) }))}
              />
            </label>
            <label className="field">
              <span>Trade Type</span>
              <select
                className="control-select"
                value={
                  approval
                    ? (approval.approved_paper && master.mode === "PAPER") || (approval.approved_live && master.mode === "LIVE")
                      ? master.mode
                      : approval.approved_paper
                        ? "PAPER"
                        : approval.approved_live
                          ? "LIVE"
                          : "PAPER"
                    : master.mode
                }
                onChange={(e) => setMaster((m) => ({ ...m, mode: e.target.value as TradeMode }))}
                disabled={approval && !approval.approved_paper && !approval.approved_live}
              >
                {(!approval || approval.approved_paper) && <option value="PAPER">PAPER</option>}
                {(!approval || approval.approved_live) && <option value="LIVE">LIVE</option>}
                {approval && !approval.approved_paper && !approval.approved_live && (
                  <option value="PAPER">No approval</option>
                )}
              </select>
              {approval && !approval.approved_paper && !approval.approved_live && (
                <span className="field-hint">Contact admin for Paper or Live approval.</span>
              )}
            </label>
          </div>

          <p className="strategy-details-hint">
            Strategy details (display name, description, indicators JSON) are edited in the Marketplace.
            Timeframe, Target, and SL are set in Trading Parameters above.
          </p>
        </div>
      </section>

      <section className="table-card">
        <div className="panel-title settings-panel-title">
          <span className="settings-title">
            <span className="settings-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" className="settings-svg">
                <path d="M12 2v3m0 14v3m7.78-2.22-2.12-2.12M6.34 6.34 4.22 4.22m15.56 0-2.12 2.12M6.34 17.66l-2.12 2.12M22 12h-3M5 12H2m10 5a5 5 0 1 1 0-10 5 5 0 0 1 0 10Z" />
              </svg>
            </span>{" "}
            ENGINE CONTROLS
          </span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>Go Live Switch</span>
            <select
              className="control-select"
              value={master.goLive ? "ON" : "OFF"}
              onChange={(e) => setMaster((m) => ({ ...m, goLive: e.target.value === "ON" }))}
            >
              <option value="OFF">OFF</option>
              <option value="ON">ON</option>
            </select>
          </label>

          <label className="field">
            <span>Kite Broker</span>
            <select
              className="control-select"
              value={master.brokerConnected ? "CONNECTED" : "DISCONNECTED"}
              onChange={(e) => setMaster((m) => ({ ...m, brokerConnected: e.target.value === "CONNECTED" }))}
            >
              <option value="DISCONNECTED">Disconnected</option>
              <option value="CONNECTED">Connected</option>
            </select>
          </label>

          <label className="field">
            <span>Shared API</span>
            <select
              className="control-select"
              value={master.sharedApiConnected ? "CONNECTED" : "DISCONNECTED"}
              onChange={(e) => setMaster((m) => ({ ...m, sharedApiConnected: e.target.value === "CONNECTED" }))}
            >
              <option value="CONNECTED">Connected</option>
              <option value="DISCONNECTED">Disconnected</option>
            </select>
          </label>
          <label className="field">
            <span>Platform API</span>
            <select
              className="control-select"
              value={master.platformApiOnline ? "ONLINE" : "OFFLINE"}
              onChange={(e) => setMaster((m) => ({ ...m, platformApiOnline: e.target.value === "ONLINE" }))}
            >
              <option value="ONLINE">Online</option>
              <option value="OFFLINE">Offline</option>
            </select>
          </label>
          <div className="field">
            <span>Save Config</span>
            <button className="action-button" onClick={saveAll}>
              Apply Complete Setup
            </button>
          </div>
          <div className="field">
            <span>Reset</span>
            <button className="action-button pause" onClick={resetDefaults}>
              Restore Defaults
            </button>
          </div>
        </div>
      </section>
    </AppFrame>
  );
}
