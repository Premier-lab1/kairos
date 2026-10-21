-- Сигналы (docs/signals.md): одна строка на (токен, версия скорера), обновляется по мере новых шиллов.
-- created_at — когда сигнал появился впервые, updated_at — последний пересчёт.
ALTER TABLE signals ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE signals ADD COLUMN first_mention_at timestamptz;
ALTER TABLE signals ADD COLUMN n_shillers integer NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX signals_token_scorer_uq ON signals (token_id, scorer_version);
CREATE INDEX signals_updated_idx ON signals (updated_at DESC);
