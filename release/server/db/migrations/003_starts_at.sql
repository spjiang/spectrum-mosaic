ALTER TABLE licenses ADD COLUMN IF NOT EXISTS starts_at TIMESTAMPTZ;
UPDATE licenses SET starts_at = issued_at WHERE starts_at IS NULL;
ALTER TABLE licenses ALTER COLUMN starts_at SET NOT NULL;
