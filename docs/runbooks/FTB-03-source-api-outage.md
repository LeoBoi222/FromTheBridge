# FTB-03: Source API Outage Response

**Trigger:** Source returns errors for >2 collection cycles
**Severity:** Yellow (escalate to Red if >3 sources simultaneously)

## Detection

`ftb_ops.adapter_health` detects via `observations_24h = 0` or `last_observation_at` exceeding cadence. Since FTB does not collect directly, this manifests as stale data in `forge.observations` after `empire_to_forge_sync` runs but finds nothing new.

```bash
# Check which sources have zero recent data
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT source_id,
            max(observed_at) AS last_obs,
            countIf(observed_at >= now() - INTERVAL 24 HOUR) AS obs_24h
            FROM forge.observations
            GROUP BY source_id
            ORDER BY last_obs ASC\""
```

## Impact

- Affected source contributes zero new data
- Silver stale → Gold stale → Marts stale for affected metrics
- If source outage persists beyond staleness_threshold, features degrade
- EDSx pillar scores that depend on affected metrics become unreliable

## Immediate Action

1. **Confirm it's a source-side issue** — check EDS adapter logs, not FTB
2. **Check source status page** if available (e.g., FRED, DeFiLlama status)
3. **Do NOT build a workaround adapter in FTB** — EDS owns all collection

## Resolution Steps

### Step 1: Verify the outage is upstream

```bash
# Check EDS-side — does empire.observations also have a gap?
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user eds_reader --password \$(cat /opt/empire/EmpireDataServices/secrets/eds_reader_password) \
  --query \"SELECT source_id, max(observed_at) AS last_obs
            FROM empire.observations
            WHERE source_id IN ('fred', 'defillama', 'tiingo', 'binance_blc01')
            GROUP BY source_id ORDER BY last_obs\""

# Check EDS Dagster runs for adapter failures
# Dagster UI: 192.168.68.11:3010 → EDS code location
```

### Step 2: Assess duration and decide response

| Duration | Action |
|----------|--------|
| < 1 day | Wait. Most APIs recover. Note in event calendar. |
| 1–3 days | Log event in `forge.event_calendar`. Monitor daily. |
| > 3 days | Investigate alternative source or EDS adapter fix. |

### Step 3: Log the outage

```sql
-- Insert outage event (via calendar_writer or manual)
INSERT INTO forge.event_calendar (event_type, event_date, description, system_id, severity, metadata)
VALUES ('maintenance', CURRENT_DATE, '<SOURCE> API outage detected', 'ftb', 'yellow',
        '{"source": "<SOURCE>", "started": "<TIMESTAMP>"}');
```

### Step 4: When source recovers

EDS adapters will resume collection automatically. Verify data flows through:

```bash
# After EDS resumes, trigger sync manually if needed
# Dagster UI → materialize empire_to_forge_sync

# Verify gap is filling
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT toDate(observed_at) AS day, count() AS rows
            FROM forge.observations
            WHERE source_id = 'eds_derived'
              AND observed_at >= now() - INTERVAL 7 DAY
            GROUP BY day ORDER BY day\""
```

## Verification

- [ ] `ftb_ops.adapter_health` freshness returns to normal
- [ ] No gap in daily row counts for affected source
- [ ] Event calendar entry updated with resolution

## Post-Mortem

Document: source, outage duration, whether any downstream impact occurred, whether backfill was needed.
