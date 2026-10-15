CREATE TABLE IF NOT EXISTS operation_inspection_audits (
    audit_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request JSONB NOT NULL,
    outcome JSONB
);
CREATE INDEX IF NOT EXISTS operation_inspection_incident_idx
    ON operation_inspection_audits (incident_id, started_at DESC);
