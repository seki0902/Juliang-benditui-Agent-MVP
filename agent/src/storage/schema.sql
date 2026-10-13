-- Local Growth Agent business schema. LangGraph checkpoint tables are managed separately.

CREATE TABLE IF NOT EXISTS daily_runs (
    run_id TEXT PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    account_id TEXT,
    data_date DATE NOT NULL,
    pipeline_version TEXT NOT NULL,
    status TEXT NOT NULL,
    data_completeness TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error_summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS raw_metrics (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    source TEXT NOT NULL,
    source_file TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    data_date DATE,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_daily_metrics (
    account_id TEXT NOT NULL,
    data_date DATE NOT NULL,
    metric_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    spend NUMERIC(18, 4) NOT NULL DEFAULT 0,
    impressions BIGINT NOT NULL DEFAULT 0,
    clicks BIGINT NOT NULL DEFAULT 0,
    leads BIGINT NOT NULL DEFAULT 0,
    valid_leads BIGINT,
    private_messages BIGINT NOT NULL DEFAULT 0,
    contacted_leads BIGINT,
    data_completeness TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, data_date, metric_version)
);

CREATE TABLE IF NOT EXISTS unit_daily_metrics (
    account_id TEXT NOT NULL,
    project_id TEXT,
    unit_id TEXT NOT NULL,
    data_date DATE NOT NULL,
    metric_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    unit_name TEXT,
    plan_type TEXT,
    lifecycle_stage TEXT,
    observed_status TEXT,
    actual_status TEXT,
    budget NUMERIC(18, 4),
    bid NUMERIC(18, 4),
    spend NUMERIC(18, 4) NOT NULL DEFAULT 0,
    impressions BIGINT NOT NULL DEFAULT 0,
    clicks BIGINT NOT NULL DEFAULT 0,
    leads BIGINT NOT NULL DEFAULT 0,
    valid_leads BIGINT,
    private_messages BIGINT NOT NULL DEFAULT 0,
    contacted_leads BIGINT,
    data_completeness TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, unit_id, data_date, metric_version)
);

CREATE TABLE IF NOT EXISTS material_daily_metrics (
    account_id TEXT NOT NULL,
    material_id TEXT NOT NULL,
    data_date DATE NOT NULL,
    metric_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    material_name TEXT,
    spend NUMERIC(18, 4) NOT NULL DEFAULT 0,
    impressions BIGINT NOT NULL DEFAULT 0,
    clicks BIGINT NOT NULL DEFAULT 0,
    leads BIGINT NOT NULL DEFAULT 0,
    valid_leads BIGINT,
    private_messages BIGINT NOT NULL DEFAULT 0,
    contacted_leads BIGINT,
    data_completeness TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, material_id, data_date, metric_version)
);

CREATE TABLE IF NOT EXISTS lead_feedback (
    lead_id TEXT PRIMARY KEY,
    lead_date DATE NOT NULL,
    account_id TEXT,
    project_id TEXT,
    unit_id TEXT,
    material_id TEXT,
    is_valid BOOLEAN,
    is_contacted BOOLEAN,
    screened_at TIMESTAMPTZ,
    contacted_at TIMESTAMPTZ,
    feedback_updated_at TIMESTAMPTZ,
    invalid_reason TEXT,
    source TEXT NOT NULL DEFAULT 'shimo'
);

CREATE TABLE IF NOT EXISTS plan_material_relation (
    account_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    material_id TEXT NOT NULL,
    relation_start_date DATE NOT NULL,
    relation_end_date DATE,
    source TEXT NOT NULL,
    confidence NUMERIC(5, 4),
    PRIMARY KEY (account_id, unit_id, material_id, relation_start_date)
);

CREATE TABLE IF NOT EXISTS plan_state_history (
    account_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    observed_status TEXT,
    actual_status TEXT,
    status_changed_at TIMESTAMPTZ,
    change_source TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (account_id, unit_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS baseline_snapshots (
    account_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    window_days INTEGER NOT NULL,
    metrics JSONB NOT NULL,
    sample JSONB NOT NULL,
    PRIMARY KEY (account_id, object_type, object_id, as_of_date, window_days)
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    diagnosis_status TEXT NOT NULL,
    evidence JSONB NOT NULL,
    missing_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES daily_runs(run_id),
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    priority TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    evidence JSONB NOT NULL,
    confidence NUMERIC(5, 4),
    observation_window TEXT,
    requires_human_approval BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'generated',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recommendation_execution (
    execution_id BIGSERIAL PRIMARY KEY,
    recommendation_id TEXT NOT NULL REFERENCES recommendations(recommendation_id),
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actual_action TEXT,
    actual_value TEXT,
    operator TEXT,
    result_status TEXT NOT NULL,
    observation_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_raw_metrics_run ON raw_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_unit_daily_date ON unit_daily_metrics(account_id, data_date);
CREATE INDEX IF NOT EXISTS idx_material_daily_date ON material_daily_metrics(account_id, data_date);
CREATE INDEX IF NOT EXISTS idx_lead_feedback_date ON lead_feedback(lead_date);
