-- Idempotent: add / refresh Nifty IVR Trend Short strategy (strike-leg ema_cross_vwap regime).
-- psql $DATABASE_URL -f db/add_nifty_ivr_trend_short_strategy.sql

INSERT INTO s004_strategy_catalog (
    strategy_id,
    version,
    display_name,
    description,
    risk_profile,
    owner_type,
    publish_status,
    execution_modes,
    supported_segments,
    performance_snapshot,
    strategy_details_json,
    created_by
)
SELECT
    'strat-nifty-ivr-trend-short',
    '1.2.0',
    'Nifty IVR Trend Short',
    'NIFTY naked short premium: symmetric CE/PE (EMA9 cross below EMA21 + LTP<VWAP on leg), chain IVR 40–100, leg RSI <80 and falling vs prior bar, VIX delta bands. High risk; margin required.',
    'HIGH',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "rule-based",
      "positionIntent": "short_premium",
      "displayName": "Nifty IVR Trend Short",
      "description": "NIFTY short premium. Per-leg regime on option LTP: fresh EMA9 cross below EMA21 within emaCrossover.maxCandlesSinceCross and last close < leg VWAP for both sell-CE and sell-PE (symmetric). If both legs qualify at one strike, the more recent cross wins. VIX→delta via shortPremiumDeltaVixBands; leg RSI when shortPremiumRsiDecreasing: RSI < shortPremiumRsiBelow and RSI strictly below prior-bar leg RSI (same period as indicators.rsi). Per-strike chain IVR in [ivr.minThreshold, maxLegThreshold]. No ADX; no min OI/volume when both are 0.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 8, "description": "Fresh cross within this many candles on the leg LTP series."},
        "ivr": {"minThreshold": 40, "maxLegThreshold": 100, "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive)."},
        "rsi": {"period": 14, "min": 0, "max": 100, "description": "Option-leg RSI on LTP series (period). With shortPremiumRsiDecreasing=true and three_factor, leg RSI must be < shortPremiumRsiBelow and falling vs the prior bar."},
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
        "shortPremiumRsiDirectBand": false,
        "shortPremiumRsiDecreasing": true,
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
        "description": "India VIX first; delta-only strike ladder. VIX>17 → CE +0.29..+0.35, PE -0.35..-0.29; VIX≤17 → CE +0.33..+0.40, PE -0.40..-0.33. Regime: same for CE/PE — fresh EMA9<EMA21 cross + LTP<VWAP on leg. shortPremiumRsiDecreasing: leg RSI < shortPremiumRsiBelow (80) and falling vs prior bar. IVR band on chain ivr. ±strikes/side floor 12 (env S004_SHORT_PREMIUM_DELTA_ONLY_STRIKES_EACH_SIDE). DTE≥2; Tue weekly; min gamma; three_factor + skew/PCR."
      },
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Symmetric sell CE/PE: regimeSellPe/Ce = fresh EMA9 cross below EMA21 + LTP < leg VWAP (tie-break if both). Leg RSI below shortPremiumRsiBelow and decreasing vs prior bar when shortPremiumRsiDecreasing. Leg IVR in [ivr.minThreshold, maxLegThreshold]. three_factor technical up to 3 points + skew/PCR bonuses. Auto-trade at autoTradeScoreThreshold."
    }'::jsonb,
    u.id
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, version) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    strategy_details_json = EXCLUDED.strategy_details_json,
    updated_at = NOW();

INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    'strat-nifty-ivr-trend-short',
    '1.2.0',
    1,
    '{
      "timeframe": "3-min",
      "min_entry_strength_pct": 0,
      "max_strike_distance_atm": 5,
      "target_points": 10,
      "sl_points": 15,
      "trailing_sl_points": 20
    }'::jsonb,
    TRUE,
    u.id,
    'NIFTY short premium template'
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING;
