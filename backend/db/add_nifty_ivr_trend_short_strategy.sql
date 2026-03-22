-- Idempotent: add Nifty IVR Trend Short strategy (run against existing DB if functional_seed already applied).
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
    '1.0.0',
    'Nifty IVR Trend Short',
    'NIFTY-only naked short options when implied-vol rank (within chain) is elevated and NIFTY spot trend aligns: sell put in uptrend, sell call in downtrend. |Delta| 0.29-0.35. Requires margin; not suitable for small accounts.',
    'HIGH',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "positionIntent": "short_premium",
      "displayName": "Nifty IVR Trend Short",
      "description": "NIFTY spot + chain only. Sells naked PE when spot trend is bullish and naked CE when bearish. Requires elevated chain IVR (IV rank proxy). Strikes limited to |delta| 0.29-0.35. Lot size, target points, and stop loss from Settings apply on execution.",
      "indicators": {
        "adx": {"period": 14, "minThreshold": 20, "description": "ADX > 20 on NIFTY spot: skip signals in weak/choppy markets."},
        "ema": {"fast": 9, "slow": 21, "description": "NIFTY spot EMA alignment defines bullish vs bearish regime."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Fresh bullish/bearish EMA cross within last 10 spot candles contributes to score."},
        "ivr": {"minThreshold": 55, "description": "Per-strike IVR (percentile within same expiry chain) must be >= 55 — elevated IV vs that chain."},
        "rsi": {"period": 14, "min": 45, "max": 75, "description": "Bullish spot RSI band for uptrend score; bearish score uses mirrored lower band."},
        "vwap": {"description": "NIFTY spot vs VWAP for trend direction."},
        "volumeSpike": {"minRatio": 1.15, "description": "Spot volume vs recent average on NIFTY."}
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "description": "Liquid strikes; short leg absolute delta between 0.29 and 0.35."
      },
      "scoreThreshold": 4,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Spot trend score 0-5 on NIFTY (VWAP, EMA, crossover, RSI band, volume). Bullish regime: sell PE only. Bearish regime: sell CE only. Leg must have chain IVR >= minThreshold and |delta| in band. Signal when spot score >= 4."
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
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
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
    '1.0.0',
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
