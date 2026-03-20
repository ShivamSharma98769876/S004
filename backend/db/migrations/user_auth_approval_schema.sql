-- User auth and admin approval schema
-- Adds email/password login and admin approval for Paper/Live trade types

-- Add new columns to s004_users (nullable for backward compatibility with existing users)
ALTER TABLE IF EXISTS s004_users
    ADD COLUMN IF NOT EXISTS email VARCHAR(255) UNIQUE,
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255),
    ADD COLUMN IF NOT EXISTS approved_paper BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS approved_live BOOLEAN NOT NULL DEFAULT FALSE;

-- Create index for email lookup
CREATE INDEX IF NOT EXISTS ix_s004_users_email ON s004_users (email) WHERE email IS NOT NULL;

-- Backfill: set approved_paper=true for existing admin (so admin can use app)
UPDATE s004_users SET approved_paper = TRUE, approved_live = TRUE WHERE role = 'ADMIN';
