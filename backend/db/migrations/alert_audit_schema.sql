-- W07 alert delivery and escalation audit tables

CREATE TABLE IF NOT EXISTS s004_alert_events (
    id BIGSERIAL PRIMARY KEY,
    alert_id VARCHAR(64) NOT NULL UNIQUE,
    severity VARCHAR(16) NOT NULL,
    category VARCHAR(40) NOT NULL,
    message TEXT NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS s004_alert_deliveries (
    id BIGSERIAL PRIMARY KEY,
    alert_id VARCHAR(64) NOT NULL,
    channel VARCHAR(20) NOT NULL,
    attempt_no INTEGER NOT NULL,
    success BOOLEAN NOT NULL,
    reason TEXT,
    delivered_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_alert_deliveries_lookup
ON s004_alert_deliveries (alert_id, channel, attempt_no);

