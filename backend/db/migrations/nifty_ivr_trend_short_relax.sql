-- Nifty IVR Trend Short: symmetric CE/PE regime (EMA9 cross below EMA21 + LTP<VWAP), direct RSI band, IVR 55–100, VIX delta bands.
-- Applies to every catalog row for this strategy_id.
UPDATE s004_strategy_catalog
SET strategy_details_json = '{
      "strategyType": "rule-based",
      "positionIntent": "short_premium",
      "displayName": "Nifty IVR Trend Short",
      "description": "NIFTY short premium. Per-leg regime on option LTP: fresh EMA9 cross below EMA21 within emaCrossover.maxCandlesSinceCross and last close < leg VWAP for both sell-CE and sell-PE (symmetric). If both legs qualify at one strike, the more recent cross wins. VIX→delta via shortPremiumDeltaVixBands; leg RSI band via indicators.rsi when shortPremiumRsiDirectBand. Per-strike chain IVR in [ivr.minThreshold, maxLegThreshold]. No ADX; no min OI/volume when both are 0.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 5, "description": "Fresh cross within this many candles on the leg LTP series (default 5 if unset)."},
        "ivr": {"minThreshold": 55, "maxLegThreshold": 100, "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive)."},
        "rsi": {"period": 14, "min": 65, "max": 100, "description": "Option-leg RSI (on LTP series). With shortPremiumRsiDirectBand=true, leg RSI must lie in [min, max] (overbought band)."},
        "vwap": {"description": "Leg last close vs leg VWAP: required LTP close < VWAP for both sell-PE and sell-CE regime paths (spotRegimeMode ema_cross_vwap)."}
      },
      "strikeSelection": {
        "minOi": 0,
        "minVolume": 0,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "shortPremiumDeltaVixBands": {
          "threshold": 17,
          "vixAbove": {
            "deltaMinCE": 0.29,
            "deltaMaxCE": 0.35,
            "deltaMinPE": -0.35,
            "deltaMaxPE": -0.29
          },
          "vixAtOrBelow": {
            "deltaMinCE": 0.33,
            "deltaMaxCE": 0.40,
            "deltaMinPE": -0.40,
            "deltaMaxPE": -0.33
          }
        },
        "shortPremiumDeltaOnlyStrikes": true,
        "shortPremiumRsiDirectBand": true,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "selectStrikeByMinGamma": true,
        "maxStrikeRecommendations": 1,
        "shortPremiumAsymmetricDatm": false,
        "shortPremiumCeMinSteps": 2,
        "shortPremiumCeMaxSteps": 4,
        "shortPremiumPeMinSteps": -4,
        "shortPremiumPeMaxSteps": 2,
        "shortPremiumLegScoreMode": "three_factor",
        "shortPremiumRsiBelow": 50,
        "shortPremiumIvrSkewMin": 5,
        "shortPremiumPcrBonusVsChain": true,
        "shortPremiumPcrChainEpsilon": 0,
        "description": "India VIX first; delta-only strike ladder. VIX>17 → CE +0.29..+0.35, PE -0.35..-0.29; VIX≤17 → CE +0.33..+0.40, PE -0.40..-0.33. Regime: same for CE/PE — fresh EMA9<EMA21 cross + LTP<VWAP on leg. shortPremiumRsiDirectBand: leg RSI in indicators.rsi min–max (65–100). IVR band on chain ivr. ±strikes/side floor 12 (env S004_SHORT_PREMIUM_DELTA_ONLY_STRIKES_EACH_SIDE). DTE≥2; Tue weekly; min gamma; three_factor + skew/PCR."
      },
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Symmetric sell CE/PE: regimeSellPe/Ce = fresh EMA9 cross below EMA21 + LTP < leg VWAP (tie-break if both). Leg RSI in [indicators.rsi.min, max] when shortPremiumRsiDirectBand. Leg IVR in [ivr.minThreshold, maxLegThreshold]. three_factor technical up to 3 points + skew/PCR bonuses. Auto-trade at autoTradeScoreThreshold."
    }'::jsonb,
    description = 'NIFTY naked short premium: symmetric CE/PE (EMA9 cross below EMA21 + LTP<VWAP on leg), chain IVR 55–100, leg RSI 65–100 (direct band), VIX delta bands. High risk; margin required.',
    updated_at = NOW()
WHERE strategy_id = 'strat-nifty-ivr-trend-short';
