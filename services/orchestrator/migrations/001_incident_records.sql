CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    current_step TEXT NOT NULL,
    environment TEXT NOT NULL,
    question TEXT NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    remediation_id TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    operation TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_audit_events (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT REFERENCES incidents(incident_id),
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    principal TEXT NOT NULL,
    mode TEXT NOT NULL,
    arguments JSONB NOT NULL,
    outcome JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    configuration JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id),
    case_id TEXT NOT NULL,
    prediction JSONB NOT NULL,
    metrics JSONB NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd NUMERIC(12, 6),
    PRIMARY KEY (run_id, case_id)
);
