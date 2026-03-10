-- Migration 0008: bronze_archive_log table
-- Source: v4.0 design doc lines 2293-2330
-- Rule 3 compliant: admin metadata only, no observed_at + value columns.

CREATE TABLE forge.bronze_archive_log (
    id                BIGSERIAL PRIMARY KEY,

    -- identity
    source_id         TEXT        NOT NULL REFERENCES forge.source_catalog(source_id),
    metric_id         TEXT        NOT NULL REFERENCES forge.metric_catalog(metric_id),
    partition_date    DATE        NOT NULL,

    -- location
    archive_path      TEXT        NOT NULL,   -- MinIO object key, e.g. s3://bronze-archive/coinalyze/2026/03/06/funding_rate.parquet
    byte_size         BIGINT,

    -- content
    row_count         INTEGER     NOT NULL,
    observed_at_min   TIMESTAMPTZ,            -- earliest observed_at in the file
    observed_at_max   TIMESTAMPTZ,            -- latest observed_at in the file

    -- integrity
    checksum          TEXT        NOT NULL,   -- SHA-256 of the archive file
    checksum_verified BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at       TIMESTAMPTZ,

    -- audit
    archived_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archive_job_run_id TEXT,                  -- Dagster run ID for traceability

    -- idempotency
    UNIQUE (source_id, metric_id, partition_date)
);

CREATE INDEX ON forge.bronze_archive_log (source_id, partition_date);
CREATE INDEX ON forge.bronze_archive_log (archived_at);
-- observed_at_min/max are coverage bounds, not metric observations.
-- Archive job uses INSERT ... ON CONFLICT (source_id, metric_id, partition_date)
--   DO UPDATE SET checksum_verified = FALSE, verified_at = NULL
--   to reset verification state on re-runs.
