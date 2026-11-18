CREATE TABLE IF NOT EXISTS lease_claims (
    id BIGSERIAL PRIMARY KEY,
    license_id TEXT NOT NULL DEFAULT '',
    customer_name TEXT NOT NULL DEFAULT '',
    machine_id TEXT NOT NULL DEFAULT '',
    ok BOOLEAN NOT NULL DEFAULT FALSE,
    detail TEXT NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    client_ip TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lease_claims_created_at_idx ON lease_claims (created_at DESC);
