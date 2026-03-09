# FromTheBridge — Solo Operator Operations & Calendar Integration Design

**Date:** 2026-03-08
**Status:** Approved
**Scope:** EDS-31 (calendar integration) + FTB Solo Operator Operations section
**Authority:** Architect-approved design session

---

## ITEM 1: CALENDAR SCHEMA EXTENSION (EDS-31)

### Schema Change

Additive ALTER TABLE on `forge.event_calendar`. No existing rows affected — all 137
FOMC rows receive defaults (`system_id='ftb'`, `severity='info'`).

```sql
ALTER TABLE forge.event_calendar
    ADD COLUMN IF NOT EXISTS system_id      TEXT NOT NULL DEFAULT 'ftb',
    ADD COLUMN IF NOT EXISTS severity       TEXT CHECK (severity IN ('info','yellow','red')) DEFAULT 'info',
    ADD COLUMN IF NOT EXISTS metadata       JSONB,
    ADD COLUMN IF NOT EXISTS expires_at     DATE,
    ADD COLUMN IF NOT EXISTS recurring_rule TEXT;

-- Expand event_type CHECK constraint
ALTER TABLE forge.event_calendar
    DROP CONSTRAINT IF EXISTS event_calendar_event_type_check;
ALTER TABLE forge.event_calendar
    ADD CONSTRAINT event_calendar_event_type_check CHECK (event_type IN (
        -- Market structure (existing)
        'fomc', 'cpi_release', 'nfp_release', 'gdp_release',
        'futures_expiry', 'options_expiry', 'token_unlock',
        -- Operational (new)
        'maintenance', 'hardware', 'milestone', 'recurring',
        'procurement', 'upgrade', 'tos_audit'
    ));

CREATE INDEX IF NOT EXISTS idx_event_calendar_system_date
    ON forge.event_calendar (system_id, event_date);
```

### Credential Isolation

```sql
CREATE ROLE calendar_writer WITH LOGIN PASSWORD '<generated>';
GRANT USAGE ON SCHEMA forge TO calendar_writer;
GRANT INSERT, SELECT ON forge.event_calendar TO calendar_writer;
GRANT USAGE ON SEQUENCE forge.event_calendar_event_id_seq TO calendar_writer;
-- No other forge.* grants.
```

**Documented exception (both CLAUDE.md files):** "EDS may INSERT to
`forge.event_calendar` only, via `calendar_writer` role. This is the sole exception
to the 'EDS never writes to forge.*' rule. No other `forge.*` table is accessible to
EDS credentials."

**Ownership:** FTB creates both `calendar_writer` and `risk_writer` roles during
Phase 1 infrastructure setup. FTB owns the PostgreSQL credential lifecycle for
`forge.*` schema. EDS receives the `calendar_writer` connection string as a
deployment config value.

### EDS Integration Contract

EDS Dagster sensors write events via direct INSERT using `calendar_writer`:

```python
{
    "event_type": str,           # from expanded CHECK
    "event_date": date,          # when it should happen
    "description": str,          # human-readable, actionable
    "source": str,               # 'dagster:capacity_asset', 'dagster:hardware_health', etc.
    "system_id": str,            # 'eds' or 'ftb' or 'shared'
    "severity": str | None,      # 'info', 'yellow', 'red'
    "metadata": dict | None,     # {"runbook_ref": "RUNBOOK-02", "pipeline_item": "EDS-14"}
    "recurring_rule": str | None,
    "expires_at": date | None
}
```

### Expiry Cleanup

Dagster scheduled asset — `shared_ops.calendar_cleanup` — runs daily at 03:00 UTC.
FTB-owned, uses `forge_writer`.

```sql
DELETE FROM forge.event_calendar
WHERE expires_at IS NOT NULL AND expires_at < CURRENT_DATE;
```

---

## ITEM 2: FTB SOLO OPERATOR OPERATIONS

### Design Principle: Shared Dagster, Separate Asset Groups

EDS established Dagster as the single pane. FTB shares the same Dagster instance
(port 3010). Asset groups separated by namespace:

