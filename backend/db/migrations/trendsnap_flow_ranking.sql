-- TrendSnap Momentum: merge flow-based strike ranking (landing-aligned CE/PE tilt + OI/vol percentiles).
UPDATE s004_strategy_catalog
SET strategy_details_json = jsonb_set(
    COALESCE(strategy_details_json, '{}'::jsonb),
    '{strikeSelection}',
    COALESCE(strategy_details_json->'strikeSelection', '{}'::jsonb)
        || '{"flowRanking":{"enabled":true,"useChainFlowTilt":true,"tiltWeight":0.22,"percentileOiWeight":1.0,"percentileVolWeight":1.0,"oiChgScaleWeight":0.12,"longBuildupBonus":0.28,"shortCoveringBonus":0.24,"description":"After rule score, rank strikes using the same option-flow tilt as the landing page (CE vs PE), plus within-wing OI and volume percentiles, smoothed OI change, and bonuses for Long Buildup and Short Covering."}}'::jsonb,
    true
)
WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0';
