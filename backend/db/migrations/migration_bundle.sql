-- W09-S01 Final schema/migration bundle for PRD v2.5
-- Consolidates missing core tables for analytics, ranking, recommendations,
-- live trades, alerts, and strategy marketplace.

-- Include W03 / W04 / W05 / W06 / W07 / W08 tables if not present.

\ir analytics_schema.sql
\ir ranking_schema.sql
\ir recommendation_schema.sql
\ir execution_schema.sql
\ir alert_audit_schema.sql
\ir strategy_catalog_schema.sql
\ir functional_core_schema.sql
\ir user_auth_approval_schema.sql

-- Optional version marker table
CREATE TABLE IF NOT EXISTS s004_schema_versions (
    id BIGSERIAL PRIMARY KEY,
    version_tag VARCHAR(64) NOT NULL UNIQUE,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),
    notes TEXT
);

INSERT INTO s004_schema_versions (version_tag, notes)
VALUES ('prd-v2.5-final-bundle', 'Consolidated schema bundle for W09')
ON CONFLICT (version_tag) DO NOTHING;

INSERT INTO s004_schema_versions (version_tag, notes)
VALUES ('functional-core-v1', 'Admin strategy + settings + analytics + manual execution + dashboard schema')
ON CONFLICT (version_tag) DO NOTHING;

