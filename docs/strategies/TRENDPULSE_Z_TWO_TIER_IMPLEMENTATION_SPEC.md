# TrendPulse Z — Two-tier (index + strike) implementation spec

**Status:** Locked from product Q&A (user decisions).  
**Strategy:** Same catalog entry — **TrendPulse Z** (`strategyType: trendpulse-z`); behaviour extended with Tier 2 gates, scoring, and auto-trade rules below.  
**Instrument:** NIFTY weekly options only.  
**Side:** Long CE / PE only.

---

## A. Scope & cadence

| ID | Decision | Value |
|----|-----------|--------|
| A1 | Strategy identity | **TrendPulse Z** (not a new `strategyType`; extend implementation + optional `trendPulseZ` JSON blocks for new thresholds) |
| A2 | Underlying / options | **NIFTY** weekly |
| A3 | Direction | **Long** CE or PE only |
| A4 | High-momentum filter (index) | **None** — PS/VS cross + HTF + ADX only (**Q-A4: A**). |
| A5 | Session | **Regular NSE F&O hours**; **block first 5 minutes** after 09:15 IST for new entries (**Q-B2: C**, 5m variant). Phase-3 session/breadth **off** per B10 unless later enabled. |
| A6 | Re-evaluation cadence | **Every chain refresh** (~30s), not only on new 5m candle close |

---

## B. Tier 1 — Index

| ID | Decision | Value |
|----|-----------|--------|
| B1 | Index series | **NIFTY 50 spot** via Kite historical (e.g. `5minute` ST, `15minute` HTF) — same as today |
| B2 | Opening window block | **No entries 09:15–09:20 IST** (first **5** minutes after open). Separate from bar warm-up: still load enough index history for z-window math (**Q-B2: C**). |
| B3 | Profile params | `zWindow`, `slopeLookback`, `adxMin`, `htfEmaFast/Slow` **fixed by profile** (conservative / balanced / aggressive) |
| B4 | Phase-3 session & breadth | **Off by default** (not enabled for this variant) |
| B5 | Bullish index but HTF neutral | **Hard no trade** (stricter than today if neutral currently allowed — **implement as explicit block**) |
| B6 | Cooldown after full round-trip | **None** — no same-direction cooldown after EXIT (**Q-B6: A**). |

**Signal engine:** Unchanged core (PS_z vs VS_z cross, HTF bias, ADX) + **B5** (hard no trade if HTF neutral vs direction) + **B2** opening block. **No** extra minimum spot-move filter (**A4**).

---

## C. Tier 2 — Strike universe (hard filters)

| ID | Decision | Value |
|----|-----------|--------|
| C1 | ATM step / OTM | **50** points; **`maxOtmSteps = 3`** (±3 strikes) |
| C2 | Selection shape | **Top K by ATM proximity**, then **rank** within K — **K = 3** nearest strikes to ATM that pass Tier-2 filters *(align with I52: evaluate depth on top 3 by ATM distance)* |
| C3 | Liquidity tiers | **Strict only** for recommendations & auto — **no relaxed tier** (no OI/vol waiver for display or auto) |
| C4 | Min OI / volume | Use catalog **`strikeSelection.minOi` / `minVolume`** (e.g. 10_000 / 500) — **strict** |
| C5 | IVR cap | **Single** cap CE and PE (e.g. **70** percentile — keep catalog `ivRankMaxPercentile`) |
| C6 | Delta | Target **\|delta\| = 0.45**, band **±0.05** (i.e. **0.40–0.50** for CE; PE mirrored negative) (**Q-C6: B**). |
| C7 | Premium / ITM filter | **Extrinsic share:** pass only if **(premium − intrinsic) / premium ≥ 0.25** (reject when time value is under 25% of premium — deep ITM). Require **positive premium** (skip divide-by-zero). *(Sheet Q-C7 was D; **v1 code = this rule only** — no premium/spot % cap unless added later.)* |
| C8 | DTE / expiry | Config **`minDteCalendarDays`** (default **3**): if calendar days to expiry **<** this threshold, roll chain/symbol to **next** listed weekly (**Q-C8: D**). Implements “don’t trade last minutes of life” in a configurable way. |

