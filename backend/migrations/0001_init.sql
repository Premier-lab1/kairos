-- Kairos: базовая схема «шиллер → токен → исход».
-- Работает на чистом PostgreSQL; если доступен TimescaleDB, временные ряды становятся гипертаблицами.

-- ───────────── Шиллеры (KOL) и их кошельки ─────────────

CREATE TABLE kols (
    id                     bigserial PRIMARY KEY,
    twitter_id             text UNIQUE,              -- стабильный идентификатор X; ник может меняться
    username               text NOT NULL,            -- текущий ник без @
    display_name           text,
    bio                    text,
    followers_count        integer,
    smart_followers_count  integer,
    smart_followers_final  boolean,                  -- meta.resolved из Frontrun
    primary_label          text,
    compact_label          text,
    first_seen_at          timestamptz NOT NULL DEFAULT now(),
    profile_fetched_at     timestamptz,
    ca_history_fetched_at  timestamptz,
    updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX kols_username_lower_uq ON kols (lower(username));

CREATE TABLE kol_username_history (
    kol_id      bigint NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    username    text NOT NULL,
    changed_at  timestamptz,
    source      text NOT NULL,                       -- frontrun_lookup, observed
    PRIMARY KEY (kol_id, username)
);

CREATE TABLE wallets (
    id             bigserial PRIMARY KEY,
    chain          text NOT NULL DEFAULT 'SOLANA',   -- SOLANA | EVM
    address        text NOT NULL,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chain, address)
);

CREATE TABLE kol_wallets (
    kol_id         bigint NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    wallet_id      bigint NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    relation       text NOT NULL CHECK (relation IN ('associated', 'mentioned', 'fomo', 'manual')),
    source         text NOT NULL,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kol_id, wallet_id, relation)
);
CREATE INDEX kol_wallets_wallet_idx ON kol_wallets (wallet_id);

-- ───────────── Токены ─────────────

CREATE TABLE tokens (
    id                      bigserial PRIMARY KEY,
    chain                   text NOT NULL DEFAULT 'SOLANA',
    mint                    text NOT NULL,
    name                    text,
    symbol                  text,
    uri                     text,
    launchpad               text,                    -- pump, bonk, ... (поле pool у PumpPortal)
    creator_wallet_id       bigint REFERENCES wallets(id),
    bonding_curve           text,
    create_signature        text,
    created_at              timestamptz,             -- время создания (по первому событию)
    initial_buy_sol         numeric,
    initial_market_cap_sol  numeric,
    migrated_at             timestamptz,             -- ушёл с кривой в пул
    migration_pool          text,
    metadata                jsonb,
    first_seen_at           timestamptz NOT NULL DEFAULT now(),
    source                  text NOT NULL,           -- pumpportal, frontrun_ca_history, helius, ...
    UNIQUE (chain, mint)
);
CREATE INDEX tokens_created_at_idx ON tokens (created_at DESC);
CREATE INDEX tokens_creator_idx ON tokens (creator_wallet_id);

-- ───────────── Шиллинг: упоминания CA шиллерами ─────────────

CREATE TABLE mentions (
    id             bigserial PRIMARY KEY,
    kol_id         bigint NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    chain          text NOT NULL,
    ca             text NOT NULL,                    -- адрес как в твите; токена может ещё не быть в tokens
    token_id       bigint REFERENCES tokens(id),     -- проставляется, когда токен известен
    tweet_id       text NOT NULL,
    tweet_time     timestamptz NOT NULL,
    content        text,
    is_deleted     boolean NOT NULL DEFAULT false,
    reply_count    integer,
    retweet_count  integer,
    quote_count    integer,
    favorite_count integer,
    source         text NOT NULL,                    -- frontrun_ca_history, frontrun_search
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tweet_id, ca)
);
CREATE INDEX mentions_kol_time_idx ON mentions (kol_id, tweet_time DESC);
CREATE INDEX mentions_ca_idx ON mentions (chain, ca);
CREATE INDEX mentions_token_idx ON mentions (token_id, tweet_time);

-- ───────────── Временные ряды ─────────────

