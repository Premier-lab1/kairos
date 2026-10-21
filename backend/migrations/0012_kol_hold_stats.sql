-- Сколько КОЛ держит купленный токен (как «Median hold time» у Pump.fun callouts).
-- Позиция = кошелёк + mint: от первой покупки до первой продажи после неё. Ещё открытые позиции в медиану не входят.
CREATE OR REPLACE VIEW kol_positions AS
SELECT b.kol_id, b.wallet_id, b.mint, b.token_id, b.first_buy, s.first_sell,
       EXTRACT(EPOCH FROM (s.first_sell - b.first_buy)) AS hold_s
FROM (
    SELECT kol_id, wallet_id, mint, max(token_id) AS token_id, min(ts) AS first_buy
    FROM kol_trades WHERE side = 'buy' GROUP BY kol_id, wallet_id, mint
) b
LEFT JOIN LATERAL (
    SELECT min(t.ts) AS first_sell FROM kol_trades t
    WHERE t.wallet_id = b.wallet_id AND t.mint = b.mint AND t.kol_id = b.kol_id AND t.side = 'sell' AND t.ts >= b.first_buy
) s ON true;

CREATE OR REPLACE VIEW kol_hold_stats AS
SELECT kol_id,
       count(*) AS positions,
       count(first_sell) AS closed_positions,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY hold_s) AS median_hold_s,
       avg((hold_s < 60)::int) FILTER (WHERE hold_s IS NOT NULL) AS fast_dump_share,
       max(greatest(first_buy, first_sell)) AS last_trade
FROM kol_positions GROUP BY kol_id;

-- Окно токена в дашборде читает сделки КОЛов по mint: token_id у сделки бывает NULL, если токен появился позже.
CREATE INDEX IF NOT EXISTS kol_trades_mint_ts_idx ON kol_trades (mint, ts);
-- kol_positions ищет первую продажу по кошельку и mint.
CREATE INDEX IF NOT EXISTS kol_trades_wallet_mint_ts_idx ON kol_trades (wallet_id, mint, ts);