---

## D. Order book (depth)

| ID | Decision | Value |
|----|-----------|--------|
| D1 | Source | Kite **`quote`** for `NFO:` symbol — **`depth.buy` / `depth.sell`** |
| D2 | Levels | **Top 5** bid + **top 5** ask |
| D3 | Aggregation | **Gate on sum of quantities** top 5/side; **threshold 10_000** tuned after live log review; log **₹ depth** (sum price×qty) alongside for analysis (**Q-D3: D**). |
| D4 | Units | Treat quantities as **lots** for thresholds once verified against one live payload |
| D5 | Gates | **`AskDepth5 ≥ 10_000`** AND **`BidDepth5 ≥ 5 × AskDepth5`** |
| D6 | Spread gate | **None** |
| D7 | Snapshots | **Single** snapshot per evaluation |
| D8 | Stale book | Reject if quote/last-trade age **> 30 seconds** |
| D9 | Level-1 concentration | **Down-rank only** if level-1 dominates side depth (no hard skip) (**Q-D9: C**); exact penalty in composite TBD. |

| D10 | Depth missing | **Fail closed** (no trade, no auto) |

---

## E. Strike tape / technicals

| ID | Decision | Value |
|----|-----------|--------|
| E1 | Bar interval | **3m** for strike LTP series (aligned with chain cadence) |
| E2 | Warm-up | **N ≥ 5** LTP points before RSI/VWAP used |
| E3 | VWAP | **Long:** **LTP > VWAP**; prefer strike **closest to VWAP above** (not far above — implement as **distance above VWAP** cap or rank penalty) |
| E4 | RSI | **45–65** band for entry |
| E5 | Momentum k-of-m | **2 of last 3** closed 3m bars: LTP **up** (CE) / **down** (PE) (**Q-E5: B**). |
| E6 | Chain `signalEligible` | **`signalEligible == true` required** for auto |
| E7 | Index bullish + strike RSI high | **Down-rank** (not hard block) |

---

## F. Scoring & outputs

| ID | Decision | Value |
|----|-----------|--------|
| F1 | Model | **Weighted score:** index strength + strike + depth + technicals |
| F2 | Weights | **index : strike : depth : technicals = 25 : 20 : 30 : 25** (**Q-F2: C**). |
| F3 | Auto minimum | Four pillars scored **0–10** each; **auto only if sum = 40/40** (all pillars at 10) (**Q-F3: A**). Map **confidence** from composite (e.g. sum×2.5 → 0–100). |
| F4 | Rows per cycle | Up to **3** ranked recommendations |
| F5 | No Tier-2 pass | Persist **`WATCH`** (or equivalent) in **DB** so reporting/history see index-OK / no-strike state (**Q-WATCH: C**). |

---

## G. Auto-trade

| ID | Decision | Value |
|----|-----------|--------|
| G1 | Eligibility | **Composite + all hard gates pass** (not legacy TrendPulse “5/5” alone) |
| G2 | Confidence | **Mapped from composite** (0–100) |
| G3 | `autoTradeScoreThreshold` | **Repurpose for TrendPulse** to **composite threshold** (or boolean `tier2_eligible` + min composite) |
| G4 | Limits | **Current DB** `max_parallel_trades`, `max_trades_day` |
| G5 | Paper vs Live | **Same** gates |
| G6 | Pre-execution | **Re-quote** and **re-check depth** (and spread if added later) immediately before `execute_recommendation`; **skip + log** on failure |

---

## H. Risk & exits

| ID | Decision | Value |
|----|-----------|--------|
| H1 | Size | **Fixed lots** (user strategy settings) |
| H2 | SL / target | **Points** from user strategy settings (`sl_points`, `target_points`) |
| H3 | Time stop | **None** |
| H4 | Max loss per trade | **As per SL** |
| H5 | Pyramiding | **One shot only** — no add |

---

## I. Ops & data

