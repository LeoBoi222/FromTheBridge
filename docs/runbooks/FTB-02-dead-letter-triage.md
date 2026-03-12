# FTB-02: Dead Letter Triage and Reprocessing

**Trigger:** Dead letter rate >1% or spike >10 entries in 24h
**Severity:** Yellow (escalate to Red if rate >5%)

## Detection

`ftb_ops.adapter_health` reports `dead_letter_24h` count per source. `ftb_ops.sync_health` reports aggregate dead letter rate.

```bash
# Manual check — recent dead letters
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT source_id, rejection_reason, count() AS cnt,
            min(rejected_at) AS first, max(rejected_at) AS last
            FROM forge.dead_letter
            WHERE rejected_at >= now() - INTERVAL 24 HOUR
            GROUP BY source_id, rejection_reason
            ORDER BY cnt DESC\""
```

## Impact

- Rejected observations are NOT in Silver — they represent data loss
- High dead letter rates indicate upstream schema changes or data quality degradation
- Persistent dead letters for a metric reduce feature coverage

## Immediate Action

1. **Classify the rejection reasons** — validation failure, duplicate, schema mismatch, null required field
2. **Determine if systematic or transient** — one bad batch vs ongoing pattern

## Resolution Steps

### Step 1: Examine rejected rows

```bash
# Sample dead letter entries for the affected source
ssh root@192.168.68.11 "docker exec empire_clickhouse clickhouse-client \
  --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader_password) \
  --query \"SELECT metric_id, instrument_id, observed_at, rejection_reason, raw_payload
            FROM forge.dead_letter
            WHERE source_id = '<SOURCE>'
              AND rejected_at >= now() - INTERVAL 24 HOUR
            ORDER BY rejected_at DESC
            LIMIT 20\""
```

### Step 2: Diagnose root cause

| Rejection Reason | Likely Cause | Fix |
|-----------------|--------------|-----|
| `null_required_field` | Upstream schema change | Check EDS adapter output format |
| `value_out_of_range` | Bad data from source API | Verify source API response, adjust GE expectations if range is legitimately wider |
| `duplicate_compound_key` | Reprocessed batch | Safe to ignore if Silver already has the data |
| `schema_mismatch` | metric_id not in catalog | Add to `forge.metric_catalog` if valid, or fix EDS metric naming |

### Step 3: Reprocess (if fixable)

Dead letters cannot be directly reprocessed — they must flow through the normal pipeline again. If the root cause is fixed:

1. Fix the upstream issue (EDS adapter, GE expectation, or catalog entry)
2. Trigger a re-sync that covers the affected time range
3. The sync bridge will re-ingest the corrected data

### Step 4: If dead letters are false positives (overly strict validation)

Update GE expectations in `src/ftb/validation/expectations.py`, test locally, deploy:

```bash
# Test locally
cd /var/home/stephen/Projects/FromTheBridge
uv run pytest tests/validation/ -v

# Deploy
rsync -av --exclude='__pycache__' --exclude='.git' src/ root@192.168.68.11:/opt/empire/FromTheBridge/src/
ssh root@192.168.68.11 'cd /opt/empire/FromTheBridge && docker compose build empire_dagster_code && docker compose up -d empire_dagster_code'
```

## Verification

- [ ] Dead letter rate returns below 1% threshold
- [ ] No new dead letters for the previously-failing rejection reason
- [ ] Silver row counts confirm reprocessed data landed

## Post-Mortem

Document: rejection reason distribution, root cause, whether GE expectations were adjusted, time to resolution.
