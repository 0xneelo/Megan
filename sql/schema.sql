-- =============================================================================
-- Megan — Postgres schema
--
-- Postgres is the SOURCE OF TRUTH. Linear and Obsidian are mirrors. A Telegram
-- ban or a sync hiccup must never lose data, so every ingested item lands here
-- first (before any LLM / downstream call) and dedup is content-hash based so
-- re-running an export or re-reading Saved Messages never creates duplicates.
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Everything ingested lands here first, before processing.
CREATE TABLE IF NOT EXISTS inbox (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          TEXT        NOT NULL,            -- dm | forward | saved_sweep | upload
    raw_type        TEXT        NOT NULL,            -- text | voice | image | link | file
    raw_ref         TEXT,                            -- telegram msg id / file path
    content_hash    TEXT        NOT NULL,            -- dedup key
    extracted_text  TEXT,                            -- after transcription / OCR / fetch
    meta            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    classify_type   TEXT,                            -- task | note | read_later | question | ambiguous
    status          TEXT        NOT NULL DEFAULT 'pending',  -- pending | asking | routed | dropped
    routed_to       TEXT,                            -- linear:LIN-432 | obsidian:path | readlater:id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    CONSTRAINT inbox_content_hash_uniq UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS inbox_status_idx       ON inbox (status);
CREATE INDEX IF NOT EXISTS inbox_classify_idx     ON inbox (classify_type);
CREATE INDEX IF NOT EXISTS inbox_created_idx      ON inbox (created_at);

-- Enforces the "<=4 open asks" rule. One row per outstanding question.
CREATE TABLE IF NOT EXISTS open_asks (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inbox_id          BIGINT      REFERENCES inbox (id) ON DELETE CASCADE,
    kind              TEXT        NOT NULL DEFAULT 'triage',  -- triage | reminder | drip | brief
    question          TEXT        NOT NULL,
    suggested_answers JSONB       NOT NULL DEFAULT '[]'::jsonb,
    state             JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- partial triage answers gathered so far
    asked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS open_asks_unanswered_idx ON open_asks (answered_at) WHERE answered_at IS NULL;

-- Agent-monitor registry. Production hosts are simply never inserted here.
CREATE TABLE IF NOT EXISTS hosts (
    name              TEXT        PRIMARY KEY,
    ssh_alias         TEXT        NOT NULL,          -- user@host or ~/.ssh/config alias
    allowed_commands  JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- read-only allowlist
    is_production     BOOLEAN     NOT NULL DEFAULT false,        -- must always be false; enforced in code too
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT hosts_never_production CHECK (is_production = false)
);

-- So Claude learns the owner's routing patterns over time (Phase 6).
CREATE TABLE IF NOT EXISTS routing_memory (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_summary  TEXT        NOT NULL,
    decision      TEXT        NOT NULL,             -- create_linear_task | create_obsidian_note | ...
    project       TEXT,
    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS routing_memory_created_idx ON routing_memory (created_at);

-- Read-later queue (mirror; Postgres is source of truth).
CREATE TABLE IF NOT EXISTS read_later (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inbox_id      BIGINT      REFERENCES inbox (id) ON DELETE SET NULL,
    url           TEXT,
    title         TEXT,
    note          TEXT,
    topic         TEXT,
    digested      BOOLEAN     NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lightweight key/value for scheduler bookkeeping, cost tracking, last-sweep ids.
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only Anthropic usage log for the monthly cost cap.
CREATE TABLE IF NOT EXISTS llm_usage (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model          TEXT        NOT NULL,
    purpose        TEXT        NOT NULL,            -- classify | triage | vision | summary | monitor
    input_tokens   INTEGER     NOT NULL DEFAULT 0,
    output_tokens  INTEGER     NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(10,5) NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_usage_created_idx ON llm_usage (created_at);
