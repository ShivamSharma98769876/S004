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
      "scoreThreshold": 3.0,
      "scoreMax": 5.0,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Weighted average of 9 heuristics. OI buildup, IVR, volume spike, RSI, EMA, VWAP, delta fit, OI change, LTP change. Signal when score >= 3.0."
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
