-- Singleton platform risk controls (global trading pause / kill switch).
-- Apply after functional_core_schema; safe to run multiple times.

CREATE TABLE IF NOT EXISTS s004_platform_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1,
    CONSTRAINT s004_platform_settings_single_row CHECK (id = 1),
    trading_paused BOOLEAN NOT NULL DEFAULT FALSE,
    pause_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT REFERENCES s004_users(id)
);

INSERT INTO s004_platform_settings (id, trading_paused) VALUES (1, FALSE)
ON CONFLICT (id) DO NOTHING;
