# TrendPulse Z — Implementation Plan

**Plan name:** TrendPulse Z  
**Status:** v1 implemented — `strategyType: trendpulse-z`, catalog `strat-trendpulse-z` / `1.0.0`, engine `backend/app/services/trendpulse_z.py` + `trades_service._get_live_candidates_trendpulse_z`  
**Scope (v1):** NIFTY option buy (long CE / long PE); signals on index; strike selection + auto-trade aligned with existing app architecture.

---

## 1. Concept

On a **signal timeframe (ST)**, trade **long options in the direction of higher-timeframe (HTF) bias** when **z-scored price momentum (PS_z)** crosses **z-scored volume momentum (VS_z)** in that direction, subject to trend quality, volatility context, and sanity filters.

**One-liner:** HTF sets allowed direction; ST PS_z vs VS_z cross times entries; strikes are chosen by eligibility (delta, liquidity, spreads), not by re-running full PS/VS on every option price series.

---

## 2. Definitions — PS and VS as z-scored slopes

### Price strength (PS)

1. Choose a **price derivative** on ST, e.g. log return over *k* bars, slope of log(close) over *k* bars, or (Close − MA) / ATR.
2. Each bar: **raw_PS**.
3. Rolling window **W** of raw_PS on ST.
4. **PS_z** = (raw_PS − mean(W)) / std(W).  
5. Optional: short EMA (3–5) on **PS_z** before cross logic.

### Volume strength (VS)

1. Choose a **volume-derived series** on ST, e.g. log(volume) slope over *k*, OBV slope over *k*, or volume / SMA(volume, L) − 1.
2. Each bar: **raw_VS**.
3. Same **W**: **VS_z** = z-score of raw_VS over W.
4. Optional: short EMA on **VS_z**.

### Cross rules (ST)

- **Bullish:** **PS_z crosses above** VS_z (define: close-of-bar or next-bar open — pick one, no mixing).
- **Bearish:** **PS_z crosses below** VS_z.

Use **underlying / future** OHLCV for these series (not per-option mid prices for v1).

---

## 3. Higher timeframe (HTF)

| Role | Balanced (v1) |
|------|----------------|
| HTF | **15m** |
| ST | **5m** |

**HTF bias (example):**

- **Bullish:** HTF close > EMA_slow (e.g. 34–50) **and** EMA_fast > EMA_slow (e.g. 13 > 34).
- **Bearish:** Opposite.
- **Neutral / chop:** No new entries (or document explicit override).

**Trade mapping:**

- HTF bullish → **long CE** only when ST gives bullish PS/VS cross + filters.
- HTF bearish → **long PE** only when ST gives bearish PS/VS cross + filters.

---

## 4. Balanced parameters (v1 default)

| Parameter | Balanced value | Notes |
|-----------|----------------|--------|
| HTF / ST | **15m / 5m** | Tune later: 30m/5m = slower bias |
| Z-score window **W** | **50** ST bars | Conservative 80, Aggressive 40 |
| Slope lookback **k** | **3–5** (fix **4** for v1) | |
| ADX minimum | **18** | On HTF or ST — choose one and document |
| IV rank max (long premium) | **~70th percentile** | Skip if IV unavailable until data exists |
| Choppiness | Optional block | Very high chop → no entry |

**Parameter tiers (future):**

| Tier | W | k | ADX min | IV rank max |
|------|---|---|---------|-------------|
| Conservative | 80 | 5–8 | 22 | 60th |
| **Balanced** | **50** | **3–5** | **18** | **70th** |
| Aggressive | 40 | 2–4 | 15 | 80th |

---

## 5. Additional filters (recommended)

- **ADX (or Choppiness):** Avoid pure chop; prefer ADX rising from a modest base when possible.
- **ATR %:** Regime awareness — compressed vs extended; affects lateness of entries and strike choice conceptually.
- **IV rank / percentile:** Cap long premium when IV is extreme unless separate catalyst rule exists.
- **Volume confirmation:** On signal bar, VS_z not collapsing against the trade direction; watch divergence vs prior swing.
- **Breadth / context (index):** Advance–decline, futures premium — optional.
- **Session / calendar:** First/last hour, expiry, major events — tag or restrict size.

