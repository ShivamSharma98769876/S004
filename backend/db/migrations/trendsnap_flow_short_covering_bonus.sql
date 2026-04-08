-- Add shortCoveringBonus to TrendSnap flowRanking when missing (idempotent).
UPDATE s004_strategy_catalog
SET strategy_details_json = jsonb_set(
    strategy_details_json,
    '{strikeSelection,flowRanking,shortCoveringBonus}',
    '0.24'::jsonb,
    true
)
WHERE strategy_id = 'strat-trendsnap-momentum'
  AND version = '1.0.0'
  AND strategy_details_json #> '{strikeSelection,flowRanking}' IS NOT NULL
  AND NOT (
    COALESCE(strategy_details_json->'strikeSelection'->'flowRanking', '{}'::jsonb) ? 'shortCoveringBonus'
  );
