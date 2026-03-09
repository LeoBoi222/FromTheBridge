# thread_6_build_plan.md
## FromTheBridge — Build Plan
## Empire Architecture v2.0

**Date:** 2026-03-05
**Status:** Authoritative. Supersedes all prior thread_6 versions.
**Owner:** Stephen (architect, sole operator)
**Depends on:** thread_infrastructure.md · thread_4_data_universe.md ·
thread_2_signal.md · thread_3_features.md

> Phase gates are hard pass/fail. No phase begins until the previous gate
> passes and the architect confirms. Self-certification is not permitted.
> Every criterion below is runnable by someone who has never seen the system.

---

## BUILD SEQUENCE

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
Schema     Collection  Features  EDSx      ML        Serving   Product
```

---

## PHASE SCOPE DEFINITIONS

### Phase 0 — Schema Foundation

**What is built (concrete deliverables):**
- PostgreSQL forge schema: all 12 catalog tables created and seeded
  (assets, asset_aliases, venues, instruments, source_catalog,
  metric_catalog, metric_lineage, event_calendar, supply_events,
  adjustment_factors, collection_events, instrument_metric_coverage)
- ClickHouse forge database: observations, dead_letter, current_values
  created with correct engines and ordering keys
- MinIO: bronze and gold buckets initialized
- ClickHouse DDL migration file: `db/migrations/clickhouse/0001_silver_schema.sql`
- PostgreSQL catalog seed migration: `db/migrations/0001_phase0_schema.sql`

**Entry condition:** None. First phase.

**Explicitly deferred scope:**
- Dagster service definition — Phase 1. No orchestration needed before
  adapters exist.
- Great Expectations setup — Phase 1. Validation rules derive from
  metric_catalog; catalog must be complete and verified first.
- Any adapter or collection code — Phase 1.
- Silver → Gold export asset — Phase 2.

**Thread section references:**
- thread_infrastructure.md: ADR-001 (ClickHouse), ADR-002 (Iceberg),
  ADR-003 (MinIO), ADR-007 (PostgreSQL), Three Hard Rules,
  Physical Deployment Topology, Cold-Start Sequence
- thread_4_data_universe.md: All table DDL, ClickHouse schema,
  Phase 0 gate checklist, PIT model

**Estimated duration:** 3–5 days
**Variance driver:** ClickHouse DDL defects discovered during write/read
round-trip testing. ReplacingMergeTree deduplication behavior under the
FINAL keyword must be verified before Phase 1 begins — defects here
invalidate every downstream correctness assumption.

---

### Phase 1 — Data Collection

**What is built (concrete deliverables):**
- Dagster Docker services added to docker-compose.yml and healthy:
  empire_dagster_daemon, empire_dagster_webserver, empire_dagster_code
- Dagster asset graph built from metric_catalog + metric_lineage at startup
- Adapters for all 10 v1 sources, each implementing the full
  10-responsibility contract (auth, rate limiting, pagination, schema
  normalization, timestamp normalization, unit normalization, validation,
  extreme value handling, idempotency, observability)
- Bronze Iceberg writes to MinIO for all 10 sources
- Great Expectations validation at Bronze → Silver boundary; GE rules
  derived from metric_catalog definitions
- Silver writes to ClickHouse for all 10 sources
- Dead letter logging for all rejected observations
- BLC-01 rsync pull routine from Server2 to proxmox landing directory
- Dagster file sensor for BLC-01 JSONL landing directory
- NAS backup job for MinIO data (Bronze + Gold)
- NAS backup job for Dagster metadata database
- BAMLH0A0HYM2 series added to FRED adapter (macro.credit.hy_oas —
  pre-condition for this phase per thread_4 known gaps)

**Entry condition:** Phase 0 gate passed. Architect confirmed.

**Explicitly deferred scope:**
- Silver → Gold export asset — Phase 2. Silver data accumulates but is
  not exported to Gold until the export asset is built.
- Feature compute — Phase 2.
- EDSx signal scoring — Phase 3.
- BLC-01 ToS audit — Phase 6. Internal use permitted; redistribution
  blocked by source_catalog redistribution=NULL.

**Thread section references:**
- thread_infrastructure.md: ADR-001 (ClickHouse write patterns),
  ADR-002 (Iceberg Bronze), ADR-003 (MinIO), ADR-005 (Dagster),
  Physical Deployment Topology, BLC-01 Data Path, Known Infrastructure Gaps
- thread_4_data_universe.md: source_catalog, metric_catalog,
  metric_lineage, collection_events, instrument_metric_coverage DDL;
  adapter registration procedure

**Estimated duration:** 2–3 weeks
**Variance driver:** Migration adapter bugs in legacy Forge data
normalization; DeFiLlama backfill rate limits during historical ingestion.
Tiingo must be the first adapter completed and verified — it provides
spot price data that other adapters depend on for unit normalization.

---

### Phase 2 — Feature Engineering

**What is built (concrete deliverables):**
- Silver → Gold incremental export Dagster asset (6h cadence)
- Gold Iceberg tables on MinIO readable by DuckDB
- dbt project with dbt-duckdb adapter for all SQL transform features
- forge_compute Python service for rolling window, cross-sectional,
  and ML-assembly features
- Feature catalog entries for all computed features in metric_catalog
  before any feature compute runs (enforced by forge_compute startup check)
- All three null states implemented and tested:
  INSUFFICIENT_HISTORY, SOURCE_STALE, METRIC_UNAVAILABLE
- Breadth score computation (deterministic formula, fixed weights)
- Computation order enforced: A → C → B → F → G → D → E
  (per thread_3_features.md locked decision)

**Entry condition:** Phase 1 gate passed. Architect confirmed.
Minimum 7 days of Silver data collected across all 10 sources before
feature compute begins (rolling window features require history).

**Explicitly deferred scope:**
- EDSx pillar scoring — Phase 3. Features are inputs; pillar scoring
  is a separate layer.
- ML feature assembly pipeline — Phase 4. Features computed here but
  ML training pipeline uses Phase 4 scope.
- Arrow Flight serving endpoint — Phase 5.

**Thread section references:**
- thread_infrastructure.md: ADR-002 (Gold Iceberg), ADR-004 (DuckDB),
  ADR-006 (dbt + forge_compute), Three Hard Rules (Rule 1, Rule 2)
- thread_3_features.md: PIT constraint, null state definitions,
  computation order, breadth score formula, feature catalog requirements
- thread_4_data_universe.md: instrument_metric_coverage, collection_events

**Estimated duration:** 2–3 weeks
**Variance driver:** PIT violations discovered in audit; rolling window
edge cases at instrument collection boundaries. Every feature must pass
the PIT audit before Phase 3 begins — a violation here produces
look-ahead bias in every downstream signal.

---

### Phase 3 — EDSx Signal

**What is built (concrete deliverables):**
- EDSx-02 (Trend/Structure) pillar: full scoring pipeline operational
- EDSx-03 R3 (Liquidity/Flow) pillar: full scoring pipeline operational
  (already landed; verified against new feature layer)
- Valuation pillar (REM-21): scoring pipeline built
- Tactical Macro pillar (REM-22/23): scoring pipeline built
- Structural Risk pillar (REM-24): scoring pipeline built,
  consuming BGeometrics metrics (MVRV, SOPR, NUPL, Puell)
- EDSx confidence computation: signals_computed / signals_available
  for each instrument, value in [0, 1]
- Regime classification: risk_on / risk_off / neutral
- EDSx output contract conformant with Layer 6 signal schema

**Entry condition:** Phase 2 gate passed. Architect confirmed.
Feature catalog verified complete — all pillar inputs have feature
catalog entries.

**Explicitly deferred scope:**
- ML track — Phase 4. EDSx is the production signal through all of
  Phase 4 shadow period. No ML output in composite signal until
  Phase 5.
- Synthesis layer — Phase 5.
- Magnitude field — Phase 5 (ML track only per design).

**Thread section references:**
- thread_2_signal.md: EDSx architecture decisions (M1, M3, M5, M9),
  five pillars, confidence formula, regime engine, graduation criteria,
  Layer 6 output contract
- thread_3_features.md: breadth score, PIT constraint

**Estimated duration:** 1–2 weeks
**Variance driver:** Rule calibration for Valuation and Structural Risk
pillars; regime classification edge cases at quadrant boundaries.

---

### Phase 4 — ML Track (Shadow)

**What is built (concrete deliverables):**
- Walk-forward training pipeline for all 5 domain models:
  M-Macro, M-Derivatives, M-Flows, M-DeFi, M-Synthesis
- 14-day volume-adjusted label generation (PIT-correct; labels used
  only in training pipeline, never served)
- Isotonic calibration applied to each model post-training
- All 5 graduation criteria evaluated on OOS data for each model:
  (1) AUC-ROC ≥ 0.56 on OOS data, (2) calibration ECE < 0.05,
  (3) no single feature > 40% importance, (4) stable predictions
  (std dev across 5 OOS folds < 0.15), (5) no cliff between
  OOS evaluation and first 30 shadow days
- All 5 models deployed to shadow mode: outputs logged but not
  used in composite signal
- Shadow evaluation Dagster asset running on schedule
- Shadow start date recorded in system metadata before gate is evaluated

**Entry condition:** Phase 3 gate passed. Architect confirmed.
Minimum 12 months of Gold layer data available for OOS evaluation
window. Shadow start date must be recorded before gate evaluation
begins — the gate cannot pass before shadow_start + 30 calendar days.

**Explicitly deferred scope:**
- ML output in composite signal — Phase 5. ML is shadow-only through
  this entire phase. EDSx remains the production signal.
- Magnitude field — Phase 5 (ML track output).
- Synthesis layer — Phase 5.

**Thread section references:**
- thread_2_signal.md: ML architecture decisions (D6, D41),
  five ML domain models, graduation criteria, shadow period rules,
  prediction horizon, label generation, calibration methodology,
  synthesis architecture

**Estimated duration:** 3–4 weeks
**Variance driver:** Graduation criteria not met on first training pass.
If any model fails graduation, the training pipeline is debugged and
retrained before shadow mode begins. Shadow period extension applies
if shadow accuracy evaluation fails consistency check against OOS.

---

### Phase 5 — Signal Synthesis and Serving

**What is built (concrete deliverables):**
- Synthesis logic: agreement check, confidence-weighted combination
  (0.5/0.5 EDSx/ML default), magnitude (ML track), regime context,
  null handling for missing pillar scenarios
- FastAPI serving layer: /v1/signals, /v1/timeseries endpoints,
  API key authentication with tier enforcement, redistribution filter,
  rate limiting
- Arrow Flight endpoint for bulk timeseries delivery
- Push delivery: webhook and Telegram
- Full end-to-end provenance trace: signal → feature values →
  metric observations → collection event → source
- Staleness propagation: SOURCE_STALE flag on synthesis output
  when upstream metric is stale

**Entry condition:** Phase 4 gate passed. All 5 ML models graduated.
Shadow period ≥ 30 days complete. Shadow evaluation passed.
Architect confirmed.

**Explicitly deferred scope:**
- Health monitoring dashboards — Phase 6.
- Methodology documentation — Phase 6.
- ToS audit — Phase 6.
- First customer delivery — Phase 6.

**Thread section references:**
- thread_2_signal.md: synthesis architecture, Layer 6 output contract,
  confidence-weighted combination, null handling, serving architecture
- thread_infrastructure.md: ADR-004 (DuckDB for serving), Layer 8
  (Serving) specification, Three Hard Rules (Rule 2 — serving has
  no ClickHouse credentials)

**Estimated duration:** 1–2 weeks
**Variance driver:** Webhook reliability under network interruption;
redistribution filter correctness across all restricted sources.

---

### Phase 6 — Productization

**What is built (concrete deliverables):**
- Health monitoring: collection health, coverage tracking, signal
  health, infrastructure health — all diagnosable via Dagster UI
  within 15 minutes of failure
- Methodology documentation: metric catalog methodology fields
  complete, EDSx methodology document, ML methodology document,
  data quality policy
- ToS audit completed for all 10 sources — documented with audit
  date, decision, and enforcement action per source
- Redistribution filter verified in production against all restricted
  sources before first customer delivery
- First customer delivery: direct engagement, real pricing,
  invoice issued, signal delivered on schedule

**Entry condition:** Phase 5 gate passed. Architect confirmed.
ToS audit must complete before any external data product ships —
this is a hard sequencing constraint, not a background task.

**Explicitly deferred scope:**
- Dashboard UI — not in v1. Signal product only.
- Billing infrastructure — not in v1. Manual invoicing.
- Index/benchmark licensing — v2. Trigger: methodology documented
  and ToS audited for all constituent sources.
- SoSoValue redistribution — v2. Non-commercial confirmed;
  paid tier evaluation or source replacement at v2 milestone.

**Thread section references:**
- thread_infrastructure.md: Managed Service Migration Triggers
  (all four components), Resource Boundaries and Scaling Thresholds,
  Source Catalog Appendix
- thread_2_signal.md: graduation criteria, serving architecture
- thread_4_data_universe.md: source_catalog redistribution field,
  known gaps table

**Estimated duration:** 1–2 weeks
**Variance driver:** ToS audit findings requiring source exclusion or
serving layer changes. A finding that blocks redistribution for a
previously assumed-open source requires API changes before first
customer delivery.

---

## PHASE GATES

## PRIOR GATE CRITERIA — ALL VOID

The gate criteria in the previous version of this document were written
against a PostgreSQL + TimescaleDB stack. That stack no longer exists.
Every prior criterion is void as of 2026-03-05. The criteria below are
written against the deployed stack: ClickHouse (Silver), Apache Iceberg
on MinIO (Bronze, Gold), Dagster, PostgreSQL (catalog only).

---

### Phase 0 Gate — Schema Foundation

Phase 1 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** All 12 forge catalog tables exist in PostgreSQL
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'forge' ORDER BY table_name;"
```
**Expected result:** 12 rows returned:
adjustment_factors, asset_aliases, assets, collection_events,
event_calendar, instruments, instrument_metric_coverage,
metric_catalog, metric_lineage, source_catalog, supply_events, venues
**Failure meaning:** Seed migration did not complete or dropped tables.
**Failure action:** Re-run `db/migrations/0001_phase0_schema.sql` from scratch.
Inspect migration output for DDL errors before re-running.

