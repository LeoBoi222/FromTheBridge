-- =============================================================================
-- ClickHouse Silver Schema — Phase 0
-- FromTheBridge / Empire Architecture
-- Authority: thread_4_data_universe.md §3, thread_infrastructure.md ADR-001
-- =============================================================================
-- Target: empire_clickhouse (port 8123 / 9000)
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Create forge database
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS forge;

-- ---------------------------------------------------------------------------
-- 2. forge.observations — Silver observation store
-- ---------------------------------------------------------------------------
-- Engine: ReplacingMergeTree(data_version)
--   - Higher data_version wins on merge (revision handling)
--   - Deduplication is eventual (background OPTIMIZE), not immediate
--   - Queries against unmerged data must use FINAL keyword
-- Ordering key: (metric_id, instrument_id, observed_at)
--   - instrument_id is non-nullable String; sentinel '__market__' for
--     market-level metrics (avoids ReplacingMergeTree dedup edge cases
--     on nullable ordering key columns — thread_4 locked decision)
-- Partition: monthly by observed_at
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forge.observations
(
    metric_id           String              NOT NULL,
    instrument_id       String              NOT NULL DEFAULT '__market__',
    source_id           String              NOT NULL,
    observed_at         DateTime64(3, 'UTC') NOT NULL,
    ingested_at         DateTime64(3, 'UTC') NOT NULL DEFAULT now64(3),
    value               Float64,
    data_version        UInt64              NOT NULL DEFAULT 1
)
ENGINE = ReplacingMergeTree(data_version)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (metric_id, instrument_id, observed_at)
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- 3. forge.dead_letter — rejected observations
-- ---------------------------------------------------------------------------
-- Valid rejection codes (document here for adapter reference):
--   RANGE_VIOLATION, TYPE_MISMATCH, NULL_VIOLATION, UNKNOWN_METRIC,
--   UNKNOWN_INSTRUMENT, DUPLICATE_OBSERVATION, STALE_OBSERVATION,
--   SCHEMA_ERROR, UNIT_UNKNOWN, EXTREME_VALUE_PENDING_REVIEW
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forge.dead_letter
(
    source_id           String              NOT NULL,
    metric_id           Nullable(String),
    instrument_id       Nullable(String),
    raw_payload         String              NOT NULL,
    rejection_reason    String              NOT NULL,
    rejection_code      LowCardinality(String) NOT NULL,
    collected_at        DateTime64(3, 'UTC') NOT NULL,
    rejected_at         DateTime64(3, 'UTC') NOT NULL DEFAULT now64(3)
)
ENGINE = MergeTree()
ORDER BY (rejection_code, source_id, rejected_at)
PARTITION BY toYYYYMM(rejected_at)
TTL rejected_at + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- 4. forge.current_values — materialized view (incremental on insert)
-- ---------------------------------------------------------------------------
-- Engine: ReplacingMergeTree(data_version)
-- Updates incrementally as new rows are inserted into observations.
-- Not refresh-on-demand (thread_4 locked decision).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS forge.current_values
ENGINE = ReplacingMergeTree(data_version)
ORDER BY (metric_id, instrument_id)
POPULATE AS
SELECT
    metric_id,
    instrument_id,
    source_id,
    observed_at,
    ingested_at,
    value,
    data_version
FROM forge.observations;

-- ---------------------------------------------------------------------------
-- 5. Users and grants
-- ---------------------------------------------------------------------------
-- forge_writer: adapters write Silver via this user
-- forge_reader: read-only access (export job, future consumers)
-- ---------------------------------------------------------------------------
CREATE USER IF NOT EXISTS forge_writer
    IDENTIFIED BY '9F0ryvApBiLH/Z51qfxOHYgdAJYrwByXUiK6HfbGgHk='
    HOST ANY;

GRANT INSERT, SELECT ON forge.observations TO forge_writer;
GRANT INSERT, SELECT ON forge.dead_letter TO forge_writer;
GRANT SELECT ON forge.current_values TO forge_writer;

CREATE USER IF NOT EXISTS forge_reader
    IDENTIFIED BY 'mVkk/DuZANpgLFHnLZgX1wRXnd0Qa7anCxYopa5VSmo='
    HOST ANY;

GRANT SELECT ON forge.observations TO forge_reader;
GRANT SELECT ON forge.dead_letter TO forge_reader;
GRANT SELECT ON forge.current_values TO forge_reader;
