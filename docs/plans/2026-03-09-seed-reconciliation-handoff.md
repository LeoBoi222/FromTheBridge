# Seed Reconciliation + Corrective Migration — Handoff

**Date:** 2026-03-09
**Pipeline item:** LH-69 (SSOT reconciliation — continuation)
**Previous session:** `2026-03-09-ssot-reconciliation-handoff.md` (tasks 1–7 complete, tasks 8–9 were remaining)

---

## What Was Done This Session

### 1. Seed Data Reconciliation (LH-69 task 8)

Replaced the narrative metric list in v4.0 (lines 2074–2171) with authoritative seed tables:

- **metric_catalog:** 74-row table, 19 columns per v4.0 DDL. All column mapping rules applied.
- **source_catalog:** 10-row table, 16 columns per v4.0 DDL.
- **13 metric_id renames applied:** `flows.stablecoin.*` → `stablecoin.*`, `flows.etf.*` → `etf.flows.*`, `meta.*` → `metadata.*`. Domain column now matches metric_id prefix for all 74 rows.

### 2. PostgreSQL Corrective Migration

**File:** `db/migrations/postgres/0004_phase0_corrective.sql`

- DROP + CREATE 6 tables: source_catalog, metric_catalog, metric_lineage, instruments, collection_events, instrument_metric_coverage
- All CREATE TABLE statements match v4.0 DDL character-for-character
- Seeds: 74 metrics, 10 sources, 4 instruments (BTC-USD, ETH-USD, SOL-USD, __market__)
- event_calendar: 5 solo-ops columns + expanded CHECK (14 event_type values)
- Roles: calendar_writer (INSERT/SELECT on event_calendar), risk_writer (INSERT/SELECT on empire.risk_assessment with conditional schema grant)
- All v4.0 indexes created

### 3. ClickHouse Corrective Migration

**File:** `db/migrations/clickhouse/0002_phase0_corrective.sql`

- Rebuilt observations: `Nullable(String)` instrument_id, `Nullable(Float64)` value, `ifNull(instrument_id, '')` in ORDER BY (ClickHouse forbids Nullable in sort keys)
- Rebuilt current_values: `AggregatingMergeTree` + `argMaxState` (was `ReplacingMergeTree`)
- 3 scoped users + ch_admin with settings profiles per v4.0 credential isolation
- Old users (forge_writer, forge_reader) dropped
- `REVOKE ALL ON forge.* FROM default`
- **Placeholder passwords** — must be replaced before execution (see §Deployment below)

### 4. v4.0 Design Doc Updates

- Narrative metric list → authoritative seed table (metric_catalog + source_catalog)
- ClickHouse DDL updated: `ifNull(instrument_id, '')` in ORDER BY for both observations and current_values
- `whale.net_direction` computation formula: added parentheses for correct operator precedence
- Credential isolation file reference: updated to `0002_phase0_corrective.sql`

### 5. Cleanup

**Deleted:**
- `Claude_20260305` — stale pivot-day CLAUDE.md snapshot
- `docs/roadmap.md` — empty file
- `docs/design/.~lock.thread_1_revenue.md#` — lockfile artifact
- `roadmap.md` (root) — stale, Phase 0 marked "pending"

**Moved to `docs/design/Archived/`:**
- `V3_UPGRADE_HANDOFF.md`, `V3_REVIEW_HANDOFF.md`, `V3.1_REVIEW_REPORT.md`, `V3.1_REVIEW_HANDOFF_SESSION2.md`, `V3.1_REVIEW_HANDOFF_SESSION3.md`

**Fixed:**
- CLAUDE.md: `FRG-45` → `LH-06`, pipeline item range note updated
- `design_index.md`: added 5 newly archived files to archive list

---

## Current File State

### `docs/design/` (clean)
```
FromTheBridge_design_v4.0.md    ← SSOT
design_index.md                 ← navigation only
Archived/                       ← 29 files (all historical)
```

### `db/migrations/` (complete for Phase 0)
```
postgres/
  0001_catalog_schema.sql       ← original Phase 0 (superseded by 0004)
  0002_eds_cohesion.sql         ← EDS cohesion tables
  0003_pipeline_triage.sql      ← FRG→LH reclassification
  0004_phase0_corrective.sql    ← corrective migration (NEW)
clickhouse/
  0001_silver_schema.sql        ← original Silver (superseded by 0002)
  0002_phase0_corrective.sql    ← corrective migration (NEW)
```

### `docs/plans/` (session artifacts)
```
2026-03-09-ssot-reconciliation-handoff.md   ← LH-69 handoff (tasks 1–7)
2026-03-09-seed-reconciliation-handoff.md   ← this file (tasks 8–9)
ftb-pipeline-gapfill-sql.sql                ← Phase 2/3/5 items, NOT EXECUTED
```

---

## Deployment Steps (Next Session)

