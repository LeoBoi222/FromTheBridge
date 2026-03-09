-- =============================================================================
-- Phase 0 Corrective Migration — ClickHouse (Layer 4 Silver)
-- FromTheBridge Architecture
-- Authority: FromTheBridge_design_v4.0.md (SSOT)
-- =============================================================================
-- Target: empire_clickhouse (port 8123 / 9000)
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"
-- =============================================================================
-- PURPOSE: Rebuild observations (Nullable instrument_id + Nullable value),
--          current_values (AggregatingMergeTree + argMaxState), credential
--          isolation (3 scoped users + profiles), suspend default user.
-- =============================================================================
-- DESTRUCTIVE: Drops and recreates observations + current_values. Safe because
--              Phase 0 has no production data in forge.observations.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. DROP current_values MV first (depends on observations)
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS forge.current_values;

-- ---------------------------------------------------------------------------
-- 2. DROP observations table
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS forge.observations;

-- ---------------------------------------------------------------------------
-- 3. CREATE observations (v4.0 DDL)
-- ---------------------------------------------------------------------------
-- Changes from 0001:
--   - instrument_id: String NOT NULL DEFAULT '__market__' → Nullable(String)
--   - value: Float64 → Nullable(Float64)
--   - ingested_at: no DEFAULT (explicit now64(3) at insert time)
--   - Removed SETTINGS index_granularity (use default 8192)
-- Note: instrument_id is Nullable(String) per v4.0 — market-level metrics have
-- NULL instrument_id. ClickHouse forbids Nullable columns in ORDER BY, so we
-- use ifNull(instrument_id, '') in the sort key while preserving NULL storage.
CREATE TABLE forge.observations
(
    metric_id           String              NOT NULL,
    instrument_id       Nullable(String),
    source_id           String              NOT NULL,
    observed_at         DateTime64(3)       NOT NULL,
    ingested_at         DateTime64(3)       NOT NULL,
    value               Nullable(Float64),
    data_version        UInt64              NOT NULL
)
ENGINE = ReplacingMergeTree(data_version)
ORDER BY (metric_id, ifNull(instrument_id, ''), observed_at)
PARTITION BY toYYYYMM(observed_at);

-- ---------------------------------------------------------------------------
-- 4. Verify dead_letter (unchanged — no action needed)
-- ---------------------------------------------------------------------------
-- forge.dead_letter was created correctly in 0001. Structure matches v4.0.
-- No changes required.

-- ---------------------------------------------------------------------------
-- 5. CREATE current_values MV (AggregatingMergeTree + argMaxState)
-- ---------------------------------------------------------------------------
-- Changes from 0001:
--   - Engine: ReplacingMergeTree → AggregatingMergeTree
--   - SELECT: raw columns → argMaxState/maxState aggregates
--   - No POPULATE (empty table, will accumulate on insert)
CREATE MATERIALIZED VIEW forge.current_values
ENGINE = AggregatingMergeTree()
ORDER BY (metric_id, ifNull(instrument_id, ''))
AS SELECT
    metric_id,
    instrument_id,
    argMaxState(value, observed_at)       AS latest_value,
    maxState(observed_at)                 AS latest_observed_at,
    maxState(ingested_at)                 AS latest_ingested_at
FROM forge.observations
GROUP BY metric_id, instrument_id;

-- ---------------------------------------------------------------------------
-- 6. DROP old users
-- ---------------------------------------------------------------------------
DROP USER IF EXISTS forge_writer;
DROP USER IF EXISTS forge_reader;

-- ---------------------------------------------------------------------------
-- 7. CREATE settings profiles
-- ---------------------------------------------------------------------------
-- Resource profiles prevent unbounded consumption on shared ClickHouse instance.
-- EDS applies matching limits on its users (eds_writer, eds_reader).

CREATE SETTINGS PROFILE IF NOT EXISTS ch_writer_profile
    SETTINGS max_memory_usage = 4000000000,   -- 4 GB
             max_threads = 4,
             max_execution_time = 300;

