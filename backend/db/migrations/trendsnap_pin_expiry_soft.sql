-- TrendSnap: soft rank penalty at OI wall on expiry day only (DTE 0 IST); merge into flowRanking.
UPDATE s004_strategy_catalog
SET strategy_details_json = jsonb_set(
    strategy_details_json,
    '{strikeSelection,flowRanking}',
    COALESCE(strategy_details_json->'strikeSelection'->'flowRanking', '{}'::jsonb)
        || '{"pinPenaltyOnExpiryDay":true,"pinMaxDistanceFromSpot":150,"pinOiDominanceRatio":1.2,"pinPenaltyWeight":0.18}'::jsonb,
    true
)
WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0';