### 1. Generate ClickHouse passwords

```bash
# On bluefin or proxmox — generate 4 passwords
for user in ch_writer ch_export_reader ch_ops_reader ch_admin; do
  openssl rand -base64 32 > "secrets/${user}.txt"
  echo "${user}: $(cat secrets/${user}.txt)"
done
```

Replace `CHANGE_ME_*` placeholders in `0002_phase0_corrective.sql` with generated values.

### 2. Deploy PostgreSQL corrective migration

```bash
cat db/migrations/postgres/0004_phase0_corrective.sql | \
  ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"
```

### 3. Deploy ClickHouse corrective migration

```bash
cat db/migrations/clickhouse/0002_phase0_corrective.sql | \
  ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"
```

### 4. Verify

```sql
-- PostgreSQL
SELECT COUNT(*) FROM forge.metric_catalog;          -- 74
SELECT COUNT(*) FROM forge.source_catalog;          -- 10
SELECT COUNT(*) FROM forge.instruments;             -- 4
SELECT domain, COUNT(*) FROM forge.metric_catalog GROUP BY domain ORDER BY domain;
-- chain 6, defi 11, derivatives 9, etf 5, flows 8, macro 23, metadata 4, price 4, stablecoin 4

-- ClickHouse
SELECT count() FROM system.tables WHERE database = 'forge';  -- 3
SHOW CREATE TABLE forge.observations;   -- Nullable columns, ifNull in ORDER BY
SHOW CREATE TABLE forge.current_values; -- AggregatingMergeTree, argMaxState
SELECT name FROM system.users WHERE name LIKE 'ch_%';  -- 4 users
```

---

## Pipeline Status

### What exists in `bridge.pipeline_items` (expected, based on migrations executed)

- `0003_pipeline_triage.sql`: FRG→LH reclassification. Creates LH-01 through LH-24. Tags ML-*, EDS, product, bridge workstreams. **Execution status unverified against live DB.**

### What has NOT been executed

- `docs/plans/ftb-pipeline-gapfill-sql.sql`: 34 new items (LH-34 through LH-66) covering Phase 2, 3, and 5. Marked "DO NOT EXECUTE without Stephen's approval." **Cross-checked against v3.1, not v4.0.** The items are likely still valid (Phase 2/3/5 structure hasn't changed materially) but should be spot-checked against v4.0 gate criteria before execution.

### Pipeline items that should be updated after deployment

```sql
-- LH-69: SSOT reconciliation complete
UPDATE bridge.pipeline_items
SET status = 'complete', completed_at = NOW(),
    decision_notes = 'v4.0 SSOT complete. Seed reconciliation done. Corrective migrations written (0004 PG, 0002 CH). 13 metric_id renames, 74 metrics in 19-col format, ClickHouse ifNull ORDER BY fix, AggregatingMergeTree current_values, credential isolation with profiles.'
WHERE id = 'LH-69';
```

### Missing pipeline items (gaps identified)

| ID | Title | Phase | Notes |
|----|-------|-------|-------|
| (none assigned) | Deploy corrective migrations | Phase 0 | PG + CH deployment + verification |
| (none assigned) | Execute gap-fill SQL (LH-34–LH-66) | — | Needs v4.0 spot-check first |
| LH-25 through LH-33 | Unassigned ID range | Phase 1 | Phase 1 build items not yet created — will be written during Phase 1 planning |

---

## Known Issues (Not Bugs — Design Decisions to Confirm)

1. **Gap-fill SQL was written against v3.1.** The Phase 2/3/5 items (LH-34–LH-66) reference v3.1 gate criteria. v4.0 tightened some gates (e.g., Phase 3 all 5 pillars non-null). A quick diff of gate criteria between the SQL descriptions and v4.0 would confirm whether any items need updating.

2. **Phase 4 (ML) has no pipeline items.** The gap-fill SQL covers Phase 2, 3, and 5 only. ML-* items exist from the triage but may not cover all v4.0 Phase 4 gate criteria. Phase 4 items should be written during Phase 4 planning.

3. **`0001_catalog_schema.sql` is superseded but not deleted.** It remains as historical record. The `0004` migration drops and recreates all its tables. Running `0001` after `0004` would be a no-op (IF NOT EXISTS guards) but would leave old-format data if the tables somehow survived. This is fine — numbered migration sequence ensures `0004` runs after `0001`.

---

## Blocking Items Before Phase 1

| Item | Status | Action |
|------|--------|--------|
| Corrective migration deployed | NOT DONE | Deploy per §Deployment above |
| Pipeline verified against live DB | NOT DONE | Query `bridge.pipeline_items` to confirm 0003 executed |
| Gap-fill SQL approved + executed | NOT DONE | Stephen approval required |
| Polygon.io integration design | NOT DONE | Blocking per CLAUDE.md |
| LH-69 pipeline item closed | NOT DONE | UPDATE after deployment |
