-- Read-only user for empire_to_forge_sync to query empire.observations
-- Password is set at deploy time via sed replacement
CREATE USER IF NOT EXISTS ch_empire_reader
    IDENTIFIED WITH sha256_password BY 'PLACEHOLDER_REPLACE_WITH_SECRET'
    SETTINGS PROFILE 'readonly';

GRANT SELECT ON empire.observations TO ch_empire_reader;
