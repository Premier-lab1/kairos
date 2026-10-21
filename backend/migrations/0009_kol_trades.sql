ALTER TABLE kols ADD COLUMN wallets_fetched_at timestamptz;
ALTER TABLE wallets ADD COLUMN kol_trades_synced_until timestamptz;

CREATE TABLE kol_trades (
    signature text NOT NULL,
    wallet_id bigint NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    kol_id bigint NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    token_id bigint REFERENCES tokens(id) ON DELETE SET NULL,
    mint text NOT NULL,
    ts timestamptz NOT NULL,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    sol_amount numeric NOT NULL,
    token_amount numeric NOT NULL,
    market_cap_sol numeric,
    program text NOT NULL,
    PRIMARY KEY (signature, wallet_id, mint)
);
CREATE INDEX kol_trades_ts_idx ON kol_trades (ts DESC);
CREATE INDEX kol_trades_kol_ts_idx ON kol_trades (kol_id, ts DESC);
