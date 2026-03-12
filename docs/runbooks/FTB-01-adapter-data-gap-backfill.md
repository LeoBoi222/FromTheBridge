# FTB-01: Adapter Data Gap Backfill

**Trigger:** Adapter freshness exceeds 2x cadence (detected by `ftb_ops.adapter_health`)
**Severity:** Yellow (escalate to Red if >3 adapters simultaneously)

## Detection

`ftb_ops.adapter_health` checks every 30 minutes. Alert fires when `last_observation_at` exceeds `cadence × 2` for any source.

```bash
# Manual check — query Silver for staleness per source
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT source_id, max(observed_at) AS last_obs, now() - max(observed_at) AS gap
            FROM forge.observations GROUP BY source_id ORDER BY gap DESC\""
```

## Impact

- Silver grows stale for affected source
- Gold export propagates gap (export reads Silver)
- Marts/features built on incomplete data
- Training windows may become non-contiguous

## Immediate Action

1. **Identify scope** — which source, which metrics, how long the gap
2. **Check EDS** — gap may originate upstream. If EDS `empire.observations` also has a gap, this is an EDS problem (escalate to EDS-side investigation)
3. **Check sync bridge** — if EDS has data but FTB doesn't, the bridge missed it

## Resolution Steps

### If gap is in EDS (no data in `empire.observations`):

This is an EDS responsibility. Check EDS adapter status:

```bash
# Check EDS-side data for the source
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user eds_reader --password \$(cat /opt/empire/EmpireDataServices/secrets/eds_reader_password) \
  --query \"SELECT source_id, max(observed_at) AS last_obs
            FROM empire.observations WHERE source_id = '<SOURCE>'
            GROUP BY source_id\""
```

If EDS also has a gap, investigate the EDS adapter (see EDS runbooks).

### If gap is in sync bridge (EDS has data, FTB doesn't):

```bash
# Check sync bridge last run
ssh root@192.168.68.11 "docker exec empire_dagster_webserver dagster run list --limit 5"

# Force a sync bridge materialization
# Navigate to Dagster UI at 192.168.68.11:3010
# Materialize empire_to_forge_sync asset manually
```

### If gap is filled after re-sync:

Verify the backfill covered the gap:

```bash
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT toDate(observed_at) AS day, count() AS rows
            FROM forge.observations
            WHERE source_id = '<SOURCE>'
              AND observed_at >= now() - INTERVAL 7 DAY
            GROUP BY day ORDER BY day\""
```

## Verification

- [ ] `ftb_ops.adapter_health` shows freshness within cadence for affected source
- [ ] No new dead letter entries for the backfilled period
- [ ] Row counts per day show no remaining gaps

## Post-Mortem

Document in `docs/runbooks/incidents/`:
- Source and duration of gap
- Root cause (EDS adapter, sync bridge, or ClickHouse)
- Whether any downstream consumers were affected
- Prevention measure (if applicable)
