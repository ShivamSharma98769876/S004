"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Box,
  Paper,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  SelectChangeEvent,
  Chip,
  CircularProgress,
  Alert,
} from "@mui/material";
import { keyframes } from "@mui/system";

// Display labels match NiftyAlgo (tradelele.in); value is API key
// Display labels match NiftyAlgo (tradelele.in); value is API key. Expiries fetched from Zerodha per instrument.
const INSTRUMENTS: { label: string; value: string }[] = [
  { label: "NIFTY 50", value: "NIFTY" },
  { label: "BANK NIFTY", value: "BANKNIFTY" },
  { label: "FIN NIFTY", value: "FINNIFTY" },
  { label: "SENSEX", value: "SENSEX" },
];

type BuildupType = "Long Buildup" | "Short Buildup" | "Short Covering" | "Long Unwinding" | "—";

type StrikeRow = {
  strike: number;
  call: {
    buildup: BuildupType;
    oiChgPct?: number;
    theta: number;
    delta: number;
    iv: number;
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
};

function formatVolOi(val: string): string {
  const n = parseFloat(val);
  if (Number.isNaN(n)) return val;
  if (n >= 100) return `${(n / 100).toFixed(1)}Cr`;
  if (n >= 1) return `${n.toFixed(1)}L`;
  return val;
}

function fmtPct(n: number): string {
  const s = n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`;
  return s;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type IndicesSpot = { spot: number; spotChgPct: number };
type IndicesData = { NIFTY: IndicesSpot; BANKNIFTY: IndicesSpot; SENSEX: IndicesSpot };

const STRIKE_RANGE_OPTIONS = [5, 10, 15, 20];

const pulseKeyframes = keyframes`
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.1); }
`;

export default function OptionAnalyticsPage() {
  const [instrument, setInstrument] = useState("NIFTY");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiriesLoading, setExpiriesLoading] = useState(true);
  const [expiry, setExpiry] = useState("");
  const [strikesUp, setStrikesUp] = useState(10);
  const [strikesDown, setStrikesDown] = useState(10);
  const [data, setData] = useState<OptionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimitMessage, setRateLimitMessage] = useState<string | null>(null);
  const [indicesData, setIndicesData] = useState<IndicesData | null>(null);

  const fetchChain = useCallback(async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        instrument,
        expiry,
        strikes_up: String(strikesUp),
        strikes_down: String(strikesDown),
      });
      const res = await fetch(`${API_BASE}/api/analytics/option-chain?${params}`);
      const json = await res.json();
      if (!res.ok) {
        if (res.status === 429) {
          setRateLimitMessage(json?.detail || "Kite rate limit. Showing last data.");
          // Keep existing data so table stays visible
          if (showSpinner) setLoading(false);
          return;
        }
        setError(json?.detail || res.statusText || "Failed to load option chain");
        // Keep existing data on failure so table stays visible; next successful fetch will refresh
        if (showSpinner) setLoading(false);
        return;
      }
      setData(json);
      setError(null);
      if (json.from_cache) setRateLimitMessage("Showing cached data (rate limited). Next refresh will retry.");
      else setRateLimitMessage(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      // Keep existing data on fetch failure; next successful fetch will refresh with new timestamp
    } finally {
      if (showSpinner) setLoading(false);
    }
  }, [instrument, expiry, strikesUp, strikesDown]);

  const [refreshSeconds, setRefreshSeconds] = useState(15);

  // Fetch expiries from Zerodha (NFO cache) when instrument changes
  useEffect(() => {
    let cancelled = false;
    setExpiries([]);
    setExpiriesLoading(true);
    fetch(`${API_BASE}/api/analytics/expiries?instrument=${encodeURIComponent(instrument)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((json: { instrument?: string; expiries?: string[] } | null) => {
        if (cancelled) return;
        const list = Array.isArray(json?.expiries) ? json.expiries : [];
        setExpiries(list);
        setExpiry((prev) => (list.length && list.includes(prev) ? prev : list[0] ?? ""));
      })
      .catch(() => { if (!cancelled) setExpiries([]); setExpiry(""); })
      .finally(() => { if (!cancelled) setExpiriesLoading(false); });
    return () => { cancelled = true; };
  }, [instrument]);

  useEffect(() => {
    if (expiry) fetchChain(true);
  }, [fetchChain]);

  // Load refresh interval from backend config
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/analytics/config`)
      .then((r) => r.ok ? r.json() : null)
      .then((json: { option_chain_refresh_seconds?: number; expiry_config?: unknown } | null) => {
        if (cancelled || !json) return;
        const sec = json.option_chain_refresh_seconds;
        if (typeof sec === "number" && sec >= 5 && sec <= 300) setRefreshSeconds(sec);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Fetch NIFTY / BANK NIFTY / SENSEX spot for NSE MARKET strip (all three at once)
  const fetchIndices = useCallback(() => {
    fetch(`${API_BASE}/api/analytics/indices`)
      .then((r) => (r.ok ? r.json() : null))
      .then((json: IndicesData | null) => {
        if (json && typeof json === "object") setIndicesData(json);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    fetchIndices();
    const t = setInterval(fetchIndices, 30000); // refresh indices every 30s
    return () => clearInterval(t);
  }, [fetchIndices]);

  // Auto-refresh at configured interval (silent, no spinner) only when expiry is selected
  useEffect(() => {
    if (!expiry) return;
    const ms = refreshSeconds * 1000;
    const t = setInterval(() => fetchChain(false), ms);
    return () => clearInterval(t);
  }, [fetchChain, refreshSeconds, expiry]);

  const spot = data?.spot ?? 0;
  const atmStrike = spot ? Math.round(spot / 50) * 50 : 0;
  const chain = data?.chain ?? [];
  const vix = data?.vix ?? null;
  const synFuture = data?.synFuture ?? null;
  const pcr = data?.pcr ?? 0;
  const pcrVol = data?.pcrVol ?? 0;
  const updated = data?.updated ? new Date(data.updated).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

  const handleInstrument = (e: SelectChangeEvent) => setInstrument(e.target.value);
  const handleExpiry = (e: SelectChangeEvent) => setExpiry(e.target.value);

  const showNa = (n: number) => (n === 0 ? "—" : n.toFixed(2));
  const showNa3 = (n: number) => (n === 0 ? "—" : n.toFixed(3));

  const isLongBuildup = (b: string) => b === "Long Buildup";
  const isShortBuildup = (b: string) => b === "Short Buildup";
  const isShortCovering = (b: string) => b === "Short Covering";
  const isLongUnwinding = (b: string) => b === "Long Unwinding";
  const buildupSx = (buildup: string) => {
    if (isLongBuildup(buildup)) return { bgcolor: "rgba(76, 175, 80, 0.35)", color: "#c8e6c9" };
    if (isShortBuildup(buildup)) return { bgcolor: "rgba(244, 67, 54, 0.35)", color: "#ffcdd2" };
    if (isShortCovering(buildup)) return { bgcolor: "rgba(33, 150, 243, 0.35)", color: "#bbdefb" };
    if (isLongUnwinding(buildup)) return { bgcolor: "rgba(255, 152, 0, 0.35)", color: "#ffe0b2" };
    return { color: "text.secondary" };
  };

  const formControlSx = {
    minWidth: 140,
    "& .MuiOutlinedInput-root": { bgcolor: "#161b22", borderColor: "#30363d" },
    "& .MuiInputLabel-root": { color: "#8b949e" },
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        bgcolor: "#0b0f16",
        color: "#e6edf3",
        minHeight: "100vh",
        p: 0,
      }}
    >
      {/* NSE MARKET strip - match NiftyAlgo top indices */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 3,
          px: 2,
          py: 1.5,
          bgcolor: "#121826",
          borderBottom: "1px solid #30363d",
        }}
      >
        <Typography variant="caption" sx={{ color: "#8b949e", fontWeight: 600, textTransform: "uppercase" }}>
          NSE MARKET
        </Typography>
        <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>NIFTY 50</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: instrument === "NIFTY" ? "#7ee787" : "#e6edf3" }}>
              {indicesData?.NIFTY?.spot
                ? `${indicesData.NIFTY.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${(indicesData.NIFTY.spotChgPct ?? 0) !== 0 ? fmtPct(indicesData.NIFTY.spotChgPct) : ""}`
                : "—"}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>BANK NIFTY</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: instrument === "BANKNIFTY" ? "#7ee787" : "#e6edf3" }}>
              {indicesData?.BANKNIFTY?.spot
                ? `${indicesData.BANKNIFTY.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${(indicesData.BANKNIFTY.spotChgPct ?? 0) !== 0 ? fmtPct(indicesData.BANKNIFTY.spotChgPct) : ""}`
                : "—"}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>SENSEX</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: instrument === "SENSEX" ? "#7ee787" : "#e6edf3" }}>
              {indicesData?.SENSEX?.spot
                ? `${indicesData.SENSEX.spot.toLocaleString("en-IN", { minimumFractionDigits: 2 })} ${(indicesData.SENSEX.spotChgPct ?? 0) !== 0 ? fmtPct(indicesData.SENSEX.spotChgPct) : ""}`
                : "—"}
            </Typography>
          </Box>
        </Box>
      </Box>

      <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
        {/* Page title */}
        <Typography variant="h5" sx={{ color: "#e6edf3", fontWeight: 700 }}>
          Option Chain
        </Typography>

        {rateLimitMessage && (
          <Alert severity="warning" onClose={() => setRateLimitMessage(null)} sx={{ bgcolor: "rgba(255, 152, 0, 0.12)", color: "#e6edf3" }}>
            {rateLimitMessage}
          </Alert>
        )}

        {/* Toolbar: Instrument + Expiry + Live (NiftyAlgo-style single row) */}
        <Box sx={{ display: "flex", gap: 2, alignItems: "center", flexWrap: "wrap" }}>
          <FormControl size="small" sx={formControlSx}>
            <InputLabel id="option-chain-instrument-label">Instrument</InputLabel>
            <Select
              labelId="option-chain-instrument-label"
              id="option-chain-instrument"
              label="Instrument"
              value={instrument}
              onChange={handleInstrument}
              sx={{ color: "#e6edf3" }}
            >
              {INSTRUMENTS.map((i) => (
                <MenuItem key={i.value} value={i.value}>{i.label}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={formControlSx}>
            <InputLabel id="option-chain-expiry-label">Expiry</InputLabel>
            <Select
              labelId="option-chain-expiry-label"
              id="option-chain-expiry"
              label="Expiry"
              value={expiry}
              onChange={handleExpiry}
              sx={{ color: "#e6edf3" }}
              displayEmpty
              renderValue={(v) => v || (expiriesLoading ? "Loading…" : expiries.length === 0 ? "No expiries (connect Zerodha)" : "Select expiry")}
            >
              {expiriesLoading && (
                <MenuItem value="" disabled>Loading expiries from Zerodha…</MenuItem>
              )}
              {!expiriesLoading && expiries.length === 0 && (
                <MenuItem value="" disabled>Run bootstrap NFO cache or connect Zerodha</MenuItem>
              )}
              {expiries.map((e) => (
                <MenuItem key={e} value={e}>{e}</MenuItem>
              ))}
            </Select>
          </FormControl>
          {!loading && chain.length > 0 && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: "#4caf50",
                  animation: `${pulseKeyframes} 2s ease-in-out infinite`,
                }}
              />
              <Typography variant="caption" sx={{ color: "#7ee787" }}>Live</Typography>
            </Box>
          )}
          {loading && <CircularProgress size={22} sx={{ color: "#4caf50" }} />}
          <Box sx={{ display: "flex", gap: 1, alignItems: "center", ml: 1 }}>
            <Typography variant="caption" sx={{ color: "#8b949e" }}>Strikes:</Typography>
            <Box
              component="select"
              aria-label="Strikes down"
              value={strikesDown}
              onChange={(e) => setStrikesDown(Number(e.target.value))}
              sx={{
                color: "#e6edf3",
                minWidth: 56,
                height: 32,
                py: 0.5,
                px: 1,
                bgcolor: "#161b22",
                border: "1px solid #30363d",
                borderRadius: 1,
                fontSize: "0.875rem",
                "&:focus": { outline: "1px solid #58a6ff" },
              }}
            >
              {STRIKE_RANGE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n} down</option>
              ))}
            </Box>
            <Box
              component="select"
              aria-label="Strikes up"
              value={strikesUp}
              onChange={(e) => setStrikesUp(Number(e.target.value))}
              sx={{
                color: "#e6edf3",
                minWidth: 56,
                height: 32,
                py: 0.5,
                px: 1,
                bgcolor: "#161b22",
                border: "1px solid #30363d",
                borderRadius: 1,
                fontSize: "0.875rem",
                "&:focus": { outline: "1px solid #58a6ff" },
              }}
            >
              {STRIKE_RANGE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n} up</option>
              ))}
            </Box>
          </Box>
          {error && (
            <Alert severity="warning" onClose={() => setError(null)} sx={{ flex: 1, maxWidth: 420 }}>
              {error}
            </Alert>
          )}
        </Box>

        {/* Market stats bar - single row like NiftyAlgo */}
        <Paper
          variant="outlined"
          sx={{
            p: 1.5,
            display: "flex",
            gap: 4,
            flexWrap: "wrap",
            alignItems: "center",
            bgcolor: "#121826",
            borderColor: "#30363d",
            color: "#e6edf3",
          }}
        >
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>SPOT</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: (data?.spotChgPct ?? 0) >= 0 ? "#7ee787" : "#ff7b72" }}>
              {spot ? spot.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—"} {(data?.spotChgPct ?? 0) !== 0 && `(${fmtPct(data?.spotChgPct ?? 0)})`}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>VIX</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: vix != null && vix < 0 ? "#ff7b72" : "#e6edf3" }}>{vix != null ? vix : "—"}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>SYN FUTURE</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: "#e6edf3" }}>{synFuture != null ? synFuture.toFixed(2) : "—"}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: "#8b949e", display: "block" }}>PCR</Typography>
            <Typography variant="body2" fontWeight="bold" sx={{ color: "#e6edf3" }}>{pcr.toFixed(2)}{pcrVol ? ` (Vol: ${pcrVol.toFixed(2)})` : ""}</Typography>
          </Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Typography variant="caption" sx={{ color: "#8b949e" }}>UPDATED</Typography>
            <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: "#4caf50", animation: `${pulseKeyframes} 2s ease-in-out infinite` }} />
            <Typography variant="body2" sx={{ color: "#e6edf3" }}>{updated || "—"}</Typography>
          </Box>
        </Paper>

        {/* Legend - NiftyAlgo style */}
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
          <Typography variant="caption" sx={{ color: "#8b949e", fontWeight: 600 }}>Legend:</Typography>
          <Chip size="small" label="ITM" sx={{ fontSize: "0.7rem", bgcolor: "rgba(33, 150, 243, 0.2)", color: "#79b8ff" }} />
          <Chip size="small" label="ATM" sx={{ fontSize: "0.7rem", bgcolor: "#0969da", color: "#fff" }} />
          <Chip size="small" label="Long Buildup" sx={{ fontSize: "0.7rem", bgcolor: "rgba(76, 175, 80, 0.35)", color: "#7ee787" }} />
          <Chip size="small" label="Short Buildup" sx={{ fontSize: "0.7rem", bgcolor: "rgba(244, 67, 54, 0.35)", color: "#ff7b72" }} />
          <Chip size="small" label="Short Covering" sx={{ fontSize: "0.7rem", bgcolor: "rgba(33, 150, 243, 0.35)", color: "#79b8ff" }} />
          <Chip size="small" label="Long Unwinding" sx={{ fontSize: "0.7rem", bgcolor: "rgba(255, 152, 0, 0.35)", color: "#ffa657" }} />
          <Typography variant="caption" sx={{ color: "#8b949e" }}>H = Day High, L = Day Low</Typography>
        </Box>

        {/* Option chain table — dark theme, vibrant colors */}
        <Paper
          variant="outlined"
          sx={{
            overflow: "auto",
            bgcolor: "#161b22",
            borderColor: "#30363d",
          }}
        >
          <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              <TableCell align="center" colSpan={9} sx={{ bgcolor: "#21262d", color: "#e6edf3", fontWeight: "bold", borderColor: "#30363d" }}>CALLS</TableCell>
              <TableCell align="center" sx={{ bgcolor: "#21262d", color: "#e6edf3", fontWeight: "bold", minWidth: 80, borderColor: "#30363d" }}>STRIKE</TableCell>
              <TableCell align="center" colSpan={10} sx={{ bgcolor: "#21262d", color: "#e6edf3", fontWeight: "bold", borderColor: "#30363d" }}>PUTS</TableCell>
            </TableRow>
            <TableRow>
              {["BUILDUP", "THETA", "DELTA", "IV%", "VOLUME", "OI", "OI CHG%", "LTP CHG%", "LTP"].map((h) => (
                <TableCell key={h} sx={{ bgcolor: "#21262d", color: "#8b949e", borderColor: "#30363d" }}>{h}</TableCell>
              ))}
              <TableCell sx={{ bgcolor: "#21262d", borderColor: "#30363d" }} />
              <TableCell sx={{ bgcolor: "#21262d", color: "#8b949e", borderColor: "#30363d" }}>PCR</TableCell>
              {["LTP", "LTP CHG%", "OI", "OI CHG%", "VOLUME", "IV%", "DELTA", "THETA", "BUILDUP"].map((h) => (
                <TableCell key={h} sx={{ bgcolor: "#21262d", color: "#8b949e", borderColor: "#30363d" }}>{h}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {chain.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={20} align="center" sx={{ py: 3, color: "#8b949e", borderColor: "#30363d" }}>
                  {error ? "Connect Zerodha broker for live option chain." : "No option chain data for this expiry."}
                </TableCell>
              </TableRow>
            )}
            {chain.map((row) => {
              const isAtm = row.strike === atmStrike;
              const cellSx = isAtm ? { bgcolor: "rgba(9, 105, 218, 0.2)", fontWeight: 600, color: "#79b8ff" } : { color: "#e6edf3" };
              const posColor = "#7ee787";
              const negColor = "#ff7b72";
              return (
                <TableRow key={row.strike} hover sx={{ "&:hover": { bgcolor: "rgba(255,255,255,0.04)" } }}>
                  <TableCell sx={{ ...cellSx, ...buildupSx(row.call.buildup), fontSize: "0.75rem", borderColor: "#30363d" }}>{row.call.buildup}</TableCell>
                  <TableCell sx={{ ...cellSx, color: row.call.theta < 0 ? negColor : posColor, borderColor: "#30363d" }}>{showNa(row.call.theta)}</TableCell>
                  <TableCell sx={{ ...cellSx, color: row.call.delta >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{showNa3(row.call.delta)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{row.call.iv ? `${row.call.iv.toFixed(2)}%` : "—"}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{formatVolOi(row.call.volume)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{formatVolOi(row.call.oi)}</TableCell>
                  <TableCell sx={{ ...cellSx, color: (row.call.oiChgPct ?? 0) >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{row.call.oiChgPct != null ? fmtPct(row.call.oiChgPct) : "—"}</TableCell>
                  <TableCell sx={{ ...cellSx, color: (row.call.ltpChg ?? 0) >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{fmtPct(row.call.ltpChg ?? 0)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{row.call.ltp.toFixed(2)}</TableCell>
                  <TableCell sx={{ ...cellSx, bgcolor: isAtm ? "rgba(9, 105, 218, 0.3)" : undefined, fontWeight: isAtm ? 700 : 500, borderColor: "#30363d" }}>{row.strike}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{row.put.pcr.toFixed(2)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{row.put.ltp.toFixed(2)}</TableCell>
                  <TableCell sx={{ ...cellSx, color: (row.put.ltpChg ?? 0) >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{fmtPct(row.put.ltpChg ?? 0)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{formatVolOi(row.put.oi)}</TableCell>
                  <TableCell sx={{ ...cellSx, color: (row.put.oiChgPct ?? 0) >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{row.put.oiChgPct != null ? fmtPct(row.put.oiChgPct) : "—"}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{formatVolOi(row.put.volume)}</TableCell>
                  <TableCell sx={{ ...cellSx, borderColor: "#30363d" }}>{row.put.iv ? `${row.put.iv.toFixed(2)}%` : "—"}</TableCell>
                  <TableCell sx={{ ...cellSx, color: row.put.delta >= 0 ? posColor : negColor, borderColor: "#30363d" }}>{showNa3(row.put.delta)}</TableCell>
                  <TableCell sx={{ ...cellSx, color: row.put.theta < 0 ? negColor : posColor, borderColor: "#30363d" }}>{showNa(row.put.theta)}</TableCell>
                  <TableCell sx={{ ...cellSx, ...buildupSx(row.put.buildup), fontSize: "0.75rem", borderColor: "#30363d" }}>{row.put.buildup}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
          </Table>
        </Paper>

        {/* Footer - NiftyAlgo style */}
        <Typography variant="caption" sx={{ color: "#8b949e", display: "block", mt: 1 }}>
          Market hours: Mon–Fri 09:15 – 15:30 IST | Pre-open: 09:00–09:15
        </Typography>
      </Box>
    </Box>
  );
}
