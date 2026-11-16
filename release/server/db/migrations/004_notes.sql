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
