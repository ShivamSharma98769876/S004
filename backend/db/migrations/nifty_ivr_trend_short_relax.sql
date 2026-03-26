-- Nifty IVR Trend Short: strike-leg EMA cross + LTP vs leg VWAP (spotRegimeMode ema_cross_vwap), IVR band, no ADX, no volume in leg score, optional zero OI/vol mins.
-- Applies to every catalog version of this strategy so older 1.0.0 rows are corrected.
UPDATE s004_strategy_catalog
SET strategy_details_json = '{
      "strategyType": "rule-based",
      "positionIntent": "short_premium",
      "displayName": "Nifty IVR Trend Short",
      "description": "NIFTY short premium. Regime is per strike on each option leg (not index spot): on that leg LTP series, fresh EMA9 cross above EMA21 within emaCrossover.maxCandlesSinceCross (default 5) and last close < leg VWAP → eligible sell PE. Fresh EMA9 cross below EMA21 and last close < leg VWAP → eligible sell CE. If both legs qualify at the same strike, the more recent cross wins. Chain IVR must lie between min and max leg thresholds. No ADX. Option-leg score excludes volume spike. No min OI/volume when both are 0.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 5, "description": "Fresh cross within this many candles on the leg LTP series (default 5 if unset)."},
        "ivr": {"minThreshold": 30, "maxLegThreshold": 55, "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive)."},
        "rsi": {"period": 14, "min": 45, "max": 85, "description": "RSI band for option-leg premium scoring; bearish leg uses mirrored lower band."},
        "vwap": {"description": "Leg last close vs leg VWAP: required LTP close < VWAP for both sell-PE and sell-CE regime paths (see code spotRegimeMode ema_cross_vwap)."}
      },
      "strikeSelection": {
        "minOi": 0,
        "minVolume": 0,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "selectStrikeByMinGamma": true,
        "maxStrikeRecommendations": 1,
        "description": "|delta| 0.29–0.35; DTE >= 2; Tuesday weekly preference. Lowest BS gamma in band. No minimum OI/volume."
      },
      "scoreThreshold": 3,
      "scoreMax": 4,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Strike-leg regime via regimeSellPe / regimeSellCe (EMA9/21 cross + LTP < leg VWAP on that leg; tie-break if both). No NIFTY spot trend score for this mode. Option leg score up to 4 (VWAP/EMA/cross/RSI; volume spike off). Leg IVR in [minThreshold, maxLegThreshold]. Auto-trade at autoTradeScoreThreshold."
    }'::jsonb,
    description = 'NIFTY naked short premium: per-strike leg regime (EMA9/21 cross + LTP vs leg VWAP), chain IVR band, |delta| 0.29–0.35. High risk; margin required.',
    updated_at = NOW()
WHERE strategy_id = 'strat-nifty-ivr-trend-short';