CREATE SETTINGS PROFILE IF NOT EXISTS ch_export_reader_profile
    SETTINGS max_memory_usage = 2000000000,   -- 2 GB
             max_threads = 2,
             max_execution_time = 120;

CREATE SETTINGS PROFILE IF NOT EXISTS ch_ops_reader_profile
    SETTINGS max_memory_usage = 1000000000,   -- 1 GB
             max_threads = 1,
             max_execution_time = 30;

-- ---------------------------------------------------------------------------
-- 8. CREATE users with profiles
-- ---------------------------------------------------------------------------
-- Passwords sourced from secrets/ch_*.txt

CREATE USER IF NOT EXISTS ch_writer
    IDENTIFIED BY 'gYyDIi/0gsToRUJf1n0qQlO0MCJxEGlS1i1C/FiG2Eg='
    HOST ANY
    SETTINGS PROFILE ch_writer_profile;

CREATE USER IF NOT EXISTS ch_export_reader
    IDENTIFIED BY '5dIUDcJOOKLwxt0uhccj0Sq3LvUrN1+t8jPcIgtIQ8Y='
    HOST ANY
    SETTINGS PROFILE ch_export_reader_profile;

CREATE USER IF NOT EXISTS ch_ops_reader
    IDENTIFIED BY 'qJdqBzAvzLo3iGo4/lB4S59QfT43dqRRpw9dOe7Ncj0='
    HOST ANY
    SETTINGS PROFILE ch_ops_reader_profile;

CREATE USER IF NOT EXISTS ch_admin
    IDENTIFIED BY 'dR/z6DWh+3NKvsN1gxfeQZwUtjiTUXmM8aZ7C0BSUp8='
    HOST LOCAL;

-- ---------------------------------------------------------------------------
-- 9. GRANT scoped permissions (v4.0 credential isolation)
-- ---------------------------------------------------------------------------

-- ch_writer: INSERT-only on observations + dead_letter. No SELECT.
GRANT INSERT ON forge.observations TO ch_writer;
GRANT INSERT ON forge.dead_letter TO ch_writer;

-- ch_export_reader: SELECT-only on observations, dead_letter, current_values. No INSERT.
GRANT SELECT ON forge.observations TO ch_export_reader;
GRANT SELECT ON forge.dead_letter TO ch_export_reader;
GRANT SELECT ON forge.current_values TO ch_export_reader;

-- ch_ops_reader: SELECT-only on observations (metadata/counts), dead_letter, current_values.
GRANT SELECT ON forge.observations TO ch_ops_reader;
GRANT SELECT ON forge.dead_letter TO ch_ops_reader;
GRANT SELECT ON forge.current_values TO ch_ops_reader;

-- ch_admin: full access (DDL + admin) — operator terminal only
GRANT ALL ON forge.* TO ch_admin;

-- ---------------------------------------------------------------------------
-- 10. Default user access restriction
-- ---------------------------------------------------------------------------
-- The default user is managed by users.xml (read-only storage) and cannot
-- be modified via SQL REVOKE. Restrict default user access by configuring
-- <allow_databases> in docker/clickhouse/users.xml instead.
-- REVOKE ALL ON forge.* FROM default;  -- NOT SUPPORTED for XML-managed users

-- ---------------------------------------------------------------------------
-- 11. Verification queries (run after migration)
-- ---------------------------------------------------------------------------
-- SELECT count() FROM system.tables WHERE database = 'forge';
--   -- expect 3 (observations, dead_letter, current_values)
-- SHOW CREATE TABLE forge.observations;
--   -- verify Nullable(String) instrument_id, Nullable(Float64) value
-- SHOW CREATE TABLE forge.current_values;
--   -- verify AggregatingMergeTree, argMaxState
-- SELECT name FROM system.users WHERE name LIKE 'ch_%';
--   -- expect: ch_writer, ch_export_reader, ch_ops_reader, ch_admin
-- SELECT * FROM system.settings_profiles WHERE name LIKE 'ch_%';
--   -- expect 3 profiles
