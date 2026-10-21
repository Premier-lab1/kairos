-- Ник в X может перейти к другому аккаунту после переименования; идентичность держим на twitter_id.
DROP INDEX kols_username_lower_uq;
CREATE INDEX kols_username_lower_idx ON kols (lower(username));
