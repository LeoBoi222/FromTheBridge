# FTB-08: Sync Boundary Gap Resolution

**Trigger:** EDS sync delivers partial or zero data
**Severity:** Yellow (escalate to Red if prolonged — data pipeline fully stalled)

## Detection

`ftb_ops.sync_health` monitors the `empire_to_forge_sync` asset. Detects when sync runs complete but deliver fewer rows than expected, or when the watermark stops advancing.

```bash
# Check sync watermark — last observed_at synced to forge
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT max(observed_at) AS forge_watermark FROM forge.observations\""

# Compare with EDS watermark
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user eds_reader --password \$(cat /opt/empire/EmpireDataServices/secrets/eds_reader_password) \
  --query \"SELECT max(observed_at) AS empire_watermark FROM empire.observations\""
```

## Impact

- This is the single bridge between EDS and FTB — if it fails, ALL new data stops
- Silver, Gold, Marts, features, signals all go stale
- Unlike single-source outages, this affects every metric

## Immediate Action

1. **Compare watermarks** — forge vs empire. The gap is the problem window.
2. **Check the sync asset run log** for errors
3. **Do NOT bypass the sync bridge** — it enforces validation and catalog filtering

## Resolution Steps

### Step 1: Diagnose the gap

```bash
# Per-metric gap analysis
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT metric_id, max(observed_at) AS last_forge
            FROM forge.observations
            GROUP BY metric_id
            ORDER BY last_forge ASC
            LIMIT 20\""

# Check if EDS has data that FTB doesn't
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user eds_reader --password \$(cat /opt/empire/EmpireDataServices/secrets/eds_reader_password) \
  --query \"SELECT source_id, count() AS rows,
            min(observed_at) AS earliest, max(observed_at) AS latest
            FROM empire.observations
            WHERE observed_at > '<FORGE_WATERMARK>'
            GROUP BY source_id\""
```

### Step 2: Check sync bridge code

```bash
# Check sync asset logs
ssh root@192.168.68.11 "docker logs empire_dagster_code --tail 300 2>&1 | grep -i 'sync\|bridge\|empire_to_forge'"
```

Common sync issues:
- **Metric not in catalog** — sync bridge only syncs metrics registered in `forge.metric_catalog` with `eds_derived` in sources
- **EDS metric name mismatch** — EDS uses different metric_id than FTB catalog (see Deploy Gotchas: C3 resolution pending)
- **Validation rejection** — GE expectations rejecting legitimate data (check FTB-02)
- **ClickHouse connection** — `ch_writer` credentials or connectivity

### Step 3: Metric catalog mismatch

```bash
# List metrics the sync bridge is configured to pull
ssh root@192.168.68.11 "docker exec empire_postgres psql -U forge_reader -d crypto_structured \
  -c \"SELECT metric_id FROM forge.metric_catalog WHERE 'eds_derived' = ANY(sources) ORDER BY metric_id\""

# Compare with what EDS is producing
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user eds_reader --password \$(cat /opt/empire/EmpireDataServices/secrets/eds_reader_password) \
  --query \"SELECT DISTINCT metric_id FROM empire.observations ORDER BY metric_id\""
```

If EDS metric names don't match FTB catalog entries, either:
- Update FTB catalog to match EDS naming (if EDS is correct)
- Fix EDS adapter to use canonical names (if FTB is correct)

### Step 4: Re-trigger sync

```bash
# Materialize empire_to_forge_sync from Dagster UI (192.168.68.11:3010)
# The sync uses watermark-based ingestion — it picks up from last successful position
```

### Step 5: If watermark is far behind

For large gaps, the sync may need multiple runs to catch up. Monitor progress:

```bash
# Watch watermark advance
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT max(observed_at) AS watermark, count() AS total_rows FROM forge.observations\""
```

## Verification

- [ ] Forge watermark is within 6h of empire watermark (normal sync lag)
- [ ] `ftb_ops.sync_health` reports healthy
- [ ] No gaps in per-day row counts for synced metrics
- [ ] Dead letter rate for the sync period is <1%

## Post-Mortem

Document: gap duration (forge vs empire watermarks), root cause (catalog mismatch, validation, connectivity), rows affected, whether any downstream consumers were impacted.
