# FTB-07: Export Job Failure Investigation

**Trigger:** Export fails or exports zero rows
**Severity:** Yellow (escalate to Red after 3 consecutive failures)

## Detection

`ftb_ops.export_health` checks every 30 minutes. Alert fires when `last_export_at > 2h ago` or `rows_exported_last_run = 0`.

```bash
# Check recent export runs in Dagster
ssh root@192.168.68.11 "docker logs empire_dagster_code --tail 200 2>&1 | grep -i 'gold_observations\|export'"

# Check Gold bucket for recent writes
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/gold/ --recursive 2>&1 | tail -10"
```

## Impact

- Gold layer goes stale — DuckDB reads from Gold
- Marts (Layer 6) built on stale Gold data
- Feature engineering and signal generation affected
- 3 consecutive failures = Red alert

## Immediate Action

1. **Check the Dagster run log** for the failed export
2. **Determine failure point** — Silver read, domain mapping, Iceberg write, or anomaly guard

## Resolution Steps

### Step 1: Identify failure point

The export pipeline (`src/ftb/export/export_asset.py`) has stages:
1. Read Silver via `ch_export_reader`
2. Domain mapping (`gold_export.py`)
3. Anomaly guard (rejects suspicious batches)
4. Write to Gold Iceberg (`gold_iceberg.py`)

```bash
# Check export asset logs
ssh root@192.168.68.11 "docker logs empire_dagster_code --tail 500 2>&1 | grep -A5 'gold_observations'"
```

### Step 2: Silver read failure

```bash
# Test ch_export_reader can read
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_export_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_export_reader_password) \
  --query \"SELECT count() FROM forge.observations WHERE observed_at >= now() - INTERVAL 2 HOUR\""
```

If zero rows: Silver is empty for the export window — check FTB-01 (adapter gap) or FTB-05 (Silver write failure).

### Step 3: Iceberg write failure

```bash
# Check MinIO Gold bucket
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/gold/ 2>&1 | head -10"

# Check disk space
ssh root@192.168.68.11 "df -h /mnt/empire-data"
```

Common Iceberg issues:
- **Schema nullability mismatch** — PyIceberg requires Arrow schema nullability to match Iceberg schema. Use `required=False` for all fields.
- **DuckDB version hint** — needs `SET unsafe_enable_version_guessing=true` without `version-hint.text`.
- **PyIceberg SqlCatalog** — `init_catalog_tables=true` targets `public` schema; must set `search_path=iceberg_catalog`.

### Step 4: Anomaly guard rejection

The anomaly guard rejects batches that look suspicious. Check logs for "anomaly" messages:

```bash
ssh root@192.168.68.11 "docker logs empire_dagster_code --tail 500 2>&1 | grep -i 'anomaly\|guard\|reject'"
```

If the guard is rejecting legitimate data, review thresholds in `src/ftb/export/gold_export.py`.

### Step 5: Re-trigger export

```bash
# Materialize gold_observations from Dagster UI (192.168.68.11:3010)
# Or wait for next gold_export_hourly run (at :15 past the hour)
```

## Verification

- [ ] Export run completes successfully with >0 rows
- [ ] Gold Iceberg table has recent snapshots
- [ ] `ftb_ops.export_health` reports `last_export_at` within 2h
- [ ] DuckDB can read the Gold table: `SELECT count(*) FROM gold.observations`

## Post-Mortem

Document: failure stage, error message, rows affected, whether data was recoverable from Silver, fix applied.