| ID | Decision | Value |
|----|-----------|--------|
| I1 | Chain refresh | **30s** acceptable |
| I2 | Depth API budget | Depth fetch for **top 3** strikes by ATM distance after Tier-1 + coarse filters |
| I3 | Depth missing | **Fail closed** |
| I4 | Logging | **Persist per-decision JSON:** index fields, strike metrics, depth sums, pass/fail reasons, composite breakdown |

---

## J. Calibration

| ID | Decision | Value |
|----|-----------|--------|
| J1 | Historical depth | **Live-only** depth gate (no historical depth backtest) |
| J2 | Promotion | **Qualitative** review only — no mandatory numeric PF gate (**Q-J2: C**). |
| J3 | Paper soak | **None** required before Live auto (**Q-J3: D**). |

---

## K. UI / config

| ID | Decision | Value |
|----|-----------|--------|
| K1 | Trades card | Show **two-tier** breakdown: index pass/fail; strike depth, RSI, VWAP, sub-scores, composite |
| K2 | Tuning | Thresholds in **`trendPulseZ` + `strikeSelection` (and new blocks)** inside **strategy JSON** (catalog / user override pattern as today) |

---

## Resolved open decisions (stakeholder sheet 2026-03-24)

| Q-ID | Answer | Notes |
|------|--------|--------|
| Q-A4 | **A** | No extra index spot-move filter. |
| Q-B2 | **C (5m)** | Block **09:15–09:20 IST**; not 15m. |
| Q-B6 | **A** | No post-exit cooldown. |
| Q-C6 | **B** | Delta **±0.05** around 0.45. |
| Q-C7 | **D → impl A** | Sheet was **D** (A+C); **code implements A only:** **(premium − intrinsic) / premium ≥ 0.25**. |
| Q-C8 | **D** | **`minDteCalendarDays`** default **3**. |
| Q-D3 | **D** | Qty sum gate; tune **10_000** after logs; log ₹ depth. |
| Q-D9 | **C** | Concentration → **down-rank** only. |
| Q-E5 | **B** | **2 of 3** bars momentum. |
| Q-F2 | **C** | **25 : 20 : 30 : 25**. |
| Q-F3 | **A** | Auto only **40/40** on four × 0–10 pillars. |
| Q-J2 | **C** | Qualitative promotion only. |
| Q-J3 | **D** | No mandatory paper soak. |
| Q-WATCH | **C** | Persist **WATCH** in DB. |

**Remaining implementation TBD:** down-rank penalty formula for **D9**; schema for **`WATCH`** rows (table/columns vs reuse recommendations with status).

---

## Implementation phases (suggested)

1. **Phase 1 — Data:** Depth fetch helper; stale check (30s); fail-closed path; log qty + ₹ depth for D3 tuning.  
2. **Phase 2 — Tier 2 hard filters:** No relaxed liquidity; delta **0.40–0.50**; IVR; top-3 ATM; `signalEligible`; **`minDteCalendarDays`** expiry roll; **C7** extrinsic-share **≥ 0.25**.  
3. **Phase 3 — Scoring:** Weights **25/20/30/25**; pillars 0–10; **40/40** for auto; **D9** down-rank.  
4. **Phase 4 — Tier 1 tweaks:** **09:15–09:20** block; **B5** HTF neutral hard no; **no** spot-move gate.  
5. **Phase 5 — Auto:** Composite + gates; confidence from sum×2.5; pre-exec depth re-check.  
6. **Phase 6 — UI + WATCH:** Two-tier Trades card; **`WATCH`** persistence; strategy JSON schema.

---

## Document history

| Date | Change |
|------|--------|
| 2026-03-24 | Initial spec from stakeholder Q&A |
| 2026-03-24 | Open decisions merged (Q-A4 … Q-WATCH); A5/B2 opening block 5m; C6–C8, D3/D9, E5, F2/F3, F5, J2/J3 updated |
| 2026-03-24 | **C7:** implementation narrowed to extrinsic-share ≥ 0.25 only (drop premium/spot Z% for v1) |
