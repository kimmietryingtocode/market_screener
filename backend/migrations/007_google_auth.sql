-- Google identities use the stable OpenID Connect subject, never email, as
-- their provider identifier. Password-only users keep this column NULL.

ALTER TABLE users ADD COLUMN google_sub TEXT;
CREATE UNIQUE INDEX users_google_sub_unique ON users(google_sub) WHERE google_sub IS NOT NULL;
