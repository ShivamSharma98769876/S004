-- Idempotent seed data for core workflows

INSERT INTO s004_users (username, full_name, role, status)
VALUES
    ('admin', 'System Admin', 'ADMIN', 'ACTIVE'),
    ('trader1', 'Primary Trader', 'USER', 'ACTIVE')
ON CONFLICT (username) DO UPDATE
SET full_name = EXCLUDED.full_name,
    role = EXCLUDED.role,
    status = EXCLUDED.status,
    updated_at = NOW();

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
    'strat-trendsnap-momentum',
    '1.0.0',
    'TrendSnap Momentum',
    'Momentum crossover option strategy with configurable risk.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY', 'BANKNIFTY', 'FINNIFTY'],
    '{"win_rate_30d": 61.2, "pnl_30d": 12450.25}'::jsonb,
    '{
      "displayName": "TrendSnap Momentum",
      "description": "Momentum crossover option strategy. Enters when short-term momentum confirms direction with price-action continuation and risk checks; exits use SL, target, and breakeven rules from Settings.",
      "indicators": {
        "adx": {"period": 14, "minThreshold": 20, "description": "ADX > 20 = strong trend. No signals when ADX < 20 (weak/choppy market)."},
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 > EMA21 = bullish momentum (short-term above long-term)"},
        "emaCrossover": {"bonus": 1, "maxCandlesSinceCross": 10, "description": "Bullish crossover within last 10 candles"},
        "ivr": {"bonus": 1, "maxThreshold": 25, "description": "IVR < 25 = low IV (cheap options) = +1 score bonus. IVR from Option Analytics per strike."},
        "rsi": {"period": 14, "min": 45, "max": 75, "description": "RSI in 45-75 = not overbought, bullish zone"},
        "vwap": {"description": "Price above VWAP = bullish intraday bias"},
        "volumeSpike": {"minRatio": 1.15, "description": "Current volume > 1.15x average = confirmation (relaxed)"}
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "description": "Liquidity: min OI 10k, min volume 500. Max 3 steps OTM to reduce theta decay. Best strike: delta ~0.45 CE / -0.45 PE. Rank by score, volume spike, OI change, delta fit, ATM distance."
      },
      "scoreThreshold": 4,
      "scoreMax": 6,
      "autoTradeScoreThreshold": 5,
      "scoreDescription": "Score 0-6: Primary(VWAP) + EMA + RSI + Volume + EMA crossover bonus + IVR bonus (when IVR<25). Crossover freshness: cross within 10 candles. ADX filter: no signals when ADX<20. Strike selection: liquidity (min OI/vol), rank by score, volume spike, OI change, delta fit. Signal when score >= 4. Auto-trade when score >= 5."
    }'::jsonb,
    u.id
FROM s004_users u
WHERE u.username = 'admin'
ON CONFLICT (strategy_id, version) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    owner_type = EXCLUDED.owner_type,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
    updated_at = NOW();