---

**Criterion:** Source catalog contains exactly 10 rows
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT COUNT(*) FROM forge.source_catalog;"
```
**Expected result:** `count = 10`
**Failure meaning:** Source catalog seed incomplete or contains
duplicate/extra rows from prior failed migration attempts.
**Failure action:** Inspect `SELECT canonical_name FROM forge.source_catalog ORDER BY canonical_name`.
Compare against the 10 sources in thread_infrastructure.md Appendix.
Delete spurious rows or re-seed.

---

**Criterion:** Metric catalog contains ≥ 50 active metric rows
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT COUNT(*) FROM forge.metric_catalog WHERE status = 'active';"
```
**Expected result:** `count ≥ 50`
**Failure meaning:** Metric catalog seed is incomplete. Features,
EDSx pillars, and ML models all depend on metric_catalog rows existing
before they can be registered.
**Failure action:** Re-run metric_catalog seed section of the migration.
Inspect for INSERT errors in migration output.

---

**Criterion:** Redistribution flags are correctly set to false for
SoSoValue and CoinMetrics
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT canonical_name, redistribution, tos_status
        FROM forge.source_catalog
        WHERE canonical_name IN ('sosovalue', 'coinmetrics')
        ORDER BY canonical_name;"
```
**Expected result:** 2 rows. Both have `redistribution = false`.
SoSoValue has `tos_status = 'restricted'`. CoinMetrics has
`tos_status = 'unaudited'`.
**Failure meaning:** Serving layer will incorrectly allow redistribution
of restricted data. This is a pre-customer legal risk.
**Failure action:** UPDATE source_catalog to set correct values. Do not
proceed to Phase 1 with incorrect redistribution flags.

---

**Criterion:** No time series columns exist in PostgreSQL forge schema
(Rule 3 — thread_infrastructure.md)
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT column_name, table_name
        FROM information_schema.columns
        WHERE table_schema = 'forge'
          AND column_name IN
            ('observed_at', 'value', 'value_numeric', 'ingested_at');"
```
**Expected result:** Zero rows returned.
**Failure meaning:** Rule 3 violated. Time series data has been placed
in PostgreSQL. Downstream consumers may silently read stale or
incorrect data.
**Failure action:** Identify and DROP the violating columns or tables.
Do not proceed until this returns zero rows.

---

**Criterion:** ClickHouse forge database contains exactly three objects
```
Check:
  docker exec empire_clickhouse clickhouse-client \
    --query "SHOW TABLES FROM forge;"
```
**Expected result:** Three rows: `dead_letter`, `observations`,
`current_values`
**Failure meaning:** ClickHouse DDL migration did not complete or
created additional unexpected objects.
**Failure action:** Run `db/migrations/clickhouse/0001_silver_schema.sql`
and inspect for errors. DROP unexpected objects if present.

---

**Criterion:** observations table uses correct engine and ordering key
```
Check:
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT engine, sorting_key
             FROM system.tables
             WHERE database = 'forge' AND name = 'observations';"
```
**Expected result:** `engine = ReplacingMergeTree`,
`sorting_key = metric_id, instrument_id, observed_at`
**Failure meaning:** Wrong engine means deduplication will not work.
Wrong sorting key means export scans will not use partition pruning.
Both produce silently incorrect PIT query results.
**Failure action:** DROP and recreate observations table with correct
DDL. Do not proceed — all Phase 1 Silver writes depend on this.

---

**Criterion:** ClickHouse write/read round-trip succeeds with FINAL
```
Check:
  docker exec empire_clickhouse clickhouse-client --multiquery --query "
    INSERT INTO forge.observations VALUES (
      'spot.price.close_usd', 'BTC', 'tiingo',
      now64(), now64(), 50000.0, 1
    );
    SELECT metric_id, instrument_id, value
    FROM forge.observations FINAL
    WHERE metric_id = 'spot.price.close_usd' AND instrument_id = 'BTC'
    ORDER BY observed_at DESC LIMIT 1;
  "
```
**Expected result:** One row returned with `value = 50000.0`
**Failure meaning:** ClickHouse cannot accept writes or FINAL queries
are not deduplicating correctly. All adapter writes and PIT export
queries are broken.
**Failure action:** Verify ClickHouse container is healthy
(`curl http://localhost:8123/ping` returns `Ok.`). Check ClickHouse
error logs: `docker logs empire_clickhouse`. Investigate engine
configuration.

---

**Criterion:** dead_letter write succeeds and is queryable
```
Check:
  docker exec empire_clickhouse clickhouse-client --multiquery --query "
    INSERT INTO forge.dead_letter VALUES (
      'tiingo', 'spot.price.close_usd', 'BTC',
      '{\"raw\": \"test\"}', 'Test rejection', 'RANGE_VIOLATION',
      now64(), now64()
    );
    SELECT count() FROM forge.dead_letter WHERE source_id = 'tiingo';
  "
```
**Expected result:** `count = 1`
**Failure meaning:** Rejected observations cannot be logged. Validation
rejections will be silently dropped — violating the "nothing silently
dropped" requirement from thread_5 locked decisions.
**Failure action:** Inspect dead_letter DDL for schema mismatch.
Verify column names match INSERT statement.

---

**Criterion:** current_values materialized view reflects inserted test row
```
Check:
  docker exec empire_clickhouse clickhouse-client \
    --query "
      SELECT argMaxMerge(latest_value) AS value
      FROM forge.current_values
      WHERE metric_id = 'spot.price.close_usd'
        AND instrument_id = 'BTC'
      GROUP BY metric_id, instrument_id;
    "
```
**Expected result:** `value = 50000.0`
**Failure meaning:** AggregatingMergeTree view is not updating on insert.
The current_values hot read path is broken — adapters that read current
values for normalization will get stale or missing data.
**Failure action:** Verify current_values DDL uses AggregatingMergeTree
with argMaxState. Force a merge: `OPTIMIZE TABLE forge.current_values FINAL`.
Re-run check.

---

**Criterion:** PIT revision query returns revised value after update
```
Check:
  # Step 1: record the observed_at of the test row
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT observed_at FROM forge.observations
             WHERE metric_id = 'spot.price.close_usd'
               AND instrument_id = 'BTC'
             ORDER BY observed_at DESC LIMIT 1;"
  # Step 2: insert a revision at the same observed_at with data_version = 2
  docker exec empire_clickhouse clickhouse-client \
    --query "INSERT INTO forge.observations VALUES (
      'spot.price.close_usd', 'BTC', 'tiingo',
      [observed_at from step 1], now64(), 51000.0, 2
    );"
  # Step 3: query FINAL — must return only the revised value
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT value FROM forge.observations FINAL
             WHERE metric_id = 'spot.price.close_usd'
               AND instrument_id = 'BTC'
             ORDER BY observed_at DESC LIMIT 1;"
```
**Expected result:** Step 3 returns `value = 51000.0` (revised value,
not original 50000.0). ReplacingMergeTree keeps highest data_version.
**Failure meaning:** Revision handling is broken. Source corrections
will not propagate. Historical data will contain stale values
indefinitely.
**Failure action:** Verify ReplacingMergeTree(data_version) is the
exact engine declaration — the version column argument is required.
Force OPTIMIZE TABLE forge.observations FINAL and re-run.

