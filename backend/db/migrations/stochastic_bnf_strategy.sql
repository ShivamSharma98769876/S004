-- StochasticBNF: Bank Nifty spot EMA5/15/50 + ADX + Stochastic RSI; short ATM; 2 trading-DTE Tuesday expiry.

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
    'strat-stochastic-bnf',
    '1.0.0',
    'StochasticBNF',
    'Bank Nifty spot: EMA 5/15/50 trend, ADX strength, Stochastic RSI timing; sells ATM puts (bull) or calls (bear) on the monthly cycle with exactly 2 trading days to expiry (Tuesday-preferred). Session VWAP and optional time window. SL when EMA5 crosses EMA15; optional square-off at 3:15 PM IST.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['BANKNIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "stochastic-bnf",
      "positionIntent": "short_premium",
      "displayName": "StochasticBNF",
      "description": "Bank Nifty spot trend + momentum: pullback or immediate entry per config; Stoch RSI 14/14/3/3 with 70/30 zones; ADX > threshold. Chart interval follows Settings timeframe. Short ATM with 2 trading-DTE (monthly Tuesday series). VWAP filter on spot; optional 9:30–2:30 IST entry window. Exits: EMA5 vs EMA15 structure SL on bar close; optional 3:15 PM IST exit.",
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Binary signal from spot model; recommendation uses max score when eligible.",
      "stochasticBnf": {
        "adxPeriod": 14,
        "adxThreshold": 20,
        "rsiLength": 14,
        "stochLength": 14,
        "stochK": 3,
        "stochD": 3,
        "overbought": 70,
        "oversold": 30,
        "candleDaysBack": 8,
        "usePullbackEntry": false,
        "stochConfirmation": true,
        "vwapFilter": true,
        "timeFilter": false,
        "timeFilterStart": "09:30",
        "timeFilterEnd": "14:30",
        "exitTimeIst": "15:15"
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 0,
        "description": "ATM only (0 steps); 2 trading-DTE monthly Tuesday expiry; Bank Nifty strike step 100."
      },
      "indicators": {
        "ivr": {"maxThreshold": 20, "description": "Per-leg IVR cap for short premium."}
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
