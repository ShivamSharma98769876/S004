# PS/VS + 15m permission & filters, 3m timing & pullback — master specification

This document consolidates the full strategy definition (data stack, indicators, gates, pullback rules, conviction, and operational behavior). It is intended as the single source of truth for implementation and backtesting alignment.

**Default index:** BANKNIFTY (configurable).  
**Divergence:** out of scope for v1.

---

## 1. Purpose

Trade options directionally when:

- **15m (resampled from 3m)** provides **directional permission** and **quality filters** (ATR, ADX, volume, RSI band), evaluated on the **last completed 15m bar** only.
- **3m** provides **timing** (PS vs VS cross) and **pullback discipline**, evaluated on **completed 3m bars** (not the forming bar).

**“In sync”** does **not** mean both timeframes cross at the same clock instant. It means: **15m permission + filters** agree with the **intended direction** at decision time, while **3m** supplies **when** to act and **pullback entry** logic.

---

## 2. Data & timeframes

| Layer | Source |
|--------|--------|
| **Raw** | **3-minute** OHLCV (broker API). Fetch **N calendar days**; **merge prior session** in the same request so early session has enough history for warm-up and **first 15m** readiness. |
| **15m** | **Resampled in memory from 3m** only. Do **not** mix broker-native 15m with resampled 15m in the same pipeline. |
| **Storage** | Prefer **in-memory** series for the signal path (aligns with S004-style on-demand history). |

**Session (IST):** trading window configurable (e.g. **09:15–15:15**). **Collection** starts at **09:15**; prior days merged for indicators.

**Square-off:** per platform/settings (e.g. before cash close).

**Bar evaluation:**

- **3m:** signals, crosses, pullback, one-bar dip — use **last completed 3m candle** (and **next** completed candle where specified for entry).
- **15m:** bias, ATR, ADX, volume gate, RSI band — use **last completed 15m candle** only. Between 15m closes these values are **stable** until the next 15m completes.

**Resampling:** Fix `label` / `closed` / `origin` / IST session alignment and document them so live and backtest match.

---

## 3. Indicators (same stack on 3m and 15m closes)

On each timeframe, computed from **close** (Wilder RSI unless code comments state otherwise):

| Name | Definition |
|------|------------|
| **RSI** | **RSI(9)** |
| **PS (price strength)** | **EMA(9)** of the **RSI series** |
| **VS (volume strength)** | **WMA(21)** of the **RSI series** |

Notation: **PS₃, VS₃, RSI₃** on 3m; **PS₁₅, VS₁₅, RSI₁₅** on 15m.

**Warm-up:** No trades until enough **completed** bars exist for RSI / EMA(9) / WMA(21) on RSI (driven primarily by **WMA(21)**).

---

## 4. Roles by timeframe

| TF | Role |
|----|------|
| **15m (last closed)** | **Bias** + **ATR(14)**, **ADX(14)**, **volume vs prior 15m**, **RSI band 40–70** on **RSI₁₅** |
| **3m (last closed)** | **Cross** (timing), **pullback** + **one-bar dip** rules; **entry on the next completed 3m after retracement** (see §7–8) |

---

## 5. 3m cross (timing; completed bars)

Compare **previous** vs **current** completed 3m bar:

- **Bullish / CE:** **PS₃** crosses **up** through **VS₃**: prior **PS₃ < VS₃**, current **PS₃ ≥ VS₃**.
- **Bearish / PE:** **PS₃** crosses **down** through **VS₃**: prior **PS₃ > VS₃**, current **PS₃ ≤ VS₃**.

No valid cross → **HOLD** (no new entry from this rule).

Map CE/PE to **long premium** vs **short premium** per product (`trade_regime` / settings).

---

## 6. 15m permission & filters (last completed 15m only)

### 6.1 Bias (direction)

- **CE / bullish permission:** **PS₁₅ ≥ VS₁₅**
- **PE / bearish permission:** **PS₁₅ ≤ VS₁₅**

If **strict** mode and 15m PS/VS unavailable (insufficient history) → **block**.

### 6.2 ATR — period & gate

- **ATR(14)** on **15m** (Wilder / standard in code; same in backtest and live).
- **Definition:** Let **range** = **high − low** of the **last completed 15m bar**. Let **ATR** = ATR(14) at that bar.
- **Gate:** ratio **R = range / ATR** must lie in **[R_min, R_max]** (config; e.g. **0.5–2.5** — confirm in settings).

### 6.3 ADX

- **ADX(14)** on **15m**, last completed bar.
- **Gate:** **ADX ≥ ADX_min** (config; e.g. **10**).

### 6.4 Volume

- Let **V_last** = volume of **last completed** 15m bar, **V_prev** = volume of **immediately prior** completed 15m bar.
- **Hard gate:** **V_last ≥ 1.10 × V_prev** (10% above prior 15m).

### 6.5 RSI band (15m)

- **Gate:** **40 ≤ RSI₁₅ ≤ 70** (same RSI(9) family as PS/VS unless explicitly overridden).

---

## 7. Pullback & one-bar dip (3m, completed candles)

**Pullback (concept):** PS **pulls toward** VS during the move, then **retraces** back to the required side — used to avoid chasing the first thrust.

**One-bar dip exception (completed 3m candles only):**

- **Bullish (CE) context:** **PS₃** may be **below** **VS₃** for **at most one** completed 3m candle; on the **next** completed 3m candle, **PS₃ ≥ VS₃** must hold, or **no entry**.
- **Bearish (PE) context:** **PS₃** may be **above** **VS₃** for **at most one** completed 3m candle; on the **next** completed 3m candle, **PS₃ ≤ VS₃** must hold, or **no entry**.

