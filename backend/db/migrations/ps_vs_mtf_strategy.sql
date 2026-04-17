-- PS/VS MTF: Bank Nifty 3m RSI stack + 15m resampled filters; long ATM (see docs/strategies/PS_VS_15M_3M_MASTER_SPEC.md).

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
    'strat-ps-vs-mtf',
    '1.0.0',
    'PS/VS MTF (3m + 15m)',
    'Bank Nifty: 3m PS vs VS timing with 15m permission (resampled from 3m), ATR/ADX/volume/RSI band, conviction gate. Single 3m index fetch per refresh; higher TFs derived in-memory. ATM long premium default.',
    'MEDIUM',
    'ADMIN',
    'PUBLISHED',
    ARRAY['PAPER', 'LIVE'],
    ARRAY['BANKNIFTY'],
    '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
    '{
      "strategyType": "ps-vs-mtf",
      "positionIntent": "long_premium",
      "displayName": "PS/VS MTF (3m + 15m)",
      "description": "3m PS/VS cross or one-bar dip recovery with 15m bias and filters (ATR range, ADX, volume vs prior 15m, RSI 40–70). Conviction weighted score. Chart data: 3minute Kite interval only; 15m derived in process.",
      "scoreThreshold": 3,
      "scoreMax": 5,
      "autoTradeScoreThreshold": 3.5,
      "scoreDescription": "Signal when model ok and conviction >= min; recommendation uses max score when eligible.",
      "psVsMtf": {
        "rsiPeriod": 9,
        "psEmaPeriod": 9,
        "vsWmaPeriod": 21,
        "atrPeriod": 14,
        "adxPeriod": 14,
        "adxMin": 10,
        "adxRef": 30,
        "atrRangeMin": 0.5,
        "atrRangeMax": 2.5,
        "rsiBandLow": 40,
        "rsiBandHigh": 70,
        "minConvictionPct": 80,
        "volumeVsPriorMult": 1.10,
        "strict15m": true,
        "candleDaysBack": 8,
        "sessionStart": "09:15",
        "sessionEnd": "15:15"
      },
      "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 0,
        "description": "ATM; nearest listed expiry via pick_primary_expiry_str."
      },
      "indicators": {
        "ivr": {"maxThreshold": 20, "description": "Per-leg IVR cap."}
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
