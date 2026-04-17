-- Nifty IVR Trend Short: wider entry tolerance for regime + three_factor leg checks.
-- Applies to catalog 1.1.0 and 1.2.0. Idempotent UPDATE.
--
-- Rationale (typical rejections):
--   - LTP slightly above leg VWAP even with 0.30% buffer
--   - EMA9 barely above EMA21 (rounded / micro structure)
--   - Regime cross "too old" vs maxCandlesSinceCross
--   - Leg RSI just at/above shortPremiumRsiBelow (three_factor: rsi_ok = rsi < threshold)
--
-- Values are conservative vs 0.30/0/8: VWAP +0.75%, EMA +0.30%, cross window 10, RSI cap 85.

UPDATE s004_strategy_catalog
SET
  strategy_details_json = jsonb_set(
    jsonb_set(
      jsonb_set(
        jsonb_set(
          strategy_details_json,
          '{indicators,emaCrossover,maxCandlesSinceCross}',
          '10'::jsonb,
          true
        ),
        '{strikeSelection,shortPremiumVwapEligibleBufferPct}',
        '0.75'::jsonb,
        true
      ),
      '{strikeSelection,shortPremiumEmaEligibleBufferPct}',
      '0.30'::jsonb,
      true
    ),
    '{strikeSelection,shortPremiumRsiBelow}',
    '85'::jsonb,
    true
  ),
  updated_at = NOW()
WHERE strategy_id = 'strat-nifty-ivr-trend-short'
  AND version IN ('1.1.0', '1.2.0');
