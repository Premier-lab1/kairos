-- Когда сделка кошелька шиллера попала в базу. ts — время в блокчейне, а кошельки опрашиваются раз в час,
-- поэтому по ts нельзя понять, появились ли у токена новые сделки после последнего расчёта сигнала;
-- скоринг (score_due_tokens) сравнивает с signals.updated_at именно inserted_at.
ALTER TABLE kol_trades ADD COLUMN IF NOT EXISTS inserted_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS kol_trades_inserted_idx ON kol_trades (inserted_at DESC);
