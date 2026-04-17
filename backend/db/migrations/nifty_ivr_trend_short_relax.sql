-- Nifty IVR Trend Short: symmetric CE/PE regime (EMA9 cross below EMA21 + LTP<VWAP), leg RSI <80 and falling vs prior bar, IVR 40–100, VIX delta bands.
-- Applies to every catalog row for this strategy_id.
UPDATE s004_strategy_catalog
SET strategy_details_json = '{
      "strategyType": "rule-based",
      "positionIntent": "short_premium",
      "displayName": "Nifty IVR Trend Short",
      "description": "NIFTY short premium. Per-leg regime on option LTP: fresh EMA9 cross below EMA21 and premium-weakness context with relaxed VWAP eligibility buffer. VIX→delta via widened shortPremiumDeltaVixBands. Leg RSI uses shortPremiumRsiBelow without mandatory falling-vs-prior bar. Per-strike chain IVR in [ivr.minThreshold, maxLegThreshold]. Strike liquidity: minOi 3000, minVolume 200.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 8, "description": "Fresh cross within this many candles on the leg LTP series."},
        "ivr": {"minThreshold": 45, "maxLegThreshold": 100, "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive)."},
        "rsi": {"period": 14, "min": 0, "max": 100, "description": "Option-leg RSI on LTP series (period). Leg RSI must be below shortPremiumRsiBelow; falling-vs-prior bar is optional via shortPremiumRsiDecreasing."},
        "vwap": {"description": "Leg last close vs leg VWAP: required LTP close < VWAP for both sell-PE and sell-CE regime paths (spotRegimeMode ema_cross_vwap)."}
      },
      "strikeSelection": {
        "minOi": 3000,
        "minVolume": 200,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "shortPremiumDeltaVixBands": {
          "threshold": 17,
          "vixAbove": {
            "deltaMinCE": 0.25,
            "deltaMaxCE": 0.40,
            "deltaMinPE": -0.40,
            "deltaMaxPE": -0.25
          },
          "vixAtOrBelow": {
            "deltaMinCE": 0.25,
            "deltaMaxCE": 0.40,
            "deltaMinPE": -0.40,
            "deltaMaxPE": -0.25
          }
        },
        "shortPremiumDeltaOnlyStrikes": true,
        "shortPremiumRsiDirectBand": false,
        "shortPremiumRsiDecreasing": false,
        "shortPremiumVwapEligibleBufferPct": 0.3,
        "shortPremiumThreeFactorRequireLtpBelowVwapForEligible": false,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "selectStrikeByMinGamma": true,
        "maxStrikeRecommendations": 3,
        "shortPremiumAsymmetricDatm": false,
        "shortPremiumCeMinSteps": 2,
        "shortPremiumCeMaxSteps": 4,
        "shortPremiumPeMinSteps": -4,
        "shortPremiumPeMaxSteps": 2,
        "shortPremiumLegScoreMode": "three_factor",
        "shortPremiumRsiBelow": 80,
        "shortPremiumIvrSkewMin": 5,
        "shortPremiumPcrBonusVsChain": true,
        "shortPremiumPcrChainEpsilon": 0,
        "description": "India VIX first; delta-only strike ladder. Min OI 3000 and min volume 200 per strike. VIX bands widened to CE +0.25..+0.40 / PE -0.40..-0.25. IVR min 45 (per-strike). Regime keeps EMA weakness with relaxed VWAP eligibility buffer (shortPremiumVwapEligibleBufferPct) and no mandatory RSI decreasing check. Auto at score ≥3.5. ±strikes/side floor 12 (env). DTE≥2; Tue weekly; min gamma; three_factor + skew/PCR."
      },
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Symmetric sell CE/PE with widened VIX delta bands. Regime uses EMA weakness and relaxed VWAP eligibility buffer; RSI must be below shortPremiumRsiBelow without mandatory decreasing filter. Leg IVR in [ivr.minThreshold, maxLegThreshold]. three_factor technical up to 3 points + skew/PCR bonuses. Auto-trade at autoTradeScoreThreshold."
    }'::jsonb,
    description = 'NIFTY naked short premium: symmetric CE/PE with widened VIX delta bands and relaxed RSI/VWAP entry filters. Chain IVR 45–100. Min OI 3k / volume 200 per strike. High risk; margin required.',
    updated_at = NOW()
WHERE strategy_id = 'strat-nifty-ivr-trend-short';
