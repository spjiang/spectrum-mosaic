CREATE TABLE IF NOT EXISTS admins (
  id            BIGSERIAL PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
  id         BIGSERIAL PRIMARY KEY,
  name       TEXT NOT NULL,
  contact    TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'trial'
               CHECK (status IN ('trial', 'formal', 'disabled')),
  notes      TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS releases (
  id           BIGSERIAL PRIMARY KEY,
  version      TEXT NOT NULL,
  image_name   TEXT NOT NULL DEFAULT '',
  digest       TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'success', 'failed')),
  log_path     TEXT NOT NULL DEFAULT '',
  artifact_path TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS licenses (
  id              BIGSERIAL PRIMARY KEY,
  license_id      TEXT NOT NULL UNIQUE,
  customer_id     BIGINT NOT NULL REFERENCES customers(id),
  release_id      BIGINT NOT NULL REFERENCES releases(id),
  starts_at       TIMESTAMPTZ NOT NULL,
  expires_at      TIMESTAMPTZ NOT NULL,
  issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  file_path       TEXT NOT NULL DEFAULT '',
  file_checksum   TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'disabled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deliveries (
  id            BIGSERIAL PRIMARY KEY,
  customer_id   BIGINT NOT NULL REFERENCES customers(id),
  release_id    BIGINT NOT NULL REFERENCES releases(id),
  license_id    BIGINT NOT NULL REFERENCES licenses(id),
  package_path  TEXT NOT NULL DEFAULT '',
  checksum      TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS system_settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL DEFAULT '',
  label       TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  used_by     TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
  id         BIGSERIAL PRIMARY KEY,
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS notes_alive_idx ON notes (id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS notes_updated_idx ON notes (updated_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_logs (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT NOT NULL DEFAULT '',
  action      TEXT NOT NULL,
  object_type TEXT NOT NULL DEFAULT '',
  object_id   TEXT NOT NULL DEFAULT '',
  detail      TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS licenses_customer_idx ON licenses (customer_id);
CREATE INDEX IF NOT EXISTS deliveries_customer_idx ON deliveries (customer_id);
CREATE INDEX IF NOT EXISTS audit_logs_created_idx ON audit_logs (created_at DESC);
