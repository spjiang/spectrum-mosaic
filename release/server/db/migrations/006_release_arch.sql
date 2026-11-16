ALTER TABLE releases ADD COLUMN IF NOT EXISTS arch TEXT;
UPDATE releases SET arch = 'arm64' WHERE arch IS NULL OR arch = '';
ALTER TABLE releases ALTER COLUMN arch SET DEFAULT 'arm64';
ALTER TABLE releases ALTER COLUMN arch SET NOT NULL;

DROP INDEX IF EXISTS releases_version_alive_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS releases_version_arch_alive_uidx
  ON releases (version, arch) WHERE deleted_at IS NULL;
