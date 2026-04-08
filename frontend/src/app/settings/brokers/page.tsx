"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";

type BrokerCard = {
  code: string;
  label: string;
  connected: boolean;
  chainSupported?: boolean;
  liveOrdersSupported?: boolean;
  note?: string;
};

type BrokersHub = {
  activeBroker: string | null;
  encryptionReady: boolean;
  brokerConnectedFlag: boolean;
  paperSharedAvailable: boolean;
  platformShared: { configured: boolean; brokerCode: string | null; updatedAt?: string | null };
  brokers: BrokerCard[];
};

export default function BrokersHubPage() {
  const [hub, setHub] = useState<BrokersHub | null>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<string>("");

  const [zApiKey, setZApiKey] = useState("");
  const [zApiSecret, setZApiSecret] = useState("");
  const [zRequestToken, setZRequestToken] = useState("");
  const [zAccessToken, setZAccessToken] = useState("");

  const [fClientId, setFClientId] = useState("");
  const [fSecret, setFSecret] = useState("");
  const [fRedirect, setFRedirect] = useState("https://trade.fyers.in/api-login/redirect-uri/index.html");
  const [fAuthCode, setFAuthCode] = useState("");

  const [adminKey, setAdminKey] = useState("");
  const [adminTok, setAdminTok] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const data = await apiJson<BrokersHub>("/api/settings/brokers", "GET");
      setHub(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load brokers.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const setActive = async (code: string) => {
    setBusy("active");
    setError("");
    try {
      await apiJson("/api/settings/brokers/active", "PUT", { brokerCode: code });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to set active broker.");
    } finally {
      setBusy("");
    }
  };

  const connectZerodha = async () => {
    setBusy("z");
    setError("");
    try {
      await apiJson("/api/settings/zerodha/connect", "POST", {
        apiKey: zApiKey,
        apiSecret: zApiSecret,
        requestToken: zRequestToken,
        accessToken: zAccessToken,
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Zerodha connect failed.");
    } finally {
      setBusy("");
    }
  };

  const disconnectZerodha = async () => {
    setBusy("z");
    setError("");
    try {
      await apiJson("/api/settings/zerodha/disconnect", "POST");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Zerodha disconnect failed.");
    } finally {
      setBusy("");
    }
  };

  const openFyersAuth = async () => {
    setBusy("f-url");
    setError("");
    try {
      const r = await apiJson<{ authUrl: string }>("/api/settings/brokers/fyers/auth-url", "POST", {
        clientId: fClientId,
        secretKey: fSecret,
        redirectUri: fRedirect,
      });
      window.open(r.authUrl, "_blank", "noopener,noreferrer");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not get FYERS URL.");
    } finally {
      setBusy("");
    }
  };

  const connectFyers = async () => {
    setBusy("f");
    setError("");
    try {
      await apiJson("/api/settings/brokers/fyers/connect", "POST", {
        clientId: fClientId,
        secretKey: fSecret,
        redirectUri: fRedirect,
        authCode: fAuthCode,
      });
      setFAuthCode("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "FYERS connect failed.");
    } finally {
      setBusy("");
    }
  };

  const disconnectFyers = async () => {
    setBusy("f");
    setError("");
    try {
      await apiJson("/api/settings/brokers/fyers/disconnect", "POST");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "FYERS disconnect failed.");
    } finally {
      setBusy("");
    }
  };

  const savePlatform = async () => {
    setBusy("adm");
    setError("");
    try {
      await apiJson("/api/admin/platform-broker", "PUT", {
        brokerCode: "zerodha",
        zerodhaApiKey: adminKey,
        zerodhaAccessToken: adminTok,
      });
      setAdminTok("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save platform broker.");
    } finally {
      setBusy("");
    }
  };

  const clearPlatform = async () => {
    setBusy("adm");
    setError("");
    try {
      await apiJson("/api/admin/platform-broker", "DELETE");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clear platform broker.");
    } finally {
      setBusy("");
    }
  };

  const admin = isAdmin();

  return (
    <AppFrame
      title="Brokers"
      subtitle="One active broker per account. Zerodha powers option chains and LIVE orders today; FYERS session is for roadmap / profile verification."
    >
      <div style={{ marginBottom: 16 }}>
        <Link href="/settings" className="summary-label">
          ← Back to Trading Settings
        </Link>
      </div>
      {error ? <div className="notice warning">{error}</div> : null}

      {hub ? (
        <section className="summary-grid" style={{ marginBottom: 24 }}>
          <div className="summary-card">
            <div className="summary-label">Active broker</div>
            <div className="summary-value">{hub.activeBroker ?? "—"}</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">Vault encryption</div>
            <div className="summary-value">{hub.encryptionReady ? "Ready" : "Not configured"}</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">Paper shared quotes</div>
            <div className="summary-value">{hub.paperSharedAvailable ? "Available" : "Not set"}</div>
          </div>
        </section>
      ) : null}

      {!hub?.encryptionReady ? (
        <div className="notice warning" style={{ marginBottom: 20 }}>
          Set <code>S004_CREDENTIALS_FERNET_KEY</code> on the server (Fernet key from{" "}
          <code>python -c &quot;from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())&quot;</code>
          ) to encrypt FYERS credentials and the admin shared connection.
        </div>
      ) : null}

      <div className="settings-grid-2">
        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">Zerodha Kite</span>
          </div>
          <div className="form-grid">
            <p className="summary-label field-span-2" style={{ lineHeight: 1.5 }}>
              Used for NIFTY option chains, quotes, PAPER/LIVE monitoring, and LIVE orders. Daily access token required.
            </p>
            {hub?.brokers.find((b) => b.code === "zerodha")?.connected ? (
              <div className="field field-span-2">
                <span className="chip chip-status-active">Connected</span>
                <button type="button" className="action-button pause" disabled={!!busy} onClick={() => void disconnectZerodha()}>
                  Disconnect
                </button>
              </div>
            ) : (
              <span className="chip chip-status-paused field-span-2">Disconnected</span>
            )}
            <label className="field">
              <span>API Key</span>
              <input className="control-input" value={zApiKey} onChange={(e) => setZApiKey(e.target.value)} autoComplete="off" />
            </label>
            <label className="field">
              <span>API Secret</span>
              <input
                className="control-input"
                type="password"
                value={zApiSecret}
                onChange={(e) => setZApiSecret(e.target.value)}
                autoComplete="off"
              />
            </label>
            <label className="field field-span-2">
              <span>Request token</span>
              <input className="control-input" value={zRequestToken} onChange={(e) => setZRequestToken(e.target.value)} />
            </label>
            <label className="field field-span-2">
              <span>Or access token</span>
              <input className="control-input" value={zAccessToken} onChange={(e) => setZAccessToken(e.target.value)} />
            </label>
            <div className="field field-span-2">
              <button type="button" className="action-button" disabled={!!busy} onClick={() => void connectZerodha()}>
                {busy === "z" ? "…" : "Connect Zerodha"}
              </button>
            </div>
            <div className="field field-span-2">
              <button
                type="button"
                className="action-button"
                disabled={!!busy || !hub?.brokers.find((b) => b.code === "zerodha")?.connected}
                onClick={() => void setActive("zerodha")}
              >
                Set active: Zerodha
              </button>
            </div>
          </div>
        </div>

        <div className="table-card">
          <div className="panel-title settings-panel-title">
            <span className="settings-title">FYERS</span>
          </div>
          <div className="form-grid">
            <p className="summary-label field-span-2" style={{ lineHeight: 1.5 }}>
              {hub?.brokers.find((b) => b.code === "fyers")?.note ?? ""} Server stores app keys encrypted when Fernet is configured.
            </p>
            {hub?.brokers.find((b) => b.code === "fyers")?.connected ? (
              <div className="field field-span-2">
                <span className="chip chip-status-active">Session connected</span>
                <button type="button" className="action-button pause" disabled={!!busy} onClick={() => void disconnectFyers()}>
                  Disconnect FYERS
                </button>
              </div>
            ) : (
              <span className="chip chip-status-paused field-span-2">Not connected</span>
            )}
            <label className="field field-span-2">
              <span>App ID (e.g. XXXXX-100)</span>
              <input className="control-input" value={fClientId} onChange={(e) => setFClientId(e.target.value)} />
            </label>
            <label className="field field-span-2">
              <span>Secret</span>
              <input className="control-input" type="password" value={fSecret} onChange={(e) => setFSecret(e.target.value)} />
            </label>
            <label className="field field-span-2">
              <span>Redirect URI (must match FYERS app)</span>
              <input className="control-input" value={fRedirect} onChange={(e) => setFRedirect(e.target.value)} />
            </label>
            <div className="field field-span-2">
              <button type="button" className="action-button" disabled={!!busy} onClick={() => void openFyersAuth()}>
                {busy === "f-url" ? "…" : "Open FYERS login"}
              </button>
            </div>
            <label className="field field-span-2">
              <span>Auth code from redirect URL</span>
              <input className="control-input" value={fAuthCode} onChange={(e) => setFAuthCode(e.target.value)} placeholder="auth_code=…" />
            </label>
            <div className="field field-span-2">
              <button type="button" className="action-button" disabled={!!busy || !hub?.encryptionReady} onClick={() => void connectFyers()}>
                {busy === "f" ? "…" : "Exchange code & connect"}
              </button>
            </div>
            <div className="field field-span-2">
              <button
                type="button"
                className="action-button"
                disabled={!!busy || !hub?.brokers.find((b) => b.code === "fyers")?.connected}
                onClick={() => void setActive("fyers")}
              >
                Set active: FYERS
              </button>
            </div>
          </div>
        </div>
      </div>

      {admin ? (
        <div className="table-card" style={{ marginTop: 24 }}>
          <div className="panel-title settings-panel-title">
            <span className="settings-title">Admin · shared Zerodha (paper quotes)</span>
          </div>
          <div className="form-grid">
            <p className="summary-label field-span-2" style={{ lineHeight: 1.5 }}>
              Single platform slot: users without their own Kite session can still get chains/quotes in PAPER when this is set.
              Requires Fernet. Audit entries are written on save/clear.
            </p>
            <div className="field field-span-2">
              <span className="summary-label">Status: {hub?.platformShared.configured ? "configured" : "empty"}</span>
            </div>
            <label className="field">
              <span>API Key</span>
              <input className="control-input" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} />
            </label>
            <label className="field">
              <span>Access token</span>
              <input className="control-input" type="password" value={adminTok} onChange={(e) => setAdminTok(e.target.value)} />
            </label>
            <div className="field field-span-2">
              <button type="button" className="action-button" disabled={!!busy || !hub?.encryptionReady} onClick={() => void savePlatform()}>
                {busy === "adm" ? "…" : "Save shared connection"}
              </button>
              <button type="button" className="action-button pause" disabled={!!busy} onClick={() => void clearPlatform()}>
                Clear shared
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppFrame>
  );
}