---

## 6. Complete rule checklist

### Pre-trade

1. HTF bias clear (CE only vs PE only).
2. ADX ≥ minimum; optional choppiness not in blocked zone.
3. IV rule satisfied if IV data exists.
4. PS_z / VS_z inputs valid (no zero-volume garbage).

### Entry (ST)

1. Bullish: PS_z crosses **above** VS_z + HTF **bullish** → **CE** candidate.
2. Bearish: PS_z crosses **below** VS_z + HTF **bearish** → **PE** candidate.
3. Optional: confirmation bar closes in trade direction.

### Exit / invalidation

- HTF bias **flips** → exit or stop pyramiding (document).
- Opposite ST cross → optional scale-out.
- **Time stop:** max bars/minutes without follow-through (theta).
- **Profit:** R-multiple, % premium, or underlying × ATR move — one consistent system.

---

## 7. Architecture — align with current trading flow

### Signal layer (single series)

- Compute **PS_z, VS_z, HTF bias, ADX** (and optional IV/ATR filters) on **NIFTY spot or NIFTY future** (one liquid OHLCV stream per session).
- Do **not** compute full PS/VS on every strike’s option price in v1 (noise + illiquidity).

### Per-strike layer

- For each **candidate strike** (NIFTY only in v1): **eligibility** — delta band, min volume/OI, max spread, ATM ladder rules, lots, margin — **same pattern as existing** auto-trade pipeline.

### Auto-trade

- **Signal ∧ eligible strike ∧ risk gates** (cooldown, max positions, paper/live, user approval) → place order — **same architecture** as current strategies.

### Reporting / diagnostics

- Optional strike-level analytics for **which strike was chosen and fill quality**, not duplicate signal engine per strike.

---

## 8. Catalog & naming

| Field | Suggested value |
|-------|------------------|
| Strategy id | e.g. `strat-trendpulse-z` (follow project conventions) |
| Version | `1.0.0` |
| Display name | **TrendPulse Z (Balanced)** |
| Aliases | TrendPulse Z — NIFTY |

---

## 9. Time series graph (UI / API — implement when ready)

**Primary chart (ST, e.g. 5m):**

- Lines: **PS_z**, **VS_z**, horizontal zero reference.
- Markers: bullish cross (PS_z above VS_z), bearish cross (PS_z below VS_z).
- Optional: HTF bias ribbon or background strip.
- Optional subplot: **ADX** with threshold line (e.g. 18).

**Data contract (conceptual):** timestamps + `ps_z[]`, `vs_z[]`, optional `adx[]`, `htf_bias[]`, event markers — for strategy detail / backtest / live diagnostics page.

---

## 10. Implementation phases (suggested)

1. **v1:** HTF/ST + PS_z/VS_z (balanced params) + ADX gate + index-only signal + existing strike eligibility + auto-trade wiring.
2. **v2:** IV rank / ATR regime + chart API + UI graph.
3. **v3:** Conservative/Aggressive profiles; optional breadth/session hard filters — **implemented** in `app/services/trendpulse_phase3.py` (`profile` / `riskProfile`, `trendPulseZ.session`, `trendPulseZ.breadth`); wired in `get_strategy_score_params`, live TrendPulse candidates, and `GET /api/landing/trendpulse-series` (`phase3` + `tradeSignal` after gates). Chart `entryEvents` remain signal-only (see `phase3.chartNote` on API).

---

## 11. References

- Prior design discussion: PS/VS cross logic, z-scored slopes, option-buy considerations, HTF filter recommendations.
- Related project docs: `docs/implementation_structure.md`, `backend/docs/HEURISTIC_ENHANCEMENTS.md` (if extending heuristic JSON patterns).

---

*Last updated: saved as implementation plan; no code in this document.*