| Namespace | Owner | What it covers |
|-----------|-------|----------------|
| `eds_ops.*` | EDS | Node health, EDS adapter health, sync pipeline |
| `ftb_ops.*` | FTB | Adapter freshness, export pipeline, layer health (phased) |
| `shared_ops.*` | Shared | Capacity, hardware SMART, risk board, calendar cleanup, alert dispatch, sync boundary, container health |

**shared_ops ownership:** FTB's Dagster instance materializes all `shared_ops.*`
assets. EDS does not materialize them. This prevents duplicate writes. EDS health
assets (`eds_ops.*`) contribute risk rows to `empire.risk_assessment` via
`risk_writer`, but the risk board rollup asset itself is FTB-owned.

**EDS doc propagation:** EDS_design_v1.1.md references `eds_ops.capacity`,
`eds_ops.hardware`, `eds_ops.risk_board`. These become `shared_ops.*`. Pipeline
item: `LH-XX: Propagate shared_ops.* rename to EDS_design_v1.1.md`.

### FTB Failure Modes

FTB's dominant failure mode is **silent data degradation**. Unlike EDS where a node
going down is binary (no blocks), FTB's sources can return partial data, adapters
can half-succeed, and the pipeline continues with gaps. Health assets must detect
degradation, not just outages.

| Failure Mode | Layer | Silent? | Cascading? |
|-------------|-------|---------|------------|
| Source API goes down | 0 | Yes | Yes — adapter stops, Silver stale, Gold stale, Marts stale |
| Adapter succeeds but collects partial data | 2 | Yes | Yes — same cascade, harder to detect |
| Bronze write succeeds, Silver write fails | 2-4 | Yes | Yes — Bronze has data, Silver doesn't |
| ClickHouse merge lag | 4 | Yes | Yes — export reads stale/duplicate data |
| Export job silently skips rows | 4-5 | Yes | Yes — Gold incomplete, Marts built on gaps |
| MinIO unreachable | 3/5 | Yes | Yes — entire pipeline stalls |
| Dead letter rate spikes | 2-4 | Yes | Partial — coverage drops, features sparse |
| empire_to_forge_sync delivers partial data | EDS/FTB boundary | Yes | Yes — Silver inherits gaps silently |

### FTB Health Asset Groups (Phase 1)

Phase 1 builds two health asset families. Additional families emerge when the layers
they monitor exist.

#### Adapter Freshness (`ftb_ops.adapter_health`)

One asset per configured source. Materializes by querying ClickHouse via
`ch_ops_reader`:

| Field | Source | Alert Condition |
|-------|--------|-----------------|
| `last_observation_at` | `MAX(observed_at)` from `forge.observations` for source | Exceeds `cadence_hours x 2` from source_catalog |
| `observations_24h` | Count in last 24h | Zero (outage) or <50% of expected |
| `dead_letter_24h` | Count from `forge.dead_letter` for source | >10 |
| `metric_coverage_pct` | Distinct metric_ids observed / expected for source | <80% |
| `instrument_coverage_pct` | Distinct instrument_ids / expected (instrument-scoped sources) | <80% |

**Freshness policy:** Matches source cadence from `forge.source_catalog`.

**Why coverage %:** Degradation detector. An adapter can collect 30% of instruments,
write them, and report success. Coverage % catches that.

#### Export Pipeline (`ftb_ops.export_health`)

Single asset for the Silver-to-Gold export — FTB's most critical single point of failure:

| Field | Source | Alert Condition |
|-------|--------|-----------------|
| `last_export_at` | Dagster run history | >2h ago (1h cadence + 1h tolerance) |
| `rows_exported_last_run` | Export run metadata | Zero |
| `merge_lag_seconds` | `system.merges` table | >300s |
| `unmerged_parts` | `system.parts` for forge.observations | >50 |
| `gold_iceberg_snapshot_count` | MinIO metadata | Growing without compaction |

### Shared Health Assets

#### Sync Boundary (`shared_ops.sync_health`)

Spans EDS/FTB boundary. FTB-owned materialization:

