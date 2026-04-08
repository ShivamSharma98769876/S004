-- Nifty IVR Trend Short 1.2.0: fewer marginal entries (goal: fewer SLs).
-- Idempotent. Safe after nifty_ivr_trend_short_relax.sql (reinforces 1.2.0 if needed).
-- - strikeSelection.minOi 3000, minVolume 200
-- - indicators.ivr.minThreshold 45 (>30; stricter than legacy 40)
-- - autoTradeScoreThreshold unchanged (4)

UPDATE s004_strategy_catalog
SET
  strategy_details_json = jsonb_set(
    jsonb_set(
      jsonb_set(
        strategy_details_json,
        '{indicators,ivr,minThreshold}',
        '45'::jsonb,
        true
      ),
      '{strikeSelection,minOi}',
      '3000'::jsonb,
      true
    ),
    '{strikeSelection,minVolume}',
    '200'::jsonb,
    true
  ),
  description = 'NIFTY naked short premium: symmetric CE/PE (EMA9 cross below EMA21 + LTP<VWAP on leg), chain IVR 45–100, leg RSI <80 and falling vs prior bar, VIX delta bands. Min OI 3k / volume 200 per strike. High risk; margin required.',
  updated_at = NOW()
WHERE strategy_id = 'strat-nifty-ivr-trend-short'
  AND version = '1.2.0';