---

**Criterion:** MinIO bronze and gold buckets exist and are accessible
```
Check:
  curl -s http://localhost:9001/minio/health/live
  mc alias set local http://localhost:9001 \
    $MINIO_ACCESS_KEY $MINIO_SECRET_KEY
  mc ls local/
```
**Expected result:** Health check returns HTTP 200. `mc ls` shows
at least two buckets: `bronze` and `gold`.
**Failure meaning:** MinIO is not running or buckets were not
initialized. Bronze adapter writes will fail immediately in Phase 1.
**Failure action:** Verify MinIO container: `docker compose ps empire_minio`.
Create buckets: `mc mb local/bronze local/gold`. Re-run check.

---

**Phase 0 gate passes when all 13 criteria above return expected results.
Record exact output of each check in the Phase 0 completion report.
Phase 1 does not begin until architect confirms.**

---

### Phase 1 Gate — Data Collection

Phase 2 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** All three Dagster services are running and healthy
```
Check:
  docker compose ps empire_dagster_daemon \
    empire_dagster_webserver empire_dagster_code
  curl -s http://localhost:3010 | head -1
```
**Expected result:** All three containers show `Up` status.
HTTP GET to port 3010 returns a non-error response (Dagster webserver HTML).
**Failure meaning:** Dagster is not operational. No scheduled collection
will run. Manual asset triggers will fail.
**Failure action:** Inspect logs: `docker logs empire_dagster_daemon`.
Verify docker-compose.yml has all three service definitions.
Verify /mnt/empire-db/dagster volume is mounted and writable.

---

**Criterion:** Dagster asset graph contains at least one asset per source
```
Check:
  # In Dagster UI → Assets tab, count assets grouped by source_id tag.
  # Alternatively, query the Dagster GraphQL API:
  curl -s http://localhost:3010/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ assetNodes { id } }"}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin);
                  print(len(d['data']['assetNodes']))"
```
**Expected result:** Asset count ≥ number of rows in
`SELECT COUNT(*) FROM forge.metric_lineage` (one asset per
metric_id × source_id combination). Minimum 10 assets (one per source).
**Failure meaning:** Asset graph is incomplete. Sources with missing
asset definitions will never collect. Downstream features will
null-propagate permanently for those sources.
**Failure action:** Identify which sources lack asset definitions.
Check the Dagster code server logs for import errors in asset modules.
Restart empire_dagster_code after fixes.

---

**Criterion:** Tiingo OHLCV adapter has produced Silver rows
(Tiingo must be verified first — spot price dependency)
```
Check:
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM forge.observations
             WHERE source_id = 'tiingo';"
```
**Expected result:** `count > 0`
**Failure meaning:** Tiingo adapter is not writing to Silver. All other
adapters that depend on spot price normalization will produce
incorrect values.
**Failure action:** Manually trigger the Tiingo asset in Dagster UI.
Inspect the asset materialization log for errors. Verify
TIINGO_API_KEY is set in the Dagster code server environment.

---

**Criterion:** All 10 sources have Silver rows
```
Check:
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT source_id, count() AS row_count
             FROM forge.observations
             GROUP BY source_id
             ORDER BY source_id;"
```
**Expected result:** 10 rows, one per source canonical name, each with
`row_count > 0`.
**Failure meaning:** One or more sources are not writing to Silver.
Feature compute will null-propagate for all metrics from missing sources.
**Failure action:** For each source with zero rows: manually trigger
its Dagster asset and inspect materialization logs. Verify API
credentials are set in environment. Check dead_letter for rejection
patterns: `SELECT source_id, rejection_code, count() FROM forge.dead_letter GROUP BY source_id, rejection_code;`

---