**Context:** Apply after **15m permission** is already valid (bias + filters).

---

## 8. Entry timing vs retracement

- **Retracement** is **recognized** on a **completed** 3m candle **T** (pullback / one-bar dip satisfied as coded).
- **Entry** is evaluated on the **next completed 3m candle after T** (**T+1**), not on the close of **T** alone — subject to all gates still passing at **T+1**.

---

## 9. Conviction score (weighted, 0–100)

Reject entry if **conviction < min_conviction_pct** (e.g. **80**).

### 9.1 Weights (original ratio 30:35:20:15:5 scaled to sum 100)

| Component | Weight |
|-----------|--------|
| volume | **29** |
| ps_vs | **33** |
| rsi | **19** |
| align | **14** |
| adx | **5** |

**Aggregate:**

\[
\text{conviction} = \frac{29\,s_{\text{vol}} + 33\,s_{\text{psvs}} + 19\,s_{\text{rsi}} + 14\,s_{\text{align}} + 5\,s_{\text{adx}}}{100}
\]

Each **s_*** ∈ **[0, 100]**.

### 9.2 Sub-scores (closed formulas)

**a) Volume — \(s_{\text{vol}}\)** (15m)

- If **V_last ≥ 1.10 × V_prev:** **100**
- Else: \(s_{\text{vol}} = \min\left(100,\ 100 \cdot \dfrac{V_{\text{last}}}{1.10 \cdot V_{\text{prev}}}\right)\) (if denominator 0, treat as **0**)

**b) PS vs — \(s_{\text{psvs}}\)** (3m, at entry evaluation bar close)

- \(d = |PS_3 - VS_3|\)
- \(s_{\text{psvs}} = \min(100,\ 25 + 75 \cdot \min(1,\ d / 15))\) — **15** is a tunable scale constant

**c) RSI — \(s_{\text{rsi}}\)** (15m)

- If **RSI₁₅** outside **[40, 70]:** **0** (consistent with hard gate)
- Else: \(s_{\text{rsi}} = \text{clip}_{[0,100]}\left(100 - \dfrac{|RSI_{15} - 55|}{15} \cdot 100\right)\) (peak at RSI **55**)

**d) Align — \(s_{\text{align}}\)** (15m)

- **100** if bias matches direction (**CE:** PS₁₅ ≥ VS₁₅; **PE:** PS₁₅ ≤ VS₁₅), else **0**

**e) ADX — \(s_{\text{adx}}\)** (15m)

- Parameters: **ADX_min** (e.g. 10), **ADX_ref** (e.g. 30)
- If **ADX < ADX_min:** **0**
- Else: \(s_{\text{adx}} = 100 \cdot \min\left(1,\ \dfrac{ADX - ADX_{\min}}{ADX_{\text{ref}} - ADX_{\min}}\right)\)

**Note:** Volume **gate** (§6.4) and volume **sub-score** both use the **1.10× prior** idea; gate is binary, sub-score rewards margin above the gate.

---

## 10. Cooldown & chop

- **Cooldown:** **60 seconds** minimum between **new entry attempts** after a signal is consumed / order placed (configurable).
- Optional tightening: **one entry per 15m permission window** if overtrading persists.

---

## 11. Execution & risk

- **Strike:** ATM by default; ITM offset / delta bands per product settings.
- **Stops / targets / square-off:** per user strategy settings (e.g. premium % SL, session end).
- **Runtime:** Platform recommendation cycle ~**20s** is acceptable; **signal changes on bar close**, not every tick.
- **Performance:** Reuse one **3m** fetch per refresh; derive **15m** once; avoid redundant broker pulls (align with S004 performance guidelines).

---

## 12. Logging & diagnostics

Log **MTF provenance** where useful: merged row counts, **3m** length, **15m** bars available, timestamps of **last completed** 3m and 15m bars, and filter pass/fail — for debugging “why no trade.”

---

## 13. S004 implementation note

Implemented as **`strategyType`: `ps-vs-mtf`** with nested **`psVsMtf`** in `strategy_details_json`, catalog id **`strat-ps-vs-mtf`** / **`1.0.0`**, signal code in `app/strategies/ps_vs_mtf.py`, and candidates in `trades_service._get_live_candidates_ps_vs_mtf`.

### 13.1 Performance (`.cursor/rules/performance.mdc`)

- **One** Kite `historical_data` call per refresh: **`3minute`** Bank Nifty index candles only; **15m** is **resampled in memory** (no second interval fetch).
- Broker SDK runs under **`asyncio.to_thread`** like other strategies.
- **No** extra historical pulls for MTF; option chain follows the same pattern as StochasticBNF (single chain build per candidate pass).

---

## 14. Revision summary

| Topic | Decision |
|--------|----------|
| Raw TF | 3m only; 15m resampled |
| PS / VS | EMA(9) of RSI, WMA(21) of RSI; RSI(9) |
| 15m role | Permission + ATR/ADX/volume/RSI band |
| 3m role | Cross + pullback + one-bar dip |
| Entry bar | **Next completed 3m after retracement** |
| Volume | **V_last ≥ 1.10 × V_prev** (15m) |
| ATR / ADX | **14** / **14** on 15m; ATR gate = **(H−L)/ATR** band |
| RSI band | **40–70** on **RSI₁₅** |
| Conviction | Weights **29, 33, 19, 14, 5**; formulas in §9.2 |
| Cooldown | **1 minute** |
| Divergence | Omitted v1 |
