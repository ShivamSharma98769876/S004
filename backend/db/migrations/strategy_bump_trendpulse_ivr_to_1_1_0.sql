-- Bump catalog + runtime bindings from 1.0.0 → 1.1.0 for:
--   strat-trendpulse-z (TrendPulse Z Balanced)
--   strat-nifty-ivr-trend-short
--
-- Idempotent: safe to re-run (ON CONFLICT / NOT EXISTS guards).
-- Run: psql "%DATABASE_URL%" -f backend/db/migrations/strategy_bump_trendpulse_ivr_to_1_1_0.sql
--
-- After run: users should open Settings once and Save, or rely on subscription row already updated.

-- 1) Catalog: clone 1.0.0 rows to 1.1.0 (same JSON and metadata)
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
    c.strategy_id,
    '1.1.0',
    c.display_name,
    c.description,
    c.risk_profile,
    c.owner_type,
    c.publish_status,
    c.execution_modes,
    c.supported_segments,
    c.performance_snapshot,
    c.strategy_details_json,
    c.created_by
FROM s004_strategy_catalog c
WHERE c.strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND c.version = '1.0.0'
ON CONFLICT (strategy_id, version) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
    updated_at = NOW();

-- 2) Config templates for 1.1.0 (copy from same strategy 1.0.0 if present)
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    v.strategy_id,
    '1.1.0',
    v.config_version,
    v.config_json,
    v.active,
    v.changed_by,
    'Bump strategy version 1.0.0 → 1.1.0'
FROM s004_strategy_config_versions v
WHERE v.strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND v.strategy_version = '1.0.0'
  AND v.config_version = 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO UPDATE SET
    config_json = EXCLUDED.config_json,
    active = EXCLUDED.active,
    changed_reason = EXCLUDED.changed_reason;

-- 2a) Nifty IVR: if no 1.0.0 config row existed, insert default 1.1.0 template
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    'strat-nifty-ivr-trend-short',
    '1.1.0',
    1,
    '{
      "timeframe": "3-min",
      "min_entry_strength_pct": 0,
      "max_strike_distance_atm": 5,
      "target_points": 10,
      "sl_points": 15,
      "trailing_sl_points": 20
    }'::jsonb,
    TRUE,
    u.id,
    'Bump to 1.1.0 (default execution template)'
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING;

-- 2b) TrendPulse Z: if there was no 1.0.0 config row, seed 1.1.0 from NIFTY short template
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    'strat-trendpulse-z',
    '1.1.0',
    1,
    '{
      "timeframe": "3-min",
      "min_entry_strength_pct": 0,
      "max_strike_distance_atm": 5,
      "target_points": 10,
      "sl_points": 15,
      "trailing_sl_points": 20
    }'::jsonb,
    TRUE,
    u.id,
    'Bump to 1.1.0 (default execution template)'
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING;

-- 3) Subscriptions: point ACTIVE users at 1.1.0
UPDATE s004_strategy_subscriptions
SET strategy_version = '1.1.0', updated_at = NOW()
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0';

-- 4) Per-user strategy settings
UPDATE s004_user_strategy_settings
SET strategy_version = '1.1.0', updated_at = NOW()
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0';

-- 5) Drop stale generated recommendations (will be rebuilt on next refresh)
DELETE FROM s004_trade_recommendations
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0'
  AND status = 'GENERATED';

-- Optional: archive old catalog rows so Marketplace lists only 1.1.0
-- UPDATE s004_strategy_catalog SET publish_status = 'ARCHIVED', updated_at = NOW()
-- WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short') AND version = '1.0.0';
