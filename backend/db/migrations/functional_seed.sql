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
      "description": "Simple four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI 50-75, volume above 1.1x average. Signal when at least three of four factors pass. Exits use SL, target, and breakeven from Settings.",
      "includeEmaCrossoverInScore": false,
      "strictBullishComparisons": true,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Not counted in score; metadata only."},
        "ivr": {"bonus": 0, "maxThreshold": 20, "description": "IVR for reference on the chain; no score bonus."},
        "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI between 50 and 75 adds one point."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {"minRatio": 1.1, "description": "Volume strictly above 1.1x recent average adds one point."}
      },
      "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "description": "Liquidity: min OI 5k, min volume 300. Max 3 steps OTM. Prefer delta near 0.45 CE / -0.45 PE; rank by score and fit."
      },
      "scoreThreshold": 3,
      "scoreMax": 4,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Primary: latest option close must be above VWAP (otherwise no signal). Score 0-4: +1 VWAP pass, +1 EMA9 above EMA21, +1 RSI 50-75, +1 volume above 1.1x average. No crossover or IVR points. Eligible BUY CE/PE when score is at least 3."
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
    '1.1.0',
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
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "maxOptionPremiumInr": 80,
        "selectStrikeByMaxGamma": true,
        "maxStrikeRecommendations": 1,
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

-- NIFTY naked short premium: chain IVR + strike-leg regime (ema_cross_vwap); bullish -> short PE, bearish -> short CE
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
    'strat-trendpulse-z',
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
    'TrendPulse Z execution template'
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
    '09:15',
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