**Criterion:** Bronze Iceberg tables exist in MinIO for at least one source
```
Check:
  mc ls --recursive local/bronze/ | head -20
```
**Expected result:** Output shows Iceberg metadata files
(metadata/*.json, data/*.parquet) under at least one source_id prefix.
**Failure meaning:** Bronze adapter is not writing to MinIO. Raw payloads
are not being preserved. Audit and backfill recovery paths are broken.
**Failure action:** Manually trigger one Bronze adapter asset in Dagster.
Inspect materialization log for MinIO write errors. Verify
MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY are set in
Dagster code server environment.

---

**Criterion:** Great Expectations checkpoint exists and produces
a validation result
```
Check:
  docker exec empire_dagster_code python3 -c "
    from great_expectations.data_context import DataContext
    ctx = DataContext()
    results = ctx.run_checkpoint(checkpoint_name='bronze_to_silver')
    print('success:', results.success)
    print('evaluated expectations:', results.statistics['evaluated_expectations'])
  "
```
**Expected result:** `success: True`, `evaluated_expectations > 0`
**Failure meaning:** Validation at the Bronze → Silver boundary is not
running. Invalid observations are passing to Silver without rejection.
**Failure action:** Verify GE context is initialized and checkpoint
is defined. Check that GE expectation suite was derived from
metric_catalog (range bounds, expected cadence, data types).
Re-run GE checkpoint configuration.

---

**Criterion:** Dead letter captures rejected observations — not silent drops
```
Check:
  # Step 1: submit a known-invalid observation through the adapter
  # (value outside metric_catalog range bounds for any active metric)
  # Step 2: verify it appears in dead_letter
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT source_id, metric_id, rejection_code, count()
             FROM forge.dead_letter
             WHERE ingested_at > now() - INTERVAL 1 HOUR
             GROUP BY source_id, metric_id, rejection_code
             ORDER BY count() DESC LIMIT 10;"
```
**Expected result:** At least one row in dead_letter from within the
last hour, demonstrating that the rejection pipeline has processed at
least one invalid observation during Phase 1 collection. Rejection code
is a non-empty string (e.g., `RANGE_VIOLATION`, `SCHEMA_VIOLATION`).
**Failure meaning:** Rejections are being silently dropped. Data quality
violations are invisible. Bad values may be reaching Silver undetected.
**Failure action:** Inject a known-bad row through one adapter's
validation path. Trace from GE rejection to dead_letter INSERT. Check
adapter code for silent exception swallowing.

---

**Criterion:** BLC-01 rsync pull routine is operational and file sensor ticks
```
Check:
  # Verify rsync landing directory exists and has received files
  ls -la /opt/empire/pipeline/blc01/landing/ | head -5
  # Verify file sensor last tick time in Dagster UI → Sensors
  # (manual check — no CLI equivalent)
  # Minimum: directory exists and contains at least one JSONL file
  ls /opt/empire/pipeline/blc01/landing/*.jsonl | wc -l
```
**Expected result:** Landing directory exists. At least 1 JSONL file
present (rsync has run at least once). Dagster UI shows file sensor
with tick timestamp within the last 24 hours.
**Failure meaning:** BLC-01 data is not reaching the system.
Liquidation flow metrics will be permanently null until resolved.
**Failure action:** SSH to Server2: `ssh root@192.168.68.12`.
Verify LXC 203 is running. Verify rsync service is running and
can reach proxmox landing directory. Check rsync logs for errors.

---

**Criterion:** NAS backup job is running for MinIO
```
Check:
  # Verify backup job exists and has run at least once
  systemctl status empire-minio-backup.timer || \
    crontab -l | grep minio-backup
  # Verify at least one backup file on NAS
  ls /mnt/nas/backups/minio/ 2>/dev/null | head -5
```
**Expected result:** Backup timer/cron is active. At least one backup
artifact visible on NAS.
**Failure meaning:** Bronze and Gold data has no backup. A MinIO disk
failure before the first backup means permanent data loss with no
recovery path.
**Failure action:** Configure and start the MinIO backup job before
any live collection continues. This is a Phase 1 pre-condition for
live collection, not a post-condition.

---

**Criterion:** NAS backup job is running for Dagster metadata
```
Check:
  systemctl status empire-dagster-backup.timer || \
    crontab -l | grep dagster-backup
  ls /mnt/nas/backups/dagster/ 2>/dev/null | head -3
```
**Expected result:** Backup timer/cron is active. At least one backup
artifact visible on NAS.
**Failure meaning:** Dagster run history and metadata are unprotected.
Loss requires rebuilding from scratch — acceptable per the failure mode
analysis in thread_infrastructure.md, but a best-practice gap.
**Failure action:** Configure and enable Dagster metadata backup job.

---

**Criterion:** ClickHouse credential isolation — forge_compute has no
ClickHouse connection string (Rule 2 enforcement)
```
Check:
  docker inspect empire_dagster_code \
    --format '{{json .Config.Env}}' \
    | python3 -c "
      import sys, json
      env = json.load(sys.stdin)
      ch_vars = [e for e in env if 'CLICKHOUSE' in e.upper()]
      print('ClickHouse env vars in dagster_code:', ch_vars)
    "
```
**Expected result:** Empty list `[]`. No CLICKHOUSE_* environment
variables in the Dagster code server container.
**Failure meaning:** Rule 2 violated. forge_compute or other services
could read ClickHouse directly, bypassing the Gold export layer and
introducing dependency on unmerged Silver data.
**Failure action:** Remove any CLICKHOUSE_* variables from the Dagster
code server environment. ClickHouse credentials belong exclusively
in the export asset's environment. Redeploy empire_dagster_code.

---

**Criterion:** Full collection round-trip verified end-to-end for one source
```
Check:
  # Trigger one adapter manually in Dagster UI and trace the full path:
  # 1. Verify Bronze Iceberg write:
  mc ls local/bronze/[source_id]/ | tail -3
  # 2. Verify Silver write (row count increases):
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM forge.observations
             WHERE source_id = '[source_id]'
               AND ingested_at > now() - INTERVAL 10 MINUTE;"
  # 3. Verify collection_events row logged:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT source_id, status, rows_written
        FROM forge.collection_events
        WHERE source_id = '[source_id]'
        ORDER BY collected_at DESC LIMIT 1;"
```
**Expected result:** (1) New Iceberg data files in bronze partition.
(2) Row count in Silver > 0 for recent ingestion_at window.
(3) collection_events row with `status = 'success'` and
`rows_written > 0`.
**Failure meaning:** The full Bronze → Validation → Silver pipeline
has not been proven end-to-end for any single source. Phase 2
cannot proceed without this verification.
**Failure action:** Identify which step is failing. Inspect Dagster
materialization log for the specific asset. Check GE validation report
for the run.

---

**Criterion:** macro.credit.hy_oas (BAMLH0A0HYM2) is present in metric_catalog
and has Silver rows from FRED adapter
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT canonical_name, status FROM forge.metric_catalog
        WHERE canonical_name = 'macro.credit.hy_oas';"
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM forge.observations
             WHERE metric_id = 'macro.credit.hy_oas'
               AND source_id = 'fred';"
```
**Expected result:** metric_catalog returns 1 row with `status = 'active'`.
ClickHouse returns `count > 0`.
**Failure meaning:** This metric is required by feature engineering and
signal design but was missing from the FRED adapter in the legacy system.
If not resolved in Phase 1, the Capital Flows pillar will null-propagate
for this metric permanently.
**Failure action:** Verify FRED adapter includes BAMLH0A0HYM2 series.
Add catalog entry if missing. Trigger FRED adapter to backfill.

---

#### Phase 1 Split Trigger Evaluation

Run these checks at Phase 1 gate. Record all measured values in the
gate completion report. No threshold is expected to be exceeded at
Phase 1 — this evaluation establishes baseline measurements.

```
Component: MinIO (Bronze + Gold object storage)
Trigger metric: /mnt/empire-data disk usage
Threshold: 80% (3.2TB of 4TB)
How to measure: df -h /mnt/empire-data
Action if at/above: Migrate to AWS S3. See thread_infrastructure.md
  Managed Service Migration Triggers — MinIO → AWS S3.
  Code changes: Zero. Endpoint config swap only.
Current measured value: [record at gate time]
Status: [Below / At / Above]

Component: ClickHouse (Silver observation store)
Trigger metric 1: Background merge queue depth
Threshold 1: > 100 parts sustained for > 7 consecutive days
How to measure: docker exec empire_clickhouse clickhouse-client \
  --query "SELECT count() FROM system.merges;"
Action if at/above: Investigate write throughput. Consider OPTIMIZE
  scheduling. If sustained, evaluate ClickHouse Cloud migration.
  Code changes: Zero. Connection string swap only.
Current measured value: [record at gate time]
Status: [Below / At / Above]

Trigger metric 2: Uncompressed row count
Threshold 2: 1 billion rows
How to measure: docker exec empire_clickhouse clickhouse-client \
  --query "SELECT count() FROM forge.observations;"
Action if at/above: Evaluate ClickHouse Cloud migration.
Current measured value: [record at gate time]
Status: [Below / At / Above]

Component: Dagster (orchestration)
Trigger metric: Operator time on Dagster infrastructure maintenance
Threshold: > 4 hours/month (upgrades, daemon restarts, metadata DB)
How to measure: Operator judgment. Track time explicitly.
Action if at/above: Evaluate Dagster Cloud migration.
  Code changes: Zero. Asset definitions are fully portable.
Current measured value: [record at gate time — estimated hours in Phase 1]
Status: [Below / At / Above]
```

**Phase 1 gate passes when all 15 criteria above return expected results
and split trigger evaluation is recorded. Phase 2 does not begin until
architect confirms.**

---

### Phase 2 Gate — Feature Engineering

Phase 3 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** Silver → Gold export asset materializes successfully
```
Check:
  # Trigger export asset manually in Dagster UI, then:
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM forge.observations
             WHERE ingested_at <= now() - INTERVAL 6 HOUR;"
  mc ls local/gold/ | head -10
```
**Expected result:** ClickHouse returns a row count. MinIO gold bucket
shows Iceberg metadata and data files created or updated within the
last run window.
**Failure meaning:** Gold layer is not being populated. Feature compute
has no data to read. All Phase 2+ gates are blocked.
**Failure action:** Inspect Dagster asset materialization log for the
export asset. Verify ClickHouse export credentials are set in the
export asset's environment. Verify MinIO gold bucket is writable.

---

**Criterion:** DuckDB can read Gold Iceberg tables
```
Check:
  docker exec empire_dagster_code python3 -c "
    import duckdb
    conn = duckdb.connect()
    conn.execute(\"INSTALL iceberg; LOAD iceberg;\")
    result = conn.execute(
      \"SELECT count(*) FROM iceberg_scan('s3://gold/observations/')\"
    ).fetchone()
    print('Gold row count:', result[0])
  "
```
**Expected result:** Script runs without error. `Gold row count` is a
positive integer matching the expected export volume.
**Failure meaning:** DuckDB cannot read Gold. Feature compute and the
serving layer have no data access path.
**Failure action:** Verify DuckDB iceberg extension is installed.
Verify MinIO endpoint configuration in DuckDB S3 config (endpoint,
access key, secret). Check that Iceberg metadata files are valid.

---

**Criterion:** DuckDB does not have ClickHouse credentials — Rule 2
```
Check:
  docker exec empire_dagster_code python3 -c "
    import os
    ch = {k: v for k, v in os.environ.items() if 'CLICKHOUSE' in k.upper()}
    print('ClickHouse vars visible to forge_compute:', ch)
  "
```
**Expected result:** Empty dict `{}`
**Failure meaning:** forge_compute can read ClickHouse directly,
bypassing the Gold export layer. Features computed from unmerged Silver
data will produce silently incorrect results.
**Failure action:** Remove CLICKHOUSE_* from forge_compute environment.
These credentials must not appear in any service except the export asset.

---

**Criterion:** Feature catalog entries exist for all computed features
before compute runs (forge_compute startup check)
```
Check:
  docker exec empire_dagster_code python3 -c "
    from forge_compute import startup_check
    result = startup_check.verify_feature_catalog_coverage()
    print('Missing catalog entries:', result.missing)
    print('All features registered:', result.all_registered)
  "
```
**Expected result:** `Missing catalog entries: []`,
`All registered: True`
**Failure meaning:** Features are being computed without catalog entries.
Feature catalog immutability requirement is violated — features cannot
be audited or traced back to their inputs.
**Failure action:** Add missing metric_catalog entries for any feature
lacking registration. Do not proceed with feature compute until
startup check passes.

---

**Criterion:** PIT constraint — no feature value uses data with
ingested_at after the feature computation timestamp
```
Check:
  # Run the PIT audit script against a sample of computed features.
  # The audit queries Gold with explicit ingested_at <= T filter
  # and compares results to features computed without the filter.
  docker exec empire_dagster_code python3 -c "
    from forge_compute import pit_audit
    violations = pit_audit.run(
      metric_id='derivatives.perpetual.funding_rate',
      instrument_id='BTC',
      sample_date='2026-01-01'
    )
    print('PIT violations found:', len(violations))
    if violations:
      print(violations[:3])
  "
```
**Expected result:** `PIT violations found: 0`
**Failure meaning:** Look-ahead bias in feature values. ML models
trained on these features will have inflated OOS performance that
does not generalize to live data. This is a fundamental correctness
failure — Phase 3 cannot proceed.
**Failure action:** Identify which features use data without the
ingested_at filter. Fix the Gold query in those features. Re-run
full feature compute for affected features. Re-audit before passing gate.

---

**Criterion:** INSUFFICIENT_HISTORY null state propagates correctly
```
Check:
  docker exec empire_dagster_code python3 -c "
    from forge_compute import feature_engine
    result = feature_engine.compute_feature(
      metric_id='derivatives.perpetual.funding_rate',
      instrument_id='BTC',
      lookback_days=365,
      as_of='2021-01-01'  # Before any data exists
    )
    print('Null state:', result.null_state)
    print('Value:', result.value)
  "
```
**Expected result:** `null_state = INSUFFICIENT_HISTORY`, `value = None`
(not an error, not 0, not a stale value — a typed null state)
**Failure meaning:** Features return incorrect values when insufficient
history exists. EDSx confidence will be miscalculated.
**Failure action:** Verify feature engine returns NullState enum, not
raises exception or returns 0. Null state must propagate to pillar
confidence computation in Phase 3.

---

**Criterion:** SOURCE_STALE null state propagates correctly
```
Check:
  # Simulate a stale source by inserting a collection_events row
  # with status='stale' for a source, then computing a dependent feature
  docker exec empire_dagster_code python3 -c "
    from forge_compute import feature_engine
    result = feature_engine.compute_feature(
      metric_id='derivatives.perpetual.funding_rate',
      instrument_id='BTC',
      lookback_days=30,
      as_of='now',
      override_source_status={'coinalyze': 'stale'}
    )
    print('Null state:', result.null_state)
  "
```
**Expected result:** `null_state = SOURCE_STALE`
**Failure meaning:** Source staleness is not propagating to features.
Signal will present confident outputs during data outages.
**Failure action:** Verify feature engine reads collection_events for
source freshness before computing. Return SOURCE_STALE when source
last_collected_at exceeds cadence_hours × 2.

---

**Criterion:** Rolling window features are idempotent
```
Check:
  docker exec empire_dagster_code python3 -c "
    from forge_compute import feature_engine
    import json
    result_1 = feature_engine.compute_feature(
      metric_id='derivatives.perpetual.funding_rate_30d_zscore',
      instrument_id='BTC',
      as_of='2026-02-01T12:00:00Z'
    )
    result_2 = feature_engine.compute_feature(
      metric_id='derivatives.perpetual.funding_rate_30d_zscore',
      instrument_id='BTC',
      as_of='2026-02-01T12:00:00Z'
    )
    print('Idempotent:', result_1.value == result_2.value)
  "
```
**Expected result:** `Idempotent: True`
**Failure meaning:** Non-deterministic feature compute produces
different signals on each run from identical inputs. ML training
is unstable. Audit trail is unusable.
**Failure action:** Identify source of non-determinism (random seeds,
wall-clock dependencies, unordered aggregations). Fix before Phase 3.

---

**Criterion:** Gold export is incremental — does not re-export all rows
on every run
```
Check:
  # Run export asset twice. Measure rows written in second run.
  # First run writes all rows since epoch.
  # Second run should write only rows ingested since first run.
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM forge.observations
             WHERE ingested_at > now() - INTERVAL 1 MINUTE;"
  # Compare against rows written in second export run (from Dagster
  # materialization metadata).
```
**Expected result:** Second export run writes 0 rows if no new
observations have arrived since the first run. Dagster materialization
metadata confirms the incremental cutoff timestamp was advanced.
**Failure meaning:** Full re-export on every run will dominate
ClickHouse I/O as Silver grows. Export scans will slow. Gold will
contain duplicate rows.
**Failure action:** Verify export asset reads its last-run cutoff
from Dagster materialization metadata. Fix to use
`WHERE ingested_at > [last_export_at]` filter.

---

**Criterion:** Breadth score is deterministic and within [0, 1]
```
Check:
  docker exec empire_dagster_code python3 -c "
    from forge_compute import breadth_score
    score_1 = breadth_score.compute(instrument_id='BTC', as_of='2026-02-15')
    score_2 = breadth_score.compute(instrument_id='BTC', as_of='2026-02-15')
    print('Score 1:', score_1)
    print('Score 2:', score_2)
    print('Deterministic:', score_1 == score_2)
    print('In range:', 0.0 <= score_1 <= 1.0)
  "
```
**Expected result:** Both scores are equal. Score is between 0.0 and 1.0 inclusive.
**Failure meaning:** Breadth score is non-deterministic or out of range.
EDSx confidence computation will be incorrect.
**Failure action:** Breadth score must use fixed weights from
thread_3_features.md. Remove any stochastic component.
Clamp output to [0, 1] and verify the formula is fixed, not learned.

---

**Phase 2 gate passes when all 13 criteria above return expected results.
Phase 3 does not begin until architect confirms.**

---

### Phase 3 Gate — EDSx Signal

Phase 4 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** EDSx-02 (Trend/Structure) produces non-null output for
BTC and ETH
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import pillar_engine
    for instrument in ['BTC', 'ETH']:
      result = pillar_engine.score(pillar='trend_structure',
                                   instrument_id=instrument)
      print(f'{instrument}: direction={result.direction},
              confidence={result.confidence}')
  "
```
**Expected result:** Both instruments return non-null direction and
confidence. Direction is one of: `bullish`, `bearish`, `neutral`.
Confidence is in [0, 1].
**Failure meaning:** EDSx-02 is not scoring. The pillar was listed as
live at Phase 0 — any regression here must be resolved before Phase 4.
**Failure action:** Inspect pillar_engine logs for feature compute
failures. Verify all EDSx-02 input features have non-null values for
BTC and ETH. Trace null state if any.

---

**Criterion:** EDSx-03 R3 (Liquidity/Flow) produces non-null output
for BTC and ETH
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import pillar_engine
    for instrument in ['BTC', 'ETH']:
      result = pillar_engine.score(pillar='liquidity_flow',
                                   instrument_id=instrument)
      print(f'{instrument}: direction={result.direction},
              confidence={result.confidence}')
  "
```
**Expected result:** Both instruments return non-null direction and
confidence in valid ranges.
**Failure meaning:** EDSx-03 R3 regressed against new feature layer.
**Failure action:** Compare input features to pre-pivot EDSx-03 inputs.
Identify which feature names or null states changed.

---

**Criterion:** EDSx confidence formula produces a value in [0, 1] equal
to signals_computed / signals_available
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import confidence
    result = confidence.compute(instrument_id='BTC')
    print('signals_computed:', result.signals_computed)
    print('signals_available:', result.signals_available)
    print('confidence:', result.value)
    print('formula check:',
          abs(result.value - result.signals_computed /
              result.signals_available) < 0.001)
  "
```
**Expected result:** `formula check: True`. Value in [0.0, 1.0].
**Failure meaning:** EDSx confidence is not computed per the locked
formula in thread_2_signal.md. Confidence values are not interpretable
as data completeness.
**Failure action:** Verify confidence.compute() implements exactly
signals_computed / signals_available. No learned weights, no adjustments.

---

**Criterion:** A null pillar reduces confidence but does not produce
a null composite signal
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import composite
    result = composite.score(
      instrument_id='BTC',
      override_pillar_null={'valuation': True}
    )
    print('Composite direction:', result.direction)
    print('Composite confidence:', result.confidence)
    print('Null signal:', result.direction is None)
  "
```
**Expected result:** `Composite direction` is non-null. `Confidence`
is lower than it would be with all pillars present. `Null signal: False`.
**Failure meaning:** A missing pillar cascades to a null composite signal.
The system produces no output instead of a degraded signal. Customers
receive silence rather than a lower-confidence signal.
**Failure action:** Verify EDSx composite uses null-propagation with
confidence reduction, not null propagation with signal suppression.

---

**Criterion:** EDSx regime classification returns one of three valid states
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import regime
    result = regime.classify()
    print('Regime:', result.state)
    print('Valid:', result.state in ['risk_on', 'risk_off', 'neutral'])
  "
```
**Expected result:** `Valid: True`. Regime is `risk_on`, `risk_off`,
or `neutral`.
**Failure meaning:** Regime classification is returning invalid states
or exceptions. Composite weight selection fails.
**Failure action:** Verify regime engine uses M2 supply logic
(legacy production engine per thread_2_signal.md locked decisions).
The Volatility-Liquidity Anchor regime is an H2 target — not Phase 3.

---

**Criterion:** EDSx output contract matches the Layer 6 signal schema
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import output
    import json
    signal = output.get_signal(instrument_id='BTC')
    schema_fields = ['instrument', 'timestamp', 'signal', 'components']
    signal_fields = ['direction', 'confidence', 'horizon', 'regime']
    print('Top-level fields present:',
          all(f in signal for f in schema_fields))
    print('Signal fields present:',
          all(f in signal['signal'] for f in signal_fields))
    print('Confidence in [0,1]:',
          0 <= signal['signal']['confidence'] <= 1)
  "
```
**Expected result:** All field checks `True`. Confidence in [0, 1].
**Failure meaning:** EDSx output does not conform to the Layer 6
contract defined in thread_2_signal.md. Phase 5 synthesis and serving
will break on this output.
**Failure action:** Align output schema to the exact JSON structure
in thread_2_signal.md. Field names are immutable — they are the API
contract.

---

**Criterion:** All 5 pillars score independently — one pillar failure
does not prevent other pillars from running
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import pillar_engine
    pillars = ['trend_structure', 'liquidity_flow', 'valuation',
               'tactical_macro', 'structural_risk']
    for pillar in pillars:
      try:
        result = pillar_engine.score(pillar=pillar, instrument_id='BTC')
        print(f'{pillar}: ok, confidence={result.confidence}')
      except Exception as e:
        print(f'{pillar}: EXCEPTION - {e}')
  "
```
**Expected result:** All 5 pillars return a result (not exception).
Pillars with insufficient data return a NullState result, not an
exception that would block the composite.
**Failure meaning:** A single bad pillar crashes the EDSx composite
run. The system produces no signal when any pillar fails, rather
than a degraded signal.
**Failure action:** Wrap each pillar execution in exception handling.
Return NullState on exception. Log the error. Do not propagate
exceptions to the composite layer.

---

**Criterion:** EDSx produces non-null output for ≥ 10 signal-eligible
instruments (not just BTC and ETH)
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import output
    from forge_catalog import instruments
    eligible = instruments.get_signal_eligible()
    scored = [i for i in eligible
              if output.get_signal(i)['signal']['direction'] is not None]
    print('Signal-eligible count:', len(eligible))
    print('Instruments with non-null signal:', len(scored))
  "
```
**Expected result:** `Instruments with non-null signal ≥ 10`
**Failure meaning:** EDSx is only working for the most data-complete
instruments. Coverage is insufficient for a signal product.
**Failure action:** Identify instruments with null outputs. Trace to
missing feature data in Silver. Verify Coinalyze adapter is writing
rows for those instruments.

---

**Criterion:** instrument_metric_coverage reflects actual data availability
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT instrument_id, metric_id, coverage_pct
        FROM forge.instrument_metric_coverage
        WHERE instrument_id = 'BTC'
        ORDER BY coverage_pct DESC LIMIT 10;"
```
**Expected result:** Rows returned with `coverage_pct` values between
0 and 1. High-coverage metrics (funding rate, OI) should show ≥ 0.9
for BTC given Phase 1 collection.
**Failure meaning:** instrument_metric_coverage is stale or unpopulated.
EDSx confidence computation reads this table — stale values produce
incorrect confidence scores.
**Failure action:** Trigger the instrument_metric_coverage update job.
Verify it is scheduled in Dagster and ran after Phase 1 collection.

---

**Phase 3 gate passes when all 10 criteria above return expected results.
Phase 4 does not begin until architect confirms.**

---

### Phase 4 Gate — ML Track (Shadow)

**Hard date constraint:** This gate cannot pass before
`shadow_start_date + 30 calendar days`. The shadow start date must be
recorded in system metadata when shadow mode is activated. Any
evaluation of this gate before the 30-day period is complete is void.

Phase 5 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** Shadow start date is recorded and 30-day minimum has elapsed
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT key, value, recorded_at
        FROM forge.system_metadata
        WHERE key = 'ml_shadow_start_date';"
  # Then verify:
  python3 -c "
    from datetime import datetime, timezone, timedelta
    shadow_start = datetime.fromisoformat('[value from above query]')
    days_elapsed = (datetime.now(timezone.utc) - shadow_start).days
    print('Days elapsed:', days_elapsed)
    print('30-day minimum met:', days_elapsed >= 30)
  "
```
**Expected result:** `system_metadata` row exists with `key = ml_shadow_start_date`.
`30-day minimum met: True`. If this returns `False`, stop — the gate
cannot pass regardless of any other criteria.
**Failure meaning:** Shadow period is being cut short. The minimum
shadow period exists to detect training-to-live distribution shifts
that OOS evaluation cannot reveal.
**Failure action:** Wait. No remediation possible. Gate evaluation must
be rescheduled for shadow_start + 30 days.

---

**Criterion:** All 5 ML models are trained with OOS period ≥ 12 months
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import model_registry
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      meta = model_registry.get(model_name)
      print(f'{model_name}: oos_months={meta.oos_months},
              trained={meta.is_trained}')
  "
```
**Expected result:** All 5 models show `is_trained=True` and
`oos_months >= 12`.
**Failure meaning:** Models trained on insufficient OOS data. Graduation
criteria cannot be reliably evaluated. Proceed to shadow mode premature.
**Failure action:** Extend the OOS window. Verify Gold layer has
sufficient historical data depth (minimum 24 months total required to
have 12 months OOS with 12 months training).

---

**Criterion:** All 5 models pass all 5 graduation criteria on OOS data
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import graduation
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      result = graduation.evaluate(model_name)
      print(f'{model_name}:')
      for criterion, passed in result.criteria.items():
        print(f'  {criterion}: {\"PASS\" if passed else \"FAIL\"}')
      print(f'  Overall: {\"PASS\" if result.all_passed else \"FAIL\"}')
  "
```
**Expected result:** All 5 models show `Overall: PASS`. Five criteria
evaluated per model: (1) AUC-ROC ≥ 0.56, (2) ECE < 0.05,
(3) no feature > 40% importance, (4) prediction stability across OOS
folds (std dev < 0.15), (5) no cliff between OOS and first shadow days.
**Failure meaning:** One or more models have not graduated. Deploying
to production would produce poorly calibrated or unstable signals.
**Failure action:** For each failing model: diagnose the failing
criterion. Retrain with corrected features or adjusted hyperparameters.
Re-run graduation evaluation. Shadow period restarts from the date of
re-deployment — the 30-day clock resets.

---

**Criterion:** ECE < 0.05 for all 5 models
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import calibration
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      ece = calibration.expected_calibration_error(model_name)
      print(f'{model_name}: ECE={ece:.4f}, pass={ece < 0.05}')
  "
```
**Expected result:** All 5 models return `pass=True` (ECE < 0.05).
**Failure meaning:** Models are poorly calibrated. Confidence values
do not reflect actual prediction accuracy. Synthesis layer weights
based on confidence are meaningless.
**Failure action:** Apply or re-apply isotonic calibration.
Verify calibration was applied to OOS predictions, not in-sample.
Re-evaluate ECE after recalibration.

---

**Criterion:** No single feature exceeds 40% importance in any model
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import feature_importance
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      top_feature, importance = feature_importance.top(model_name)
      print(f'{model_name}: top_feature={top_feature},
              importance={importance:.3f},
              pass={importance < 0.40}')
  "
```
**Expected result:** All models show `pass=True` (top feature < 40%).
**Failure meaning:** Model is dominated by a single feature —
a fragile signal that will degrade badly when that source is stale
or the feature distribution shifts.
**Failure action:** Inspect the dominant feature. Consider removing it
or regularizing the model. Retrain. The 30-day shadow clock resets.

---

**Criterion:** All 5 models are in shadow mode and producing output
each scheduled run
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import shadow_log
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=25)
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      last_run = shadow_log.last_run(model_name)
      print(f'{model_name}: last_run={last_run},
              recent={last_run > cutoff}')
  "
```
**Expected result:** All 5 models show `recent=True` — output logged
within the last 25 hours (accounts for 24h cadence with margin).
**Failure meaning:** Shadow evaluation is not running on schedule.
The 30-day shadow period contains gaps. Gap-filled shadow data is not
a valid shadow evaluation.
**Failure action:** Identify which models missed runs. Inspect Dagster
shadow evaluation asset for failures. Verify model outputs are not
blocked by upstream data staleness.

---

**Criterion:** Shadow accuracy is consistent with OOS evaluation —
no cliff between OOS and live shadow performance
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import shadow_evaluation
    for model_name in ['m_macro', 'm_derivatives', 'm_flows',
                       'm_defi', 'm_synthesis']:
      result = shadow_evaluation.consistency_check(model_name)
      print(f'{model_name}: oos_auc={result.oos_auc:.3f},
              shadow_auc={result.shadow_auc:.3f},
              cliff={result.cliff_detected}')
  "
```
**Expected result:** All models show `cliff_detected=False`. Shadow AUC
is within 0.05 of OOS AUC for each model.
**Failure meaning:** Model performance degrades significantly when
moved from historical OOS data to live data. This indicates feature
distribution shift, data pipeline differences, or look-ahead bias in
OOS evaluation. The model is not production-ready.
**Failure action:** Investigate the source of the cliff. If feature
distributions differ between OOS and live: audit the feature compute
pipeline for PIT violations. If OOS was inflated: review label
generation for look-ahead bias. Retrain with corrected pipeline.
Shadow period restarts.

---

**Criterion:** No ML output is present in the composite signal —
EDSx is the sole production signal through Phase 4
```
Check:
  docker exec empire_dagster_code python3 -c "
    from edsx import output
    signal = output.get_signal(instrument_id='BTC')
    has_ml = signal.get('ml_contribution') is not None
    synthesis_mode = signal.get('synthesis_mode')
    print('ML contribution in signal:', has_ml)
    print('Synthesis mode:', synthesis_mode)
    print('EDSx-only mode confirmed:', synthesis_mode == 'edsx_only')
  "
```
**Expected result:** `ML contribution in signal: False`,
`EDSx-only mode confirmed: True`.
**Failure meaning:** ML output has been included in the production
signal before Phase 5. Shadow period is compromised — if ML is
affecting production, shadow evaluation is not independent.
**Failure action:** Remove ML contribution from the composite signal.
Verify synthesis_mode flag. Deploy fix before proceeding.

---

**Criterion:** M-Synthesis produces valid probability distributions
for all signal-eligible instruments each shadow run
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import shadow_log
    from forge_catalog import instruments
    eligible = instruments.get_signal_eligible()
    last_run = shadow_log.get_last_run_outputs('m_synthesis')
    missing = [i for i in eligible if i not in last_run]
    invalid = [i for i, v in last_run.items()
               if not (isinstance(v, float) and 0 <= v <= 1)]
    print('Eligible instruments:', len(eligible))
    print('Missing from last run:', len(missing))
    print('Invalid probability values:', len(invalid))
  "
```
**Expected result:** `Missing from last run: 0`, `Invalid probability values: 0`.
All signal-eligible instruments have a probability value in [0, 1].
**Failure meaning:** M-Synthesis does not cover all instruments.
Synthesis layer in Phase 5 will produce null outputs for uncovered
instruments, which breaks the Layer 6 output contract.
**Failure action:** Identify which instruments are missing from
M-Synthesis output. Trace to missing input features from domain models.
Fix coverage before Phase 5.

---

**Criterion:** Shadow period had zero infrastructure failures —
no missed runs due to system issues
```
Check:
  docker exec empire_dagster_code python3 -c "
    from ml import shadow_log
    from datetime import datetime, timezone
    import json
    failures = shadow_log.get_infrastructure_failures()
    print('Infrastructure failures during shadow period:', len(failures))
    if failures:
      for f in failures[:5]:
        print(' ', f)
  "
```
**Expected result:** `Infrastructure failures during shadow period: 0`
**Failure meaning:** Shadow evaluation was interrupted by system
failures. The 30-day shadow period must be clean — a failure and
recovery means the effective shadow period restarts.
**Failure action:** If failures are present: record them, resolve the
underlying infrastructure issue, and restart the shadow period.
30-day clock resets from the date of last failure resolution.

---

**Phase 4 gate passes when all 10 criteria above return expected results
AND shadow_start_date + 30 calendar days has elapsed.
Phase 5 does not begin until architect confirms.**

---

### Phase 5 Gate — Signal Synthesis and Serving

Phase 6 does not begin until all criteria below pass and architect confirms.

---

**Criterion:** Synthesis output matches the Layer 6 output contract for
BTC and at least 2 additional instruments
```
Check:
  curl -s http://localhost:8000/v1/signals/BTC \
    -H "Authorization: Bearer $TEST_API_KEY" \
    | python3 -c "
      import sys, json
      s = json.load(sys.stdin)
      required = ['instrument', 'timestamp', 'signal', 'components']
      signal_required = ['direction', 'confidence', 'magnitude', 'horizon', 'regime']
      print('Top-level fields:', all(f in s for f in required))
      print('Signal fields:', all(f in s['signal'] for f in signal_required))
      print('Confidence valid:', 0 <= s['signal']['confidence'] <= 1)
      print('Magnitude non-null:', s['signal']['magnitude'] is not None)
    "
```
**Expected result:** All field checks `True`. Magnitude is non-null
(ML track is now active in synthesis).
**Failure meaning:** Serving layer does not conform to the Layer 6
contract. Customer-facing API output is malformed.
**Failure action:** Align /v1/signals response schema to the exact
JSON structure in thread_2_signal.md. Magnitude must be non-null
in Phase 5 — if ML track is producing null magnitude, trace to
M-Synthesis output.

---

**Criterion:** Agreement scenario produces correct confidence adjustment
```
Check:
  docker exec empire_dagster_code python3 -c "
    from synthesis import engine
    result = engine.synthesize(
      edsx={'direction': 'bullish', 'confidence': 0.72},
      ml={'direction': 'bullish', 'probability': 0.68}
    )
    print('Direction:', result.direction)
    print('Confidence:', result.confidence)
    print('Agreement boost applied:',
          result.confidence >= max(0.72, 0.68))
  "
```
**Expected result:** `Direction: bullish`. `Agreement boost applied: True`
(confidence at or above the higher individual confidence).
**Failure meaning:** Synthesis is not rewarding agreement between EDSx
and ML. Confidence values will be lower than warranted on aligned signals.
**Failure action:** Verify synthesis engine implements the agreement
confidence adjustment from thread_2_signal.md.

---

**Criterion:** Disagreement scenario produces reduced confidence
```
Check:
  docker exec empire_dagster_code python3 -c "
    from synthesis import engine
    edsx_conf = 0.70
    ml_prob = 0.68
    result = engine.synthesize(
      edsx={'direction': 'bullish', 'confidence': edsx_conf},
      ml={'direction': 'bearish', 'probability': ml_prob}
    )
    print('Direction:', result.direction)
    print('Confidence:', result.confidence)
    print('Confidence reduced:',
          result.confidence < max(edsx_conf, ml_prob))
  "
```
**Expected result:** `Confidence reduced: True`. Direction reflects
the 0.5/0.5 weighted resolution per thread_2_signal.md.
**Failure meaning:** Synthesis treats disagreement the same as agreement.
Overconfident signals on contested predictions will be served.
**Failure action:** Verify synthesis engine applies confidence penalty
on EDSx/ML directional disagreement.

---

**Criterion:** Redistribution filter blocks SoSoValue and CoinMetrics
data from external API responses
```
Check:
  # Test with an endpoint that would return ETF flow data (SoSoValue)
  curl -s "http://localhost:8000/v1/timeseries?metric_id=etf.flows.net_inflow_usd&instrument_id=BTC" \
    -H "Authorization: Bearer $EXTERNAL_TIER_API_KEY" \
    | python3 -c "
      import sys, json
      r = json.load(sys.stdin)
      print('Response type:', type(r))
      print('Data empty or excluded:',
            r.get('data') == [] or r.get('excluded') == True
            or 'redistribution_restricted' in str(r))
    "
```
**Expected result:** Response is empty data, an explicit exclusion flag,
or a redistribution_restricted error. No SoSoValue data in the response body.
**Failure meaning:** Restricted source data is being served externally.
This is a ToS violation before Phase 6 audit resolves it.
**Failure action:** Verify serving layer checks source_catalog.redistribution
before including data in responses. SoSoValue (redistribution=false) and
CoinMetrics (redistribution=false) must be excluded from all external
API responses. Fix and re-verify.

---

**Criterion:** Full provenance trace works for BTC signal
```
Check:
  curl -s "http://localhost:8000/v1/signals/BTC?include_provenance=true" \
    -H "Authorization: Bearer $TEST_API_KEY" \
    | python3 -c "
      import sys, json
      r = json.load(sys.stdin)
      prov = r.get('provenance', {})
      print('Has provenance:', 'provenance' in r)
      print('Has feature_values:', 'feature_values' in prov)
      print('Has observations:', 'observations' in prov)
      print('Has collection_events:', 'collection_events' in prov)
    "
```
**Expected result:** All four fields present and non-empty. Provenance
chain is complete from signal to source.
**Failure meaning:** Signal auditability is broken. The core
differentiator (systematic, auditable signals) cannot be demonstrated.
**Failure action:** Verify provenance assembly in serving layer reads
feature_values from Marts, observations from Gold, collection_events
from PostgreSQL. Wire missing links.

---

**Criterion:** Webhook delivery works against a test endpoint
```
Check:
  # Start a webhook capture endpoint (ngrok, webhook.site, or local netcat)
  # Configure test webhook URL in serving layer
  # Trigger a manual signal computation
  # Verify payload received at webhook endpoint
  curl -s http://localhost:8000/v1/admin/trigger-webhook-test \
    -H "Authorization: Bearer $ADMIN_API_KEY" \
    -d '{"webhook_url": "http://localhost:9999/test"}'
  # Check capture endpoint for received payload
```
**Expected result:** Webhook capture endpoint receives a POST request
with a valid signal payload within 30 seconds.
**Failure meaning:** Push delivery is broken. Customers relying on
webhooks will not receive signals.
**Failure action:** Verify webhook worker is running. Check outbound
network connectivity from the API container. Verify payload serialization.

---

**Criterion:** SOURCE_STALE flag appears in signal output within 2
collection cycles of a source failure
```
Check:
  docker exec empire_dagster_code python3 -c "
    from forge_catalog import collection_events
    from edsx import output
    # Simulate stale source by inserting a stale collection_events row
    collection_events.insert_stale_marker(
      source_id='coinalyze',
      stale_since='6 hours ago'
    )
    signal = output.get_signal('BTC')
    print('Staleness flag:', signal.get('signal', {}).get('staleness_flags'))
    print('Source stale flagged:',
          'coinalyze' in str(signal.get('signal', {}).get('staleness_flags', [])))
  "
```
**Expected result:** Signal output includes a staleness_flags entry
for coinalyze. Signal is still produced (not null) but flags the stale source.
**Failure meaning:** Customers will not know when signal inputs are
stale. They may act on signals built from outdated data without warning.
**Failure action:** Verify staleness propagation from collection_events
through feature compute to signal output. All SOURCE_STALE null states
must surface in the serving layer response.

---

**Criterion:** Serving layer has no ClickHouse credentials in its
environment (Rule 2)
```
Check:
  docker inspect empire_api \
    --format '{{json .Config.Env}}' \
    | python3 -c "
      import sys, json
      env = json.load(sys.stdin)
      ch = [e for e in env if 'CLICKHOUSE' in e.upper()]
      print('ClickHouse env vars in serving layer:', ch)
    "
```
**Expected result:** Empty list `[]`
**Failure meaning:** Serving layer has direct ClickHouse access.
Rule 2 is violated. The serving layer should read only DuckDB/Gold.
**Failure action:** Remove CLICKHOUSE_* from empire_api environment.
Redeploy. Re-verify.

---

**Criterion:** /v1/signals endpoint returns correct responses for
≥ 3 distinct instruments
```
Check:
  for instrument in BTC ETH SOL; do
    curl -s "http://localhost:8000/v1/signals/$instrument" \
      -H "Authorization: Bearer $TEST_API_KEY" \
      | python3 -c "
        import sys, json
        r = json.load(sys.stdin)
        print('$instrument: direction=', r.get('signal',{}).get('direction'),
              'confidence=', r.get('signal',{}).get('confidence'))
      "
  done
```
**Expected result:** All 3 instruments return non-null direction and
confidence in [0, 1]. No HTTP errors.
**Failure meaning:** Signal endpoint is only working for BTC/ETH.
Coverage is insufficient for product launch.
**Failure action:** Verify signal-eligible instruments in instrument_metric_coverage.
Ensure Phase 1 collection covered all three instruments.

---

**Phase 5 gate passes when all 10 criteria above return expected results.
Phase 6 does not begin until architect confirms.**

---

### Phase 6 Gate — Productization

**Sequencing constraint:** ToS audit must complete and all restrictions
must be enforced in the serving layer before any data is delivered
to an external customer. This is not a background task — it gates
first customer delivery.

No first customer delivery before all criteria below pass and architect confirms.

---

**Criterion:** Any collection failure is diagnosable within 15 minutes
using Dagster UI alone
```
Check:
  # Simulate a collection failure by stopping one adapter manually.
  # Record the time at which the failure began.
  # Navigate to Dagster UI → Assets tab.
  # Verify the affected asset shows a stale indicator within 15 minutes.
  # Verify the failure is visible with enough detail to identify:
  #   (a) which source failed, (b) which metric, (c) the error message.
  # Time from failure to visible stale indicator: [record at gate time]
```
**Expected result:** Stale indicator appears within 15 minutes.
Error message in Dagster UI identifies source, metric, and error type
without requiring SSH or log inspection.
**Failure meaning:** Operations are not meeting the one-operator
viability requirement. A data quality failure will not be detected
until it affects downstream signals.
**Failure action:** Verify Dagster freshness policies are set from
`cadence_hours` in metric_catalog. Tune sensor tick frequency if
stale detection lag exceeds 15 minutes.

---

**Criterion:** Metric catalog methodology fields are populated for all
active metrics
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT COUNT(*) FROM forge.metric_catalog
        WHERE status = 'active' AND
          (methodology IS NULL OR methodology = '');"
```
**Expected result:** `count = 0` (no active metrics with empty methodology).
**Failure meaning:** Metric catalog is incomplete. Customers cannot
audit how metrics are computed. Methodology documentation is a Phase 6
deliverable — incomplete documentation blocks first customer delivery.
**Failure action:** Populate methodology field for all active metrics.
For proxy metrics (e.g., defi.lending.utilization_rate), document
the proxy computation explicitly.

---

**Criterion:** EDSx methodology document exists and is complete
```
Check:
  ls -la docs/methodology/edsx_methodology.md
  wc -l docs/methodology/edsx_methodology.md
```
**Expected result:** File exists. Line count > 100 (non-trivial document).
Content covers: five pillar descriptions, confidence formula, regime
engine, scoring rules, null handling.
**Failure meaning:** First customer cannot audit signal methodology.
The core product differentiator (systematic, auditable signals)
is undocumented.
**Failure action:** Write EDSx methodology document. Minimum: pillar
descriptions, confidence formula, regime logic, scoring algorithm.

---

**Criterion:** ML methodology document exists and is complete
```
Check:
  ls -la docs/methodology/ml_methodology.md
  wc -l docs/methodology/ml_methodology.md
```
**Expected result:** File exists. Line count > 100. Content covers:
5 domain models, training procedure, walk-forward validation, graduation
criteria, calibration method, shadow period protocol, synthesis weights.
**Failure meaning:** ML signal is undocumented. Customers cannot
evaluate model quality claims.
**Failure action:** Write ML methodology document. Minimum: model
descriptions, training and evaluation procedure, graduation criteria,
calibration method.

---

**Criterion:** ToS audit complete — all 10 sources have audit records

The gate cannot pass while any source has `tos_status = 'unaudited'`
and `redistribution IS NULL`. Every source must have a documented
audit decision before external data products ship.

```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT canonical_name, tos_status, redistribution,
               commercial_use, audit_date, audit_notes
        FROM forge.source_catalog
        ORDER BY canonical_name;"
```

**ToS Audit Table — must be complete before gate passes:**

| Source | ToS Status | Redistribution | Commercial Use | Audit Date | Decision | Action Taken |
|---|---|---|---|---|---|---|
| binance_blc01 | [result] | [result] | [result] | [record] | [record] | [record] |
| bgeometrics | [result] | [result] | [result] | [record] | [record] | [record] |
| coinmetrics | restricted | false | false | [record] | Internal only | Serving filter active |
| coinpaprika | [result] | [result] | [result] | [record] | [record] | [record] |
| coinalyze | [result] | [result] | [result] | [record] | [record] | [record] |
| defillama | low | true | true | [record] | Full redistribution | No restriction |
| etherscan | [result] | [result] | [result] | [record] | [record] | [record] |
| fred | none | true | true | [record] | Full redistribution | No restriction |
| sosovalue | restricted | false | false | [record] | Internal only | Serving filter active |
| tiingo | paid | true | true | [record] | Full redistribution | Paid tier confirmed |

**Expected result:** All 10 rows have non-null audit_date. No row has
`tos_status = 'unaudited'` after audit. Redistribution=false sources
have serving filter verified in Phase 5 criterion above.
**Failure meaning:** Restricted data may be exposed in external products.
Legal risk before first customer delivery.
**Failure action:** Complete ToS review for each unaudited source.
For sources where redistribution cannot be confirmed: set
`redistribution = false` and enforce in serving layer before delivery.

---

**Criterion:** SoSoValue data is confirmed excluded from all external
API responses in production
```
Check:
  # Same as Phase 5 redistribution criterion — re-verify in production config
  curl -s "https://fromthebridge.net/api/v1/timeseries?metric_id=etf.flows.net_inflow_usd&instrument_id=BTC" \
    -H "Authorization: Bearer $EXTERNAL_TIER_PROD_API_KEY" \
    | python3 -c "
      import sys, json
      r = json.load(sys.stdin)
      has_data = len(r.get('data', [])) > 0
      print('SoSoValue data in external response:', has_data)
    "
```
**Expected result:** `SoSoValue data in external response: False`
**Failure meaning:** SoSoValue data is being distributed externally.
This violates the non-commercial ToS confirmed in thread_4.
**Failure action:** Verify redistribution filter is active in
production (not just dev). Check that API key tier enforcement
is applying the filter. Do not deliver to first customer until resolved.

---

**Criterion:** CoinMetrics data is confirmed excluded from all external
API responses in production
```
Check:
  curl -s "https://fromthebridge.net/api/v1/timeseries?metric_id=onchain.transfer.volume_usd&instrument_id=BTC" \
    -H "Authorization: Bearer $EXTERNAL_TIER_PROD_API_KEY" \
    | python3 -c "
      import sys, json
      r = json.load(sys.stdin)
      has_data = len(r.get('data', [])) > 0
      print('CoinMetrics data in external response:', has_data)
    "
```
**Expected result:** `CoinMetrics data in external response: False`
**Failure meaning:** CoinMetrics redistribution is pending ToS audit.
Serving this data externally before audit completes is a ToS risk.
**Failure action:** Verify redistribution=false is enforced for
coinmetrics in the serving layer filter. Do not deliver until confirmed.

---

**Criterion:** API key authentication and tier enforcement are working
in production
```
Check:
  # Test 1: unauthenticated request returns 401
  curl -s -o /dev/null -w "%{http_code}" \
    https://fromthebridge.net/api/v1/signals/BTC
  # Test 2: valid API key returns 200
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $PROD_API_KEY" \
    https://fromthebridge.net/api/v1/signals/BTC
  # Test 3: expired/invalid key returns 401
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer invalid_key_test_abc123" \
    https://fromthebridge.net/api/v1/signals/BTC
```
**Expected result:** Test 1: `401`. Test 2: `200`. Test 3: `401`.
**Failure meaning:** API is either open to unauthenticated access or
blocking valid customers.
**Failure action:** Verify API key middleware is active in production.
Verify Cloudflare tunnel routes /api/* to empire_api container.
Test key issuance and validation flow.

---

**Criterion:** First customer invoice is issued with real pricing
```
Check:
  # Manual verification — not automatable.
  # Confirm:
  # (a) Invoice has been prepared and sent to first customer.
  # (b) Invoice specifies real monetary amount (not $0, not free trial).
  # (c) Customer has acknowledged the pricing.
  # Record: customer identifier (internal), invoice date, invoice amount.
  echo "Invoice issued: [yes/no]"
  echo "Invoice date: [date]"
  echo "Customer acknowledged: [yes/no]"
```
**Expected result:** Invoice issued = yes. Real pricing = yes. 
Customer acknowledged = yes.
**Failure meaning:** MVP definition not met. The product is not
generating revenue.
**Failure action:** Direct customer engagement. Real pricing.
No free trials.

---

**Criterion:** First customer received at least one scheduled signal
delivery on time
```
Check:
  # Manual verification.
  # Confirm the signal was delivered to the customer on the agreed schedule.
  # Confirm delivery mechanism worked (webhook, email, or Telegram).
  # Record: delivery time, scheduled time, delta.
  echo "Signal delivered on schedule: [yes/no]"
  echo "Scheduled time: [time]"
  echo "Actual delivery time: [time]"
  echo "Delta: [minutes]"
```
**Expected result:** Signal delivered within ±30 minutes of scheduled time.
**Failure meaning:** The operational pipeline is not meeting delivery
commitments. First customer relationship is at risk.
**Failure action:** Diagnose delivery failure. Check Dagster run log
for the signal generation asset. Check webhook or delivery mechanism.

---

**Criterion:** BLC-01 ToS audit complete
```
Check:
  docker exec empire_postgres psql -U crypto_user -d crypto_structured \
    -c "SELECT canonical_name, tos_status, redistribution, audit_date
        FROM forge.source_catalog
        WHERE canonical_name = 'binance_blc01';"
```
**Expected result:** `tos_status` is not `'unaudited'`. `audit_date` is non-null.
**Failure meaning:** Binance tick liquidation data has been used in
signals that may be delivered to customers without knowing whether
redistribution is permitted.
**Failure action:** Complete Binance/BLC-01 ToS review. Determine
whether the tick liquidation endpoint permits redistribution of
derived signals. Document decision. If redistribution is blocked:
restrict BLC-01-derived features to internal signal use only.

---

#### Phase 6 Split Trigger Evaluation

Run these checks at Phase 6 gate. Record all measured values in the
gate completion report. Compare against Phase 1 baseline.

```
Component: MinIO (Bronze + Gold object storage)
Trigger metric: /mnt/empire-data disk usage
Threshold: 80% (3.2TB of 4TB)
How to measure: df -h /mnt/empire-data
Action if at/above: Execute migration to AWS S3.
  See thread_infrastructure.md: MinIO → AWS S3.
  Lead time: 1-2 days. Code changes: zero.
Phase 1 baseline: [from Phase 1 gate report]
Current measured value: [record at gate time]
Status: [Below / At / Above]

Component: ClickHouse (Silver observation store)
Trigger metric 1: Paying customer count
Threshold: 50 paying customers
How to measure: API auth logs — unique authenticated users per month
Action if at/above: Evaluate ClickHouse Cloud migration.
  See thread_infrastructure.md: ClickHouse → ClickHouse Cloud.
  Lead time: 1 week. Code changes: zero.
Current measured value: [record at gate time]
Status: [Below / At / Above]

Trigger metric 2: Sustained merge queue depth
Threshold: > 100 parts for > 7 consecutive days
How to measure:
  docker exec empire_clickhouse clickhouse-client \
    --query "SELECT count() FROM system.merges;"
Phase 1 baseline: [from Phase 1 gate report]
Current measured value: [record at gate time]
Status: [Below / At / Above]

Component: Dagster (orchestration)
Trigger metric: Operator time on Dagster infrastructure per month
Threshold: > 4 hours/month
How to measure: Operator judgment. Time tracked explicitly.
Action if at/above: Evaluate Dagster Cloud migration.
  See thread_infrastructure.md: Dagster → Dagster Cloud.
  Lead time: 1-2 days. Code changes: zero.
Phase 1 baseline: [from Phase 1 gate report]
Current measured value: [record at gate time]
Status: [Below / At / Above]

Component: PostgreSQL (catalog)
Trigger metric: Paying customer count
Threshold: 50 paying customers
How to measure: API auth logs — unique authenticated users per month
Action if at/above: Evaluate AWS RDS migration.
  See thread_infrastructure.md: PostgreSQL → AWS RDS.
  Lead time: 1 day. Code changes: zero.
Current measured value: [record at gate time]
Status: [Below / At / Above]
```

**Phase 6 gate passes when all 16 criteria above return expected results,
ToS audit table is complete with no unaudited rows, split trigger
evaluation is recorded, and first customer delivery has occurred.
Architect confirms.**

---

## INFRASTRUCTURE SPLIT TRIGGERS — REFERENCE TABLE

Numbers from thread_infrastructure.md: Resource Boundaries and
Managed Service Migration Triggers sections.

| Component | Current deployment | Managed equivalent | Trigger metric | Threshold | How to measure | Lead time | Code changes |
|---|---|---|---|---|---|---|---|
| MinIO | Self-hosted Docker on proxmox, /mnt/empire-data | AWS S3 | Paying customers OR disk usage | 50 customers OR /mnt/empire-data > 80% (3.2TB) | `df -h /mnt/empire-data`; API auth logs | 1–2 days | Zero — endpoint config swap only |
| ClickHouse | Self-hosted Docker on proxmox, /mnt/empire-db | ClickHouse Cloud | Paying customers OR sustained merge queue | 50 customers OR merge queue > 100 parts for > 7 consecutive days | `SELECT count() FROM system.merges`; API auth logs | 1 week (data migration) | Zero — connection string swap only |
| Dagster | Self-hosted Docker on proxmox | Dagster Cloud | Operator maintenance time | > 4 hours/month on Dagster infrastructure | Operator judgment — track explicitly | 1–2 days | Zero — asset definitions fully portable |
| PostgreSQL | Self-hosted Docker on proxmox, /mnt/empire-db | AWS RDS for PostgreSQL | Paying customers | 50 paying customers | API auth logs | 1 day | Zero — connection string swap only |

**All four migrations are zero-code-changes. Environment variable swaps only.**
**Trigger conditions are independent — any single threshold crossing
triggers evaluation of that component's migration. The four triggers
are not required to trigger simultaneously.**

---

## TIMELINE ESTIMATES (SINGLE OPERATOR)

| Phase | Estimate | Primary risk |
|---|---|---|
| Phase 0 | 3–5 days | ClickHouse DDL defects during round-trip testing |
| Phase 1 | 2–3 weeks | Migration adapter bugs; DeFiLlama backfill rate limits |
| Phase 2 | 2–3 weeks | PIT violations in audit; rolling window edge cases |
| Phase 3 | 1–2 weeks | Rule calibration; regime edge cases |
| Phase 4 | 3–4 weeks | Graduation criteria not met on first pass; shadow period is minimum 30 days regardless of other criteria |
| Phase 5 | 1–2 weeks | Webhook reliability; redistribution filter correctness |
| Phase 6 | 1–2 weeks | ToS audit findings requiring source exclusion or serving layer changes |
| **Total** | **13–20 weeks** | Shadow period minimum (Phase 4) is a floor that cannot be shortened. Migration data quality is the primary variance driver in Phase 1. |

---

## WHAT CARRIES FORWARD FROM PRIOR VERSIONS

| Decision | Outcome |
|---|---|
| Build sequence | Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 |
| Phase gate model | Hard pass/fail. No phase begins until previous gate passes. Architect confirms. |
| ML shadow period | Minimum 30 days. Extension if shadow evaluation fails consistency check. Clock resets on infrastructure failure or model retrain. |
| First customer | Phase 6 completion. Direct engagement. Real pricing. No free trials. |
| ToS audit timing | Phase 6. Before any external data product ships. Hard sequencing constraint. |
| Schema immutability | No schema changes after Phase 0 gate passes. New metrics and sources add catalog rows. Zero DDL. |

---

*Build plan authored: 2026-03-05. Gate criteria written against the deployed
stack: ClickHouse (Silver), Apache Iceberg on MinIO (Bronze, Gold),
Dagster, PostgreSQL (catalog only). All prior gate criteria are void.*
*Locked decisions require architect approval to reopen.*
*Next action: architect reviews done criteria checklist against this document.*
