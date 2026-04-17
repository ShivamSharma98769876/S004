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
      "description": "Four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI ≥ 50 (no 75 cap), volume above 1.0x average. RSI must be in band for eligibility (not score-only). NIFTY spot trend must align: bullish for CE, bearish for PE. Strike choice ranks eligible legs by score then by option flow (aligned with landing CE/PE tilt), OI/volume depth, OI change, and Long Buildup. Early session uses relaxed min contract volume until 10:30 IST. Exits use SL, target, and breakeven from Settings.",
      "requireRsiForEligible": true,
      "longPremiumSpotAlign": true,
      "includeEmaCrossoverInScore": false,
      "strictBullishComparisons": true,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Not counted in score; metadata only."},
        "ivr": {"bonus": 0, "maxThreshold": 20, "description": "IVR for reference on the chain; no score bonus."},
        "rsi": {"period": 14, "min": 50, "max": 100, "description": "RSI at or above 50 adds one point (upper band relaxed)."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {"minRatio": 1.0, "description": "Volume strictly above 1.0x recent average adds one point."}
      },
      "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "minVolumeEarlySession": 120,
        "earlySessionEndHourIST": 10,
        "earlySessionEndMinuteIST": 30,
        "maxStrikeRecommendations": 2,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "flowRanking": {
          "enabled": true,
          "useChainFlowTilt": true,
          "tiltWeight": 0.22,
          "percentileOiWeight": 1.0,
          "percentileVolWeight": 1.0,
          "oiChgScaleWeight": 0.12,
          "longBuildupBonus": 0.28,
          "shortCoveringBonus": 0.24,
          "pinPenaltyOnExpiryDay": true,
          "pinMaxDistanceFromSpot": 150,
          "pinOiDominanceRatio": 1.2,
          "pinPenaltyWeight": 0.18,
          "description": "After rule score, rank by flow tilt, OI/vol percentiles, OI change, Long Buildup / Short Covering. On expiry day (calendar DTE 0 IST only), subtract a soft rank penalty at the top-OI CE/PE strike if within pinMaxDistanceFromSpot of spot and OI dominance vs second strike passes pinOiDominanceRatio — no hard ban."
        },
        "description": "Liquidity: min OI 5k, min volume 300 (120 until 10:00 IST). Max 2 eligible strikes per refresh. Max 3 steps OTM. Prefer delta near 0.45 CE / -0.45 PE; rank by score, then flow ranking (OI/vol/ΔOI + landing flow tilt)."
      },
      "scoreThreshold": 3,
      "scoreMax": 4,
      "autoTradeScoreThreshold": 4,
      "scoreDescription": "Primary: latest option close must be above VWAP (otherwise no signal). Score 0-4: +1 VWAP pass, +1 EMA9 above EMA21, +1 RSI ≥ 50, +1 volume above 1.0x average. No crossover or IVR points. Eligible BUY when score >= 3 AND RSI ≥ 50 AND NIFTY spot regime matches leg (bullish/CE, bearish/PE). Among ties, strikes rank by flow (landing-style CE/PE tilt, OI/vol percentiles, OI change, Long Buildup). Auto-execute still requires autoTradeScoreThreshold."
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
    '1.2.0',
    'Nifty IVR Trend Short',
    'NIFTY naked short premium: symmetric CE/PE (EMA9 cross below EMA21 + LTP<VWAP on leg), chain IVR 45–100, leg RSI <80 and falling vs prior bar, VIX delta bands. Min OI 3k / volume 200 per strike. High risk; margin required.',
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
      "description": "NIFTY short premium. Per-leg regime on option LTP: fresh EMA9 cross below EMA21 and premium-weakness context with relaxed VWAP eligibility buffer. VIX→delta via widened shortPremiumDeltaVixBands. Leg RSI uses shortPremiumRsiBelow without mandatory falling-vs-prior bar. Per-strike chain IVR in [ivr.minThreshold, maxLegThreshold]. Strike liquidity: minOi 3000, minVolume 200.",
      "spotRegimeMode": "ema_cross_vwap",
      "spotRegimeSatisfiedScore": 5,
      "includeVolumeInLegScore": false,
      "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Fresh cross within this many candles on the leg LTP series."},
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
        "shortPremiumVwapEligibleBufferPct": 0.75,
        "shortPremiumEmaEligibleBufferPct": 0.3,
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
        "shortPremiumRsiBelow": 85,
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
  AND NOT EXISTS (
    SELECT 1 FROM s004_user_master_settings m WHERE m.user_id = u.id
  );
-- Seed only missing master rows; never touch existing (re-seed must not reset daily caps / limits).

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
  AND NOT EXISTS (
    SELECT 1
    FROM s004_user_strategy_settings s
    WHERE s.user_id = u.id
      AND s.strategy_id = 'strat-trendsnap-momentum'
      AND s.strategy_version = '1.0.0'
  );
-- Seed only missing strategy rows for TrendSnap 1.0.0; never overwrite saved SL/target/window.
