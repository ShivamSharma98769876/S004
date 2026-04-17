-- SuperTrendTrail: NIFTY spot SuperTrend (10/3) + EMA pullback vs slow EMA; short ATM weekly (min 2 DTE).

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
    'strat-supertrend-trail',
    '1.0.0',
    'SuperTrendTrail',
    'NIFTY spot SuperTrend pullback: bullish ST needs fast EMA above slow and close below slow EMA (sell ATM PE); bearish ST needs fast below slow and close above slow EMA (sell ATM CE). Weekly min 2 DTE. SL/trailing SL/target per Settings. Exits on SuperTrend flip or session end (Settings).',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['NIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "supertrend-trail",
      "positionIntent": "short_premium",
      "displayName": "SuperTrendTrail",
      "description": "Pullback entries on NIFTY spot: SuperTrend (ATR 10 x 3) with EMA10/EMA20 vs slow EMA. Bullish: ST bullish, fast above slow, latest close below slow EMA (sell ATM PE). Bearish: ST bearish, fast below slow, latest close above slow EMA (sell ATM CE). Weekly expiry with min 2 calendar DTE. SL, trailing SL, and target follow user Settings unless explicitly overridden here. Exits: SuperTrend flip on spot, or after trade_end (Settings).",
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Binary signal from spot pullback model; recommendation uses max score when eligible.",
      "superTrendTrail": {
        "emaFast": 10,
        "emaSlow": 20,
        "atrPeriod": 10,
        "atrMultiplier": 3,
        "candleDaysBack": 5,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "maxConsecutiveClosesInZone": 1,
        "vwapStepThresholdPct": 0.05,
        "entryVsVwapEpsPct": 0.02
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 1,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "description": "ATM only (0 steps); weekly expiry. Engine does not gate ATM leg on OI/volume/IVR (spot signal + LTP>0 only)."
      },
      "indicators": {
        "ivr": {"maxThreshold": 20, "description": "Reference only for SuperTrendTrail; ATM short leg is not blocked on IVR in the recommendation engine."}
      }
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
