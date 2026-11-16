ALTER TABLE customers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE releases ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE licenses ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE releases DROP CONSTRAINT IF EXISTS releases_version_key;
CREATE UNIQUE INDEX IF NOT EXISTS releases_version_alive_uidx
  ON releases (version) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS customers_alive_idx ON customers (id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS licenses_alive_idx ON licenses (id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS deliveries_alive_idx ON deliveries (id) WHERE deleted_at IS NULL;
