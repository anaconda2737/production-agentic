CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS ix_judgment_aliases_normalized_trgm
    ON judgment_aliases USING GIN (normalized_value gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_party_aliases_normalized_trgm
    ON party_aliases USING GIN (normalized_value gin_trgm_ops);

