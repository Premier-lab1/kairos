-- Докуда у токена загружены сделки (Helius). Исход на горизонте считается, только когда конец горизонта
-- не позже trades_synced_until — иначе отсутствие сделок нельзя отличить от «не загрузили».
ALTER TABLE tokens ADD COLUMN trades_synced_until timestamptz;
ALTER TABLE tokens ADD COLUMN trades_backfill_truncated boolean NOT NULL DEFAULT false;