-- Сделки по токену (Helius / PumpPortal trades). Первичный ключ включает ts ради гипертаблицы.
CREATE TABLE trades (
    ts              timestamptz NOT NULL,
    signature       text NOT NULL,
    token_id        bigint NOT NULL REFERENCES tokens(id),
    wallet_address  text NOT NULL,
    side            text NOT NULL CHECK (side IN ('buy', 'sell')),
    sol_amount      numeric NOT NULL,
    token_amount    numeric NOT NULL,
    market_cap_sol  numeric,
    source          text NOT NULL,
    PRIMARY KEY (signature, token_id, ts)
);
CREATE INDEX trades_token_ts_idx ON trades (token_id, ts DESC);
CREATE INDEX trades_wallet_ts_idx ON trades (wallet_address, ts DESC);

-- Снимки состояния токена: цена, капитализация, ликвидность.
CREATE TABLE token_snapshots (
    ts              timestamptz NOT NULL,
    token_id        bigint NOT NULL REFERENCES tokens(id),
    price_sol       numeric,
    market_cap_sol  numeric,
    liquidity_sol   numeric,                         -- SOL в кривой или в пуле
    holders         integer,
    source          text NOT NULL,
    PRIMARY KEY (token_id, ts)
);

-- Сырые события источников: для повторной обработки и отладки.
CREATE TABLE raw_events (
    received_at  timestamptz NOT NULL DEFAULT now(),
    source       text NOT NULL,
    kind         text NOT NULL,
    payload      jsonb NOT NULL
);
CREATE INDEX raw_events_time_idx ON raw_events (received_at DESC);

-- ───────────── Исходы и сигналы ─────────────

-- Исход токена на горизонте. Правила разметки версионируются (rule_version), см. docs/outcomes.md.
CREATE TABLE token_outcomes (
    token_id         bigint NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    horizon          interval NOT NULL,              -- '1 hour', '24 hours', '7 days'
    rule_version     text NOT NULL,
    reference        text NOT NULL,                  -- creation | first_mention
    reference_time   timestamptz NOT NULL,
    label            text NOT NULL CHECK (label IN ('success', 'rug', 'dead', 'neutral', 'pending', 'no_data')),
    peak_multiple    numeric,                        -- пик капитализации / капитализация в точке отсчёта
    end_multiple     numeric,                        -- на конец горизонта
    max_drawdown     numeric,                        -- 0..1 от пика
    liquidity_alive  boolean,
    migrated         boolean,
    metrics          jsonb,
    computed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (token_id, horizon, rule_version, reference)
);
CREATE INDEX token_outcomes_label_idx ON token_outcomes (rule_version, horizon, label);

CREATE TABLE signals (
    id             bigserial PRIMARY KEY,
    token_id       bigint NOT NULL REFERENCES tokens(id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    score          numeric NOT NULL,
    confidence     text CHECK (confidence IN ('low', 'medium', 'high')),
    scorer_version text NOT NULL,
    features       jsonb NOT NULL,                   -- кто внутри, на какой минуте, похожие прошлые случаи
    explanation    text,                             -- текст LLM выше порога
    delivered_at   timestamptz
);
CREATE INDEX signals_token_idx ON signals (token_id, created_at DESC);
CREATE INDEX signals_created_idx ON signals (created_at DESC);

-- ───────────── Учёт платных API ─────────────

CREATE TABLE api_calls (
    ts           timestamptz NOT NULL DEFAULT now(),
    provider     text NOT NULL,                      -- frontrun, helius, sorsa, llm
    endpoint     text NOT NULL,
    credits      numeric NOT NULL DEFAULT 0,
    http_status  integer,
    ok           boolean NOT NULL,
    note         text
);
CREATE INDEX api_calls_provider_ts_idx ON api_calls (provider, ts DESC);

-- ───────────── TimescaleDB, если есть ─────────────

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable('trades', 'ts', migrate_data => true);
        PERFORM create_hypertable('token_snapshots', 'ts', migrate_data => true);
    ELSE
        RAISE NOTICE 'timescaledb not available: time series stay plain tables';
    END IF;
END
$$;