| Field | Source | Alert Condition |
|-------|--------|-----------------|
| `last_sync_at` | Dagster run history | >12h ago |
| `rows_synced_last_run` | Sync metadata | Zero |
| `coverage_delta` | Metrics in EDS vs metrics in FTB Silver | Delta >0 |
| `dead_letter_rate` | Sync dead letters / total synced | >1% |

#### Capacity (`shared_ops.capacity`)

Already designed by EDS. Same asset, FTB-owned materialization. Covers all proxmox
volumes: ClickHouse, PostgreSQL, MinIO, Dagster metadata.

#### Hardware (`shared_ops.hardware`)

Already designed by EDS. SMART data for proxmox drives. FTB-owned materialization.

#### Container Health (`shared_ops.container_health`)

All Docker containers on proxmox. `docker ps --format` parsed into health rows.
Catches restart loops, OOM kills, unexpected exits.

### Risk Board Integration

FTB writes to `empire.risk_assessment` via `risk_writer`:

```sql
CREATE ROLE risk_writer WITH LOGIN PASSWORD '<generated>';
GRANT INSERT, SELECT ON empire.risk_assessment TO risk_writer;
-- Both EDS and FTB health assets use risk_writer.
```

**Documented exception (both CLAUDE.md files):** "FTB may INSERT to
`empire.risk_assessment` only, via `risk_writer` role."

**FTB risk categories:**

| Category | Source Assets | Example |
|----------|-------------|---------|
| `adapter_freshness` | `ftb_ops.adapter_health` | "Coinalyze: no observations in 18h (cadence: 8h)" |
| `data_quality` | `ftb_ops.data_quality` (Phase 2) | "Dead letter rate 2.1% (threshold: 1%)" |
| `export_pipeline` | `ftb_ops.export_health` | "ClickHouse merge lag 420s. 73 unmerged parts." |
| `sync_boundary` | `shared_ops.sync_health` | "EDS sync: 0 rows last run" |
| `storage` | `shared_ops.capacity` | "MinIO bronze-hot: 78% full. Exhaustion April 22." |
| `container` | `shared_ops.container_health` | "empire_clickhouse: 3 OOM restarts in 24h" |

Every risk row includes `mitigation_action` (concrete command or runbook ref) and
`projected_escalation_date` (when yellow becomes red). No "investigate" actions.

### Alert Routing

Shared with EDS. Same operator, same channel.

**Interface contract (mechanism picked during Phase 1 build):**

```python
class AlertDispatch:
    def send_red(self, system_id: str, risk_id: str,
                 description: str, mitigation: str) -> None: ...
    def send_daily_digest(self, risks: list[RiskRow]) -> None: ...
    # Implementation pluggable: Telegram, Ntfy, Pushover. Picked during build.
```

**Routing rules:**

| Severity | Action |
|----------|--------|
| Red | Push notification immediately. Max 2-3/week normal ops. |
| Yellow | Daily digest. All items across EDS + FTB, grouped by system_id. |
| Green | Silent. Dagster dashboard only. |

**FTB-specific red triggers:**

| Condition | Rationale |
|-----------|-----------|
| Silver-to-Gold export failed 3 consecutive times | Single point of failure |
| ClickHouse merge lag >600s | Data correctness at risk |
| >3 adapters simultaneously returning zero data | Systemic issue |
| MinIO unreachable | Bronze and Gold both down |
| Dead letter rate >5% | Data quality emergency |

**Quiet hours:** Configurable. Red held until morning unless critical pattern
(container crash loop, disk >95%, all adapters down). Critical list: 3-5 conditions,
explicit.

### Rule 2 Reframe

**Current:** "The only process that reads ClickHouse is the Dagster export asset."

**Proposed:** "No application service reads ClickHouse. Only Dagster assets read
ClickHouse — the export asset (`ch_export_reader`) for Silver-to-Gold data flow,
and ops health assets (`ch_ops_reader`) for operational monitoring. Both are
Dagster-managed, both use dedicated scoped credentials."

### Phase 1 Runbooks

Written during adapter build, tested before Phase 1 gate:

| # | Runbook | Trigger |
|---|---------|---------|
| FTB-01 | Adapter data gap backfill | Adapter freshness >2x cadence |
| FTB-02 | Dead letter triage and reprocessing | Dead letter rate >1% or spike >10/24h |
| FTB-03 | Source API outage response | Source returns errors for >2 collection cycles |
| FTB-04 | Bronze write failure recovery | MinIO unreachable or partition write fails |
| FTB-05 | Silver write failure recovery | ClickHouse INSERT fails or rejects rows |
| FTB-06 | ClickHouse merge lag resolution | Unmerged parts >50 or merge lag >300s |
| FTB-07 | Export job failure investigation | Export fails or exports zero rows |
| FTB-08 | Sync boundary gap resolution | EDS sync delivers partial or zero data |

**Runbook structure (per entry):** Severity, Detection, Impact, Immediate action,
Resolution steps (exact commands), Verification, Post-mortem. Stored in
`docs/runbooks/`, versioned with the codebase.

### Future Phase Gate Criteria (one-liners)

- **Phase 2 gate** requires ops runbooks for dbt model failures, Gold Iceberg
  compaction, and feature null state investigation. `ftb_ops.layer_health` and
  `ftb_ops.data_quality` assets operational.
- **Phase 4 gate** requires ML ops runbooks for model retraining and feature drift.
  `ftb_ops.ml_model_health` operational for all shadow models.
- **Phase 5 gate** requires serving and tunnel runbooks. `ftb_ops.serving_health`
  and `ftb_ops.tunnel_health` operational. Alert dispatch mechanism deployed.
  End-to-end: red alert fires within 5 minutes of simulated failure.

### Phase 1 Gate Additions

Added to existing Phase 1 gate criteria:

- `ftb_ops.adapter_health` materializing for all configured sources
- `ftb_ops.export_health` materializing
- `shared_ops.sync_health` operational
- FTB-01 through FTB-08 runbooks written and tested
- Calendar schema extension deployed
- `calendar_writer` and `risk_writer` roles created
- `ch_ops_reader` role created with SELECT on `forge.observations`,
  `forge.dead_letter`, and `system.merges`/`system.parts`

---

## PIPELINE ITEMS

| ID | Description | Trigger | System |
|----|-------------|---------|--------|
| EDS-31 | FTB calendar schema extension and integration | Phase 1 start | Shared |
| LH-25 | Propagate shared_ops.* rename to EDS_design_v1.1.md | Next EDS session | EDS |
| LH-26 | Create calendar_writer, risk_writer, ch_ops_reader roles | Phase 1 infra setup | FTB |
| LH-27 | Deploy calendar schema ALTER TABLE migration | Phase 1 infra setup | FTB |
| LH-28 | Build ftb_ops.adapter_health Dagster assets | Phase 1 adapter build | FTB |
| LH-29 | Build ftb_ops.export_health Dagster asset | Phase 1 export build | FTB |
| LH-30 | Build shared_ops.sync_health Dagster asset | Phase 1 sync build | FTB |
| LH-31 | Build shared_ops.calendar_cleanup Dagster asset | Phase 1 infra setup | FTB |
| LH-32 | Write and test FTB-01 through FTB-08 runbooks | Phase 1 build | FTB |
| LH-33 | Update both CLAUDE.md files with calendar/risk exceptions and Rule 2 reframe | Phase 1 start | Shared |

---

## CROSS-REFERENCES

| Document | Change Required |
|----------|----------------|
| `FromTheBridge/CLAUDE.md` | Add calendar_writer exception, risk_writer exception, Rule 2 reframe |
| `EmpireDataServices/CLAUDE.md` | Add calendar_writer exception (EDS side) |
| `EDS_design_v1.1.md` | Rename eds_ops.capacity/hardware/risk_board to shared_ops.* |
| `FromTheBridge_design_v3.1.md` | Add Solo Operator Operations section (derived from this doc) |
| `thread_6_build_plan.md` | Add Phase 1 gate criteria for ops assets and runbooks |

---

*Design session 2026-03-08. Approved by architect.*
