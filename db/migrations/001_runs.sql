CREATE TABLE runs (
    id uuid PRIMARY KEY,
    run_type text NOT NULL,
    label text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    code_version text NOT NULL,
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    duration_ms double precision,
    error text,
    pipeline_metrics jsonb,
    duplicates jsonb NOT NULL DEFAULT '[]'::jsonb,
    exclusions jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE UNIQUE INDEX one_active_holdout ON runs (run_type)
WHERE run_type = 'holdout';

CREATE TABLE source_records (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN ('ledger', 'settlements', 'bank', 'tax_26as')),
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, source_type, ordinal)
);

CREATE TABLE reconciliation_results (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE llm_decisions (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE tax_findings (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE evaluation_metrics (
    run_id uuid PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    payload jsonb NOT NULL
);

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconciliation_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tax_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_metrics ENABLE ROW LEVEL SECURITY;
