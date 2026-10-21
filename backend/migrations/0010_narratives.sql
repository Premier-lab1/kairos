ALTER TABLE tokens ADD COLUMN IF NOT EXISTS narrative text;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS narrative_version smallint;
CREATE INDEX IF NOT EXISTS tokens_narrative_idx ON tokens (narrative);
-- Cover the rolling-window aggregates without sorting/fetching full trade rows.
CREATE INDEX IF NOT EXISTS trades_positive_cap_time_idx ON trades (ts, token_id)
    WHERE market_cap_sol > 0;
CREATE INDEX IF NOT EXISTS mentions_narrative_time_idx ON mentions (tweet_time)
    INCLUDE (token_id, kol_id);
