-- TrendSnap v1.0.0: disable volume as a hard leg-score eligibility gate.
UPDATE s004_strategy_catalog
SET strategy_details_json = jsonb_set(
        COALESCE(strategy_details_json, '{}'::jsonb),
        '{includeVolumeInLegScore}',
        'false'::jsonb,
        true
    ),
    updated_at = NOW()
WHERE strategy_id = 'strat-trendsnap-momentum'
  AND version = '1.0.0'
  AND (strategy_details_json->>'includeVolumeInLegScore') IS DISTINCT FROM 'false';

UPDATE s004_user_strategy_settings
SET strategy_details_json = jsonb_set(
        COALESCE(strategy_details_json, '{}'::jsonb),
        '{includeVolumeInLegScore}',
        'false'::jsonb,
        true
    ),
    updated_at = NOW()
WHERE strategy_id = 'strat-trendsnap-momentum'
  AND strategy_version = '1.0.0'
  AND (strategy_details_json->>'includeVolumeInLegScore') IS DISTINCT FROM 'false';
