-- AI Gift strategy catalog + execution template

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
    'strat-ai-gift',
    '1.0.0',
    'AI Gift',
    'Adaptive option-chain strategy aligned with live market context for long/short strike selection.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "heuristic-voting",
      "displayName": "AI Gift",
      "description": "Adaptive weighted option-chain model tuned for intraday NIFTY. Uses OI buildup, IVR, volume spike, RSI (≥50, relaxed upper band), EMA alignment, VWAP structure, delta fit, OI change, and LTP change. Early session uses relaxed min contract volume until 10:30 IST. Enhanced by directional context, moneyness and DTE guardrails. Emits strongest strike candidates for Dashboard SIGNALS; execution follows your Paper/Live and risk settings.",
      "indicators": {
        "rsi": {"period": 14, "min": 50, "max": 100, "description": "RSI at or above 50 scores well; upper band relaxed vs legacy 75 cap."},
        "volumeSpike": {"minRatio": 1.0, "description": "Chain indicator reference for volume context."}
      },
      "heuristics": {
        "oiBuildup": {"enabled": true, "weight": 1.25},
        "ivr": {"enabled": true, "weight": 1.05},
        "volumeSpike": {"enabled": true, "weight": 1.05},
        "rsi": {"enabled": true, "weight": 0.85},
        "emaAlignment": {"enabled": true, "weight": 0.95},
        "primaryVwap": {"enabled": true, "weight": 1.10},
        "deltaFit": {"enabled": true, "weight": 0.90},
        "oiChange": {"enabled": true, "weight": 0.80},
        "ltpChange": {"enabled": true, "weight": 0.75}
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 400,
        "minVolumeEarlySession": 180,
        "earlySessionEndHourIST": 10,
        "earlySessionEndMinuteIST": 30,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Liquidity-first candidate pool. Strike radius +/-3 around ATM. Min volume 400 (180 until 10:30 IST). Prefer deltas near 0.35 (CE) / -0.35 (PE)."
      },
      "heuristicEnhancements": {
        "enabled": true,
        "maxMoneynessPct": 1.0,
        "moneynessOverrideMinScore": 4.6,
        "flatSpotBandPct": 0.08,
        "flatOiPct": 0.5,
        "volumeHighRatio": 1.5,
        "oiChurnAbsPct": 0.35,
        "churnScoreMultiplier": 0.94,
        "ltpStrongPct": 2.0,
        "oiWeightWhenLtpStrong": 0.45,
        "maxLtpOiCombinedWeightShare": 0.88,
        "jointMinMult": 0.72,
        "jointMaxMult": 1.10,
        "bestPerSideMinGap": 0.30,
        "singleDirectionOnly": false,
        "singleDirectionMinSpread": 0.40,
        "ceRequiresSpotNotDown": false,
        "peRequiresSpotNotUp": false,
        "directionalGateFlatBandPct": 0.05,
        "dynamicActionIntentEnabled": true,
        "dynamicLongTrendMinSpotChgPct": 0.35,
        "dynamicLongIvrMax": 55.0,
        "dynamicShortIvrMin": 65.0
      },
      "scoreThreshold": 3.2,
      "scoreMax": 5.0,
      "autoTradeScoreThreshold": 3.8,
      "scoreDescription": "Composite weighted score on chain micro-structure + directional context. RSI heuristic uses ≥50 with relaxed upper band. Eligible when enhanced score >= 3.2; auto-execution threshold 3.8 (subject to confidence and runtime gates)."
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
    'strat-ai-gift',
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
    'AI Gift execution template'
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
    'strat-ai-gift',
    '1.0.0',
    'PAPER',
    'ACTIVE'
FROM s004_users u
WHERE u.role = 'ADMIN'
ON CONFLICT (user_id, strategy_id, strategy_version) DO UPDATE
SET status = s004_strategy_subscriptions.status,
    mode = s004_strategy_subscriptions.mode,
    updated_at = s004_strategy_subscriptions.updated_at;

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
    auto_pause_after_losses,
    updated_at
)
SELECT
    u.id,
    'strat-ai-gift',
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
    3,
    NOW() - INTERVAL '30 days'
FROM s004_users u
WHERE u.role = 'ADMIN'
ON CONFLICT (user_id, strategy_id, strategy_version) DO NOTHING;
