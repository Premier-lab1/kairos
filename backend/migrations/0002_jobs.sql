-- Очередь задач в PostgreSQL (вместо Redis на этапе MVP): запросы во Frontrun и другие платные API.
-- Одна задача на (kind, key): повторная постановка той же задачи ничего не делает.

CREATE TABLE jobs (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,                       -- frontrun_search_ca, frontrun_ca_history, ...
    key         text NOT NULL,                       -- например '<mint>:migrate' или '<username>'
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority    integer NOT NULL DEFAULT 0,          -- больше — раньше
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    run_after   timestamptz NOT NULL DEFAULT now(),
    attempts    integer NOT NULL DEFAULT 0,
    locked_at   timestamptz,
    last_error  text,
    result      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, key)
);
CREATE INDEX jobs_due_idx ON jobs (priority DESC, run_after, id) WHERE status = 'pending';
CREATE INDEX jobs_running_idx ON jobs (locked_at) WHERE status = 'running';

CREATE INDEX api_calls_ts_idx ON api_calls (ts DESC);

-- Кто из авторов твитов признан шиллером, за которым стоит следить (история CA, кошельки).
ALTER TABLE kols ADD COLUMN tracked boolean NOT NULL DEFAULT false;
ALTER TABLE kols ADD COLUMN last_mention_at timestamptz;
