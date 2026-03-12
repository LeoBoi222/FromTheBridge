# FTB-06: ClickHouse Merge Lag Resolution

**Trigger:** Unmerged parts >50 or merge lag >300s
**Severity:** Yellow (escalate to Red if merge lag >600s)

## Detection

`ftb_ops.export_health` checks `system.merges` and `system.parts` every 30 minutes.

```bash
# Manual check — unmerged parts for forge.observations
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT table, count() AS parts, sum(rows) AS total_rows,
            sum(bytes_on_disk) AS bytes
            FROM system.parts
            WHERE database = 'forge' AND active = 1
            GROUP BY table ORDER BY parts DESC\""

# Check active merges
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT table, elapsed, progress, num_parts, result_part_name
            FROM system.merges
            WHERE database = 'forge'\""
```

## Impact

- `forge.observations` uses `ReplacingMergeTree` — unmerged parts mean duplicate rows visible to queries
- Gold export reads Silver — duplicates propagate to Gold layer
- High part count degrades query performance
- Extreme lag (>600s) is a Red alert trigger

## Immediate Action

1. **Do NOT manually force merges immediately** — check why merges are lagging first
2. **Check disk I/O and CPU** — merges compete with INSERTs for resources

## Resolution Steps

### Step 1: Diagnose cause

```bash
# Check disk I/O
ssh root@192.168.68.11 "iostat -x 1 3 | grep -E 'Device|nvme|sd'"

# Check ClickHouse memory usage
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT formatReadableSize(sum(value)) AS memory_usage
            FROM system.metrics WHERE metric LIKE '%Memory%'\""

# Check if too many small INSERTs are creating excessive parts
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT table, partition, count() AS parts,
            min(modification_time) AS oldest_part,
            max(modification_time) AS newest_part
            FROM system.parts
            WHERE database = 'forge' AND active = 1
            GROUP BY table, partition
            HAVING parts > 10
            ORDER BY parts DESC\""
```

### Step 2: Force optimize (if merges are stuck)

```bash
# Force merge — use FINAL to trigger ReplacingMergeTree dedup
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --query \"OPTIMIZE TABLE forge.observations FINAL\""

# Monitor merge progress
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT elapsed, progress, num_parts FROM system.merges WHERE database = 'forge'\""
```

### Step 3: If caused by too many small INSERTs

The sync bridge batches INSERTs, but if batch sizes are too small, increase the batch size in `src/ftb/writers/silver.py` or adjust sync frequency.

## Verification

- [ ] Part count for `forge.observations` below 50
- [ ] No active merges lagging >300s
- [ ] `ftb_ops.export_health` reports healthy merge status
- [ ] Export job produces correct (deduplicated) row counts

## Post-Mortem

Document: peak part count, merge lag duration, root cause (disk I/O, small batches, table size), corrective action.
