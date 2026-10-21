#!/usr/bin/env bash
# Ежедневная копия базы Kairos: сжатый дамп (pg_dump -Fc) в ~/backups, хранение 14 дней.
# Вызывается из cron пользователя kairos: 30 3 * * * /home/kairos/kairos/deploy/backup.sh
# Предупреждение pg_dump про circular foreign-key (continuous_agg) — штатное для TimescaleDB;
# восстановление — по разделу 11 deploy/README.md (timescaledb_pre_restore / post_restore).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
cd "$(dirname "$0")"
mkdir -p "$BACKUP_DIR"

file="$BACKUP_DIR/kairos-$(date -u +%F).dump"
tmp="$file.part"
docker compose exec -T db pg_dump -U kairos -Fc kairos > "$tmp" 2> >(grep -v -E "circular foreign-key|continuous_agg|disable-triggers|data-only dump" >&2)
mv "$tmp" "$file"
find "$BACKUP_DIR" -name "kairos-*.dump" -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup ok: $file ($(du -h "$file" | cut -f1))"
