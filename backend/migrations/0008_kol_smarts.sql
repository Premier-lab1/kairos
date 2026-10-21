-- Когда последний раз запрашивали число smart followers шиллера (Frontrun, 3 кредита): задача
-- frontrun_smart_followers обновляет его раз в 14 дней, незавершённый подсчёт (meta.resolved = false) — через сутки.
ALTER TABLE kols ADD COLUMN IF NOT EXISTS smart_followers_fetched_at timestamptz;
