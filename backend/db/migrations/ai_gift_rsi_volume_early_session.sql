-- AI Gift v1.0.0: RSI floor ≥50 with relaxed upper band; optional volumeSpike ref; early-session strike volume until 10:30 IST.
UPDATE s004_strategy_catalog
SET strategy_details_json =
  jsonb_set(
    jsonb_set(
      jsonb_set(
        COALESCE(strategy_details_json, '{}'::jsonb),
        '{indicators,rsi}',
        COALESCE(strategy_details_json->'indicators'->'rsi', '{}'::jsonb)
          || '{"period": 14, "min": 50, "max": 100, "description": "RSI at or above 50 scores well in the composite model; upper band relaxed vs legacy 75 cap."}'::jsonb,
        true
      ),
      '{indicators,volumeSpike}',
      COALESCE(strategy_details_json->'indicators'->'volumeSpike', '{}'::jsonb)
        || '{"minRatio": 1.0, "description": "Chain indicator reference; aligns with relaxed intraday volume context."}'::jsonb,
      true
    ),
    '{strikeSelection}',
    COALESCE(strategy_details_json->'strikeSelection', '{}'::jsonb)
      || '{"minVolumeEarlySession": 180, "earlySessionEndHourIST": 10, "earlySessionEndMinuteIST": 30}'::jsonb,
    true
  ),
  updated_at = NOW()
WHERE strategy_id = 'strat-ai-gift' AND version = '1.0.0';