-- Multi-heuristic voting strategy (weighted average of OI buildup, IVR, volume spike, RSI, EMA, VWAP, delta fit, OI change, LTP change)
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
    'strat-heuristic-voting',
    '1.0.0',
    'Multi-Heuristic Strike Selector',
    'Weighted scoring from multiple heuristics: OI buildup, IVR, volume spike, RSI, EMA, VWAP, delta fit, OI change, LTP change. No single rule dominates; strikes need broad strength.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY', 'BANKNIFTY', 'FINNIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "heuristic-voting",
      "displayName": "Multi-Heuristic Strike Selector",
      "description": "Weighted scoring from multiple heuristics. Each heuristic scores 1-5; weighted average produces final score. Eligible when score >= 3.0.",
      "heuristics": {
        "oiBuildup": {"enabled": true, "weight": 1.2},
        "ivr": {"enabled": true, "weight": 1.0},
        "volumeSpike": {"enabled": true, "weight": 1.0},
        "rsi": {"enabled": true, "weight": 0.8},
        "emaAlignment": {"enabled": true, "weight": 0.9},
        "primaryVwap": {"enabled": true, "weight": 1.0},
        "deltaFit": {"enabled": true, "weight": 0.8},
        "oiChange": {"enabled": true, "weight": 0.7},
        "ltpChange": {"enabled": true, "weight": 0.6}
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Liquidity: min OI 10k, min volume 500."
      },
      "heuristicEnhancements": {
        "enabled": true,
        "maxMoneynessPct": 1.2,
        "moneynessOverrideMinScore": 4.5,
        "flatSpotBandPct": 0.08,
        "flatOiPct": 0.5,
        "volumeHighRatio": 1.5,
        "oiChurnAbsPct": 0.35,
        "churnScoreMultiplier": 0.94,
        "ltpStrongPct": 2.0,
        "oiWeightWhenLtpStrong": 0.45,
        "maxLtpOiCombinedWeightShare": 0.88,
        "jointMinMult": 0.72,
        "jointMaxMult": 1.08,
        "bestPerSideMinGap": 0.35,
        "singleDirectionOnly": false,
        "singleDirectionMinSpread": 0.4,
        "ceRequiresSpotNotDown": false,
        "peRequiresSpotNotUp": false,
        "directionalGateFlatBandPct": 0.05
      },
      "scoreThreshold": 3.0,
      "scoreMax": 5.0,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Weighted average of 9 heuristics (OI buildup, IVR, volume, RSI, EMA, VWAP, delta fit, OI change, LTP change). Post-filters: moneyness cap, DTE matrix, joint spot x OI, churn dampening, best CE and best PE. Signal when enhanced score >= 3.0."
    }'::jsonb,
    u.id
FROM s004_users u
WHERE u.username = 'admin'
ON CONFLICT (strategy_id, version) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    owner_type = EXCLUDED.owner_type,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
    updated_at = NOW();

-- TrendPulse Z: PS_z vs VS_z on 5m, HTF 15m bias, ADX gate (long CE/PE)
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
    'strat-trendpulse-z',
    '1.0.0',
    'TrendPulse Z (Balanced)',
    'NIFTY long options when z-scored price momentum crosses volume momentum on 5m and 15m HTF bias agrees. ADX gate on ST; chain IVR cap per strike. See docs/strategies/TRENDPULSE_Z_IMPLEMENTATION_PLAN.md.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "trendpulse-z",
      "positionIntent": "long_premium",
      "displayName": "TrendPulse Z (Balanced)",
      "description": "HTF 15m EMA bias; ST 5m PS_z vs VS_z cross; ADX on ST must exceed minimum. Long CE on bullish cross + bullish HTF; long PE on bearish cross + bearish HTF. Strikes filtered by liquidity and per-strike IVR.",
      "trendPulseZ": {
        "profile": "balanced",
        "stInterval": "5minute",
        "htfInterval": "15minute",
        "zWindow": 50,
        "slopeLookback": 4,
        "adxMin": 18,
        "adxPeriod": 14,
        "htfEmaFast": 13,
        "htfEmaSlow": 34,
        "ivRankMaxPercentile": 70,
        "candleDaysBack": 5,
        "session": { "enabled": false, "blockFirstMinutes": 15, "blockLastMinutes": 25 },
        "breadth": { "enabled": false, "requireSpotAligned": true, "minAbsSpotChgPct": 0.05, "requirePcrAligned": false }
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Same liquidity band as other NIFTY long strategies."
      },
      "scoreThreshold": 5,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 5,
      "scoreDescription": "Signal when TrendPulse engine fires (HTF/ST cross + ADX). Recommendation score is fixed at scoreMax when eligible; auto-trade uses autoTradeScoreThreshold."
    }'::jsonb,
    u.id
FROM s004_users u
WHERE u.username = 'admin'
ON CONFLICT (strategy_id, version) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    owner_type = EXCLUDED.owner_type,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
    updated_at = NOW();

-- NIFTY naked short premium: high chain IVR + spot trend; bullish -> short PE, bearish -> short CE
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
ON CONFLICT (strategy_id, version) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    owner_type = EXCLUDED.owner_type,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
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
    'strat-trendsnap-momentum',
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
    'Initial production template'
