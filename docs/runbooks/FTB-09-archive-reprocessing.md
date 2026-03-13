# FTB-09: Archive Reprocessing Procedure

**Trigger:** Data needs reprocessing from bronze-archive (e.g., after hot expiry, schema correction, or validation rule change)
**Severity:** Yellow
**Reference:** v4.0 §Layer 1: Landing Zone — "8-step documented procedure"

## Prerequisites

- Archive partitions exist in `bronze-archive` for the target source/date range
- `forge.bronze_archive_log` has entries for the target partitions
- MinIO, ClickHouse, and Dagster are healthy

## 8-Step Procedure

### Step 1: Identify target partitions in archive

```bash
# List archived partitions for source/date range
ssh root@192.168.68.11 "docker exec empire_minio mc ls --recursive local/bronze-archive/<source_id>/ 2>&1"

# Cross-reference with archive log
ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_reader -d crypto_structured -c \"
  SELECT source_id, metric_id, partition_date, row_count, checksum_verified
  FROM forge.bronze_archive_log
  WHERE source_id = '<source_id>'
    AND partition_date BETWEEN '<start_date>' AND '<end_date>'
  ORDER BY partition_date;
\""
```

### Step 2: Record Silver baseline before reprocessing

```bash
# Count existing Silver rows for the target range (for post-reprocessing comparison)
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  -u ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader.txt) \
  -q \"SELECT count() as before_count FROM forge.observations FINAL
       WHERE source_id = '<source_id>'
         AND observed_at >= '<start_date>' AND observed_at < '<end_date>'\""
```

### Step 3: Copy partitions from archive to hot

```bash
# mc cp --recursive from archive to hot
ssh root@192.168.68.11 "docker exec empire_minio mc cp --recursive \
  local/bronze-archive/<source_id>/<partition_path>/ \
  local/bronze-hot/<source_id>/<partition_path>/ 2>&1"
```

### Step 4: Force-materialize Bronze Iceberg table

The Bronze Iceberg table (`bronze.observations_hot`) must recognize the restored files. If files were copied outside Iceberg (via `mc cp`), the Iceberg metadata doesn't know about them. Re-ingest via the sync pipeline to update Iceberg metadata.

```bash
# Trigger empire_to_forge_sync which writes to both Bronze (Iceberg) and Silver (ClickHouse)
ssh root@192.168.68.11 'curl -s -X POST http://localhost:3010/graphql \
  -H "Content-Type: application/json" \
  -d '"'"'{"query": "mutation { launchRun(executionParams: { selector: { repositoryLocationName: \"ftb\", repositoryName: \"__repository__\", jobName: \"sync_job\" }, runConfigData: {} }) { __typename ... on LaunchRunSuccess { run { runId } } ... on PythonError { message } } }"}'"'"''
```

Note: The sync asset reads from `empire.observations` and writes to both Bronze and Silver. For archive reprocessing, the source data must still be available in `empire.observations` or the sync must be configured to re-read the target range.

### Step 5: Silver deduplication via ReplacingMergeTree

ClickHouse `forge.observations` uses `ReplacingMergeTree(data_version)` with ORDER BY `(metric_id, instrument_id, observed_at)`. Duplicate rows with the same key and `data_version` are automatically deduplicated:
- At query time: use `FROM forge.observations FINAL`
- At merge time: background merges physically remove duplicates

```bash
# Verify deduplication works (FINAL count should match pre-reprocessing baseline)
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  -u ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader.txt) \
  -q \"SELECT count() as after_final FROM forge.observations FINAL
       WHERE source_id = '<source_id>'
         AND observed_at >= '<start_date>' AND observed_at < '<end_date>'\""
```

### Step 6: Trigger Silver → Gold export

```bash
ssh root@192.168.68.11 'curl -s -X POST http://localhost:3010/graphql \
  -H "Content-Type: application/json" \
  -d '"'"'{"query": "mutation { launchRun(executionParams: { selector: { repositoryLocationName: \"ftb\", repositoryName: \"__repository__\", jobName: \"gold_export_job\" }, runConfigData: {} }) { __typename ... on LaunchRunSuccess { run { runId status } } ... on PythonError { message } } }"}'"'"''

# If row count triggers anomaly guard, re-run with force_backfill:
# Add to runConfigData: {"ops": {"gold_observations": {"config": {"force_backfill": true}}}}
```

### Step 7: Verify Gold export success

```bash
# Check Dagster run status
ssh root@192.168.68.11 'curl -s -X POST http://localhost:3010/graphql \
  -H "Content-Type: application/json" \
  -d '"'"'{"query": "{ runsOrError(filter: {pipelineName: \"gold_export_job\"}, limit: 1) { __typename ... on Runs { results { runId status } } } }"}'"'"''

# Verify Gold Iceberg table has data for the reprocessed range
ssh root@192.168.68.11 "docker exec empire_minio mc ls --recursive local/gold/ 2>&1 | tail -10"
```

### Step 8: Rematerialize features (Phase 2+)

Feature rematerialization is not available until Phase 2 (feature engineering layer). When available:
- Trigger `forge_compute` for the affected metric/instrument/date range
- Verify feature store PIT correctness for the reprocessed window

## Verification Checklist

- [ ] Archive partitions copied to hot (Step 3)
- [ ] Bronze Iceberg table updated (Step 4)
- [ ] Silver FINAL count matches expected (no phantom duplicates) (Step 5)
- [ ] Gold export run SUCCESS (Step 7)
- [ ] No new dead letter entries for reprocessed data

## Rollback

Reprocessing is idempotent. ReplacingMergeTree deduplicates on `(metric_id, instrument_id, observed_at)` with `data_version`. Re-running the procedure with the same data produces the same result. No rollback needed.
