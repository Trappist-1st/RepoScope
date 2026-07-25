-- RepoScope schema (phases 1 + 5)

CREATE TABLE IF NOT EXISTS repos (
    repo_id      TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    commit_hash  TEXT NOT NULL DEFAULT '',
    local_path   TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS files (
    repo_id          TEXT NOT NULL REFERENCES repos(repo_id) ON DELETE CASCADE,
    file_path        TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    last_indexed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repo_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,
    repo_id         TEXT NOT NULL,
    question        TEXT NOT NULL,
    intent          TEXT,
    node_timings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    result          JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_passed   BOOLEAN,
    low_confidence  BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'ok',
    warnings        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_repo ON agent_runs(repo_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at DESC);