FROM s004_users u
WHERE u.username = 'admin'
ON CONFLICT (strategy_id, strategy_version, config_version) DO UPDATE
SET config_json = EXCLUDED.config_json,
    active = EXCLUDED.active,
    changed_by = EXCLUDED.changed_by,
    changed_reason = EXCLUDED.changed_reason;

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
ON CONFLICT (strategy_id, strategy_version, config_version) DO UPDATE
SET config_json = EXCLUDED.config_json,
    active = EXCLUDED.active,
    changed_by = EXCLUDED.changed_by,
    changed_reason = EXCLUDED.changed_reason;

INSERT INTO s004_strategy_subscriptions (
    user_id,
    strategy_id,
    strategy_version,
    mode,
    status
)
SELECT
    u.id,
    'strat-trendsnap-momentum',
    '1.0.0',
    'PAPER',
    'ACTIVE'
FROM s004_users u
WHERE u.username IN ('admin', 'trader1')
ON CONFLICT (user_id, strategy_id, strategy_version) DO UPDATE
SET status = 'ACTIVE',
    mode = EXCLUDED.mode,
    updated_at = NOW();

INSERT INTO s004_user_master_settings (
    user_id,
    go_live,
    engine_running,
    broker_connected,
    shared_api_connected,
    platform_api_online,
    mode,
    max_parallel_trades,
    max_trades_day,
    max_profit_day,
    max_loss_day,
    initial_capital,
    max_investment_per_trade
)
SELECT
    u.id,
    FALSE,
    FALSE,
    FALSE,
    TRUE,
    TRUE,
    'PAPER',
    3,
    4,
    5000,
    2000,
    100000,
    50000
FROM s004_users u
WHERE u.username IN ('admin', 'trader1')
ON CONFLICT (user_id) DO UPDATE
SET mode = EXCLUDED.mode,
    max_parallel_trades = EXCLUDED.max_parallel_trades,
    max_trades_day = EXCLUDED.max_trades_day,
    max_profit_day = EXCLUDED.max_profit_day,
    max_loss_day = EXCLUDED.max_loss_day,
    initial_capital = EXCLUDED.initial_capital,
    max_investment_per_trade = EXCLUDED.max_investment_per_trade,
    updated_at = NOW();

INSERT INTO s004_user_strategy_settings (
    user_id,
    strategy_id,
    strategy_version,
    lots,
    lot_size,
    max_strike_distance_atm,
    max_premium,
    min_premium,
    min_entry_strength_pct,
    sl_type,
    sl_points,
    breakeven_trigger_pct,
    target_points,
    trailing_sl_points,
    timeframe,
    trade_start,
    trade_end,
    enabled_indices,
    auto_pause_after_losses
)
SELECT
    u.id,
    'strat-trendsnap-momentum',
    '1.0.0',
    1,
    65,
    5,
    200,
    30,
    0,
    'Fixed Points',
    15,
    50,
    10,
    20,
    '3-min',
    '09:20',
    '15:00',
    ARRAY['NIFTY'],
    3
FROM s004_users u
WHERE u.username IN ('admin', 'trader1')
ON CONFLICT (user_id, strategy_id, strategy_version) DO UPDATE
SET lots = EXCLUDED.lots,
    lot_size = EXCLUDED.lot_size,
    max_strike_distance_atm = EXCLUDED.max_strike_distance_atm,
    max_premium = EXCLUDED.max_premium,
    min_premium = EXCLUDED.min_premium,
    min_entry_strength_pct = EXCLUDED.min_entry_strength_pct,
    sl_type = EXCLUDED.sl_type,
    sl_points = EXCLUDED.sl_points,
    breakeven_trigger_pct = EXCLUDED.breakeven_trigger_pct,
    target_points = EXCLUDED.target_points,
    trailing_sl_points = EXCLUDED.trailing_sl_points,
    timeframe = EXCLUDED.timeframe,
    trade_start = EXCLUDED.trade_start,
    trade_end = EXCLUDED.trade_end,
    enabled_indices = EXCLUDED.enabled_indices,
    auto_pause_after_losses = EXCLUDED.auto_pause_after_losses,
    updated_at = NOW();
