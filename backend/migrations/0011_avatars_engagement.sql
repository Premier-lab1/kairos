-- Аватар шиллера (URL картинки профиля X из Frontrun) и просмотры твита для окна энгейджмента.
ALTER TABLE kols ADD COLUMN IF NOT EXISTS avatar_url text;
ALTER TABLE mentions ADD COLUMN IF NOT EXISTS view_count bigint;
