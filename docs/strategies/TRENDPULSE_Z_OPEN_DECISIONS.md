# TrendPulse Z two-tier — open decisions (options + questions)

**Use this sheet to answer; merge into `TRENDPULSE_Z_TWO_TIER_IMPLEMENTATION_SPEC.md` § Open points when the file is not locked.**

Reply in one block, e.g.  
`Q-A4: B | Q-B2: B | Q-C6: B | …`

---

### Q-A4 — Index momentum beyond PS/VS cross

**Why it matters:** Reduces false crosses in a flat tape; wrong window may kill valid trends.

| Opt | Definition |
|-----|------------|
| **A** | No extra filter (cross + HTF + ADX only). | Yes 
| **B** | \|Spot % change\| **≥ 0.01%** over last **20 minutes**. |NA
| **C** | \|Spot % change\| **≥ 0.05%** over last **20 minutes**. | NA
| **D** | \|Spot % change\| **≥ 0.01%** over last **5 completed 5m** bars. | NA

**→ Your answer:** Q-A4 = **A_** 

---

### Q-B2 — Meaning of “15 min” warm-up

**Why it matters:** Drives how much history we load and when the first trade is allowed.

| Opt | Definition |
|-----|------------|
| **A** | **15 closed 5m** index bars (75 min wall time). |
| **B** | Series covers **≥ 15 minutes** exchange time (e.g. 3× 5m). |
| **C** | **Block** first **5 minutes** after 09:15 IST daily. |
| **D** | Use existing **math warm-up** from z-window / slope (ignore “15 min” label). |

**→ Your answer:** Q-B2 = **C**

---

### Q-B6 — Cooldown after EXIT (same direction)

**Why it matters:** Limits churn after SL/target on the same “story”.

| Opt | Definition |
|-----|------------|
| **A** | No cooldown. |
| **B** | **15** minutes. |
| **C** | **30** minutes. |
| **D** | Until **next 5m ST bar** close after exit. |

**→ Your answer:** Q-B6 = **\A_** 
---

### Q-C6 — Delta band around **0.45**

**Why it matters:** **±0.05** ⇒ only **0.40–0.50** — often **zero** strikes; **±0.05** is typical.

| Opt | Band |
|-----|------|
| **A** | **±0.005** (as written). |
| **B** | **±0.05** (likely if typo). |
| **C** | Target **0.40**, band **±0.10**. |
| **D** | Hard **\|delta\| ∈ [0.40, 0.50]** only. |

**→ Your answer:** Q-C6 = **\B**

---

### Q-C7 — “Too expensive” (intrinsic vs time value)

**Why it matters:** Must be **codeable** (intrinsic from spot/strike; TV = premium − intrinsic).

| Opt | Rule sketch |
|-----|----------------|
| **A** | **(premium − intrinsic) / premium ≥ 0.25** (reject deep ITM). |
| **B** | Cap **time value ₹** or **% of spot** (you fill numbers in JSON). |
| **C** | **premium / spot ≤ Z%** (Z per DTE bucket). |
| **D** | **A and C** both required. |

**→ Your answer:** Q-C7 = **\D** 

---

### Q-C8 — DTE − 2 → next weekly

**Why it matters:** Calendar vs trading-day changes roll date near holidays.

| Opt | Definition |
|-----|------------|
| **A** | **Calendar** DTE ≤ 2 ⇒ next weekly. |
| **B** | **Trading sessions** left ≤ 2 ⇒ next weekly. |
| **C** | Always pick weekly with **calendar DTE ≥ 3**. |
| **D** | Config key **`minDteCalendarDays`** (default 3). |

**→ Your answer:** Q-C8 = **D**

---

### Q-D3 — Depth: sum qty vs sum ₹

**Why it matters:** Gate **10_000** must match **units**.

| Opt | Metric |
|-----|--------|
| **A** | Sum **qty** top 5/side. |
| **B** | Sum **price × qty** (₹ depth). |
| **C** | Both; **gate on A**, log **B**. |
| **D** | **A**; tune threshold after one live log sample. |

**→ Your answer:** Q-D3 = **D**

---

### Q-D9 — Level-1 concentration

**Why it matters:** One huge top tick can be spoof or illusory support.

| Opt | Definition |
|-----|------------|
| **A** | No rule. |
| **B** | **Skip** if level-1 / sideDepth5 **> 70%**. |
| **C** | **Down-rank** only. |
| **D** | **Skip** if **> p%**; **p =** \_\_ (e.g. 60). |

**→ Your answer:** Q-D9 = **\C_**

---

### Q-E5 — Option LTP momentum (k-of-m)

**Why it matters:** Aligns premium drift with signal; 3m bars.

| Opt | Definition |
|-----|------------|
| **A** | Not used. |
| **B** | **2 of last 3** bars: LTP **up** (CE) / **down** (PE). |
| **C** | **3 of last 5** same. |
| **D** | Only **last bar** direction. |

**→ Your answer:** Q-E5 = **\B_** 
---

### Q-F2 — Weight split (sum 100%)

**Why it matters:** Defines composite behaviour (index / strike / depth / technicals).

| Opt | index : strike : depth : technicals |
|-----|--------------------------------------|
| **A** | **25 : 25 : 25 : 25** |
| **B** | **35 : 25 : 20 : 20** |
| **C** | **25 : 20 : 30 : 25** |
| **D** | **Custom** four integers → **\_ : _ : _ : _** |

**→ Your answer:** Q-F2 = **\C_**

---

### Q-F3 — “10/10” / auto floor

**Why it matters:** Single scale avoids ambiguous “perfect 10”.

| Opt | Auto when |
|-----|-----------|
| **A** | Four **0–10** pillars; auto only **40/40**. |
| **B** | Four pillars; auto **sum ≥ 34** (~85%). |
| **C** | One **0–100** score; auto **≥ 90**; confidence = score. |
| **D** | One **0–100**; auto **≥ 80**. |

**→ Your answer:** Q-F3 = **\A_**

---

### Q-J2 — Profit factor for promoting defaults

| Opt | Rule |
|-----|------|
| **A** | **PF ≥ 1.2** on last **20** paper trades. |
| **B** | **PF ≥ 1.5** on last **30** trades. |
| **C** | Qualitative only. |
| **D** | **PF ≥** \_\_ on **N =** \_\_ trades. |

**→ Your answer:** Q-J2 = **\C_**

---

### Q-J3 — Paper soak before Live auto

| Opt | Days |
|-----|------|
| **A** | **5** |
| **B** | **10** |
| **C** | **20** |
| **D** | None required. |

**→ Your answer:** Q-J3 = **\D_**

---

### Q-WATCH — How to show “watch” (Tier 1 OK, Tier 2 none)

| Opt | Delivery |
|-----|----------|
| **A** | Extra **API fields** on snapshot; **no** DB row. |
| **B** | **UI-only** from a small eval endpoint. |
| **C** | Persist row status **`WATCH`** (DB + reporting impact). |

**→ Your answer:** Q-WATCH = **\C_**

---

## Document history

| Date | Note |
|------|------|
| 2026-03-24 | Created (spec main file may be open in IDE — use this companion until merged). |
