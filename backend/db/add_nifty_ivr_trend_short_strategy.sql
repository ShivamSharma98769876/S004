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
    '1.1.0',
    'Nifty IVR Trend Short',
    'NIFTY naked short premium: per-strike leg regime (EMA9/21 cross + LTP vs leg VWAP), chain IVR band, |delta| 0.29–0.35. High risk; margin required.',
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
      "description": "NIFTY short premium. Regime is per strike on each option leg (not index spot): on that leg LTP series, fresh EMA9 cross above EMA21 within emaCrossover.maxCandlesSinceCross (default 5) and last close < leg VWAP → eligible sell PE. Fresh EMA9 cross below EMA21 and last close < leg VWAP → eligible sell CE. If both legs qualify at the same strike, the more recent cross wins. Chain IVR must lie between min and max leg thresholds. No ADX. Option-leg score excludes volume spike. No min OI/volume when both are 0.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 5, "description": "Fresh cross within this many candles on the leg LTP series (default 5 if unset)."},
        "ivr": {"minThreshold": 30, "maxLegThreshold": 55, "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive)."},
        "rsi": {"period": 14, "min": 45, "max": 85, "description": "RSI band for option-leg premium scoring; bearish leg uses mirrored lower band."},
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
    '1.1.0',
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
