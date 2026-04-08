-- Multi-broker: encrypted vault per user, single platform shared slot, audit log.
-- Apply after functional_core_schema. Requires S004_CREDENTIALS_FERNET_KEY for FYERS / vault (Zerodha can still use legacy JSON only).

ALTER TABLE s004_user_master_settings
  ADD COLUMN IF NOT EXISTS active_broker_code VARCHAR(32),
  ADD COLUMN IF NOT EXISTS broker_vault_cipher TEXT;

CREATE TABLE IF NOT EXISTS s004_platform_broker_shared (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  broker_code VARCHAR(32) NOT NULL DEFAULT 'zerodha',
  vault_cipher TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by_user_id BIGINT REFERENCES s004_users(id)
);

INSERT INTO s004_platform_broker_shared (id, broker_code) VALUES (1, 'zerodha')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS s004_broker_audit_log (
  id BIGSERIAL PRIMARY KEY,
  actor_user_id BIGINT NOT NULL REFERENCES s004_users(id),
  subject_user_id BIGINT REFERENCES s004_users(id),
  broker_code VARCHAR(32),
  action VARCHAR(48) NOT NULL,
  client_ip TEXT,
  meta JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_s004_broker_audit_actor ON s004_broker_audit_log (actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_s004_broker_audit_subject ON s004_broker_audit_log (subject_user_id, created_at DESC);
