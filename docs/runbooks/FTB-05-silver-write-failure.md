# FTB-05: Silver Write Failure Recovery

**Trigger:** ClickHouse INSERT fails or rejects rows
**Severity:** Red

## Detection

Silver writes happen in `empire_to_forge_sync` (via `src/ftb/writers/silver.py`). Failures appear in Dagster run logs. `ftb_ops.sync_health` detects if expected row counts don't materialize.

```bash
# Check recent sync runs for failures
ssh root@192.168.68.11 "docker logs empire_dagster_code --tail 100 2>&1 | grep -i 'error\|fail\|exception'"

# Check ClickHouse is accepting connections
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query 'SELECT 1'"
```

## Impact

- Silver is the observation store — the canonical time series layer
- Failed Silver writes mean data exists in Bronze (raw) but not in the queryable layer
- Gold export reads Silver — stale Silver means stale Gold
- All downstream consumers (Marts, features, signals) are affected

## Immediate Action

1. **Check ClickHouse container health**
2. **Check disk space** on `/mnt/empire-db` (ClickHouse storage)
3. **Check `ch_writer` credentials** — INSERT requires SELECT grant (driver introspects column types)

## Resolution Steps

### ClickHouse container down

```bash
# Check container status
ssh root@192.168.68.11 "docker ps -f name=empire_clickhouse --format '{{.Status}}'"

# Check ClickHouse logs
ssh root@192.168.68.11 "docker logs empire_clickhouse --tail 100"

# Check disk space
ssh root@192.168.68.11 "df -h /mnt/empire-db"

# Restart ClickHouse (managed by Nexus-Council compose)
ssh root@192.168.68.11 "cd /opt/empire/Nexus-Council && docker compose restart empire_clickhouse"
```

### INSERT permission failure

```bash
# Test ch_writer can INSERT
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_writer --password \$(cat /opt/empire/FromTheBridge/secrets/ch_writer_password) \
  --query \"INSERT INTO forge.observations (metric_id, instrument_id, observed_at, value, source_id, ingested_at)
            VALUES ('test.ping', 'SYSTEM', now(), 1.0, 'test', now())\""

# If permission denied, re-grant (ch_writer needs INSERT + SELECT)
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --query \"GRANT INSERT, SELECT ON forge.observations TO ch_writer\""

# Clean up test row
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --query \"ALTER TABLE forge.observations DELETE WHERE metric_id = 'test.ping'\""
```

### Row rejection (data format issues)

Check dead letter table for rejection details — this overlaps with FTB-02:

```bash
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT rejection_reason, count() FROM forge.dead_letter
            WHERE rejected_at >= now() - INTERVAL 1 HOUR
            GROUP BY rejection_reason\""
```

### After recovery

Re-trigger sync to backfill missed Silver writes:

```bash
# Materialize empire_to_forge_sync from Dagster UI (192.168.68.11:3010)
# The sync bridge uses watermark-based ingestion — it will pick up where it left off
```

## Verification

- [ ] ClickHouse accepting INSERTs from `ch_writer`
- [ ] `ftb_ops.sync_health` shows healthy status
- [ ] Row counts in `forge.observations` increasing after re-sync
- [ ] No new entries in `forge.dead_letter` with write-related rejection reasons

## Post-Mortem

Document: failure type (container, permissions, disk, data format), duration, rows affected, whether Bronze had the data (recovery possible vs data loss).
