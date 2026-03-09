# thread_5_collection.md
## FromTheBridge — Normalization & Collection
## Empire Architecture v2.0

**Date:** 2026-03-05
**Status:** Authoritative. Supersedes all prior Thread 5 content.
**Owner:** Stephen (architect, sole operator)
**Preceded by:** thread_infrastructure.md (technology decisions locked there — referenced by ADR number)
**Consumed by:** thread_6_build_plan.md §Phase 1 gate criteria

> All infrastructure technology decisions are locked in thread_infrastructure.md.
> This document does not reopen them. Build sessions reference ADR numbers where relevant.
> If a build requirement cannot be traced to a specification here or in thread_infrastructure,
> stop and surface it to the architect.

---

## WHAT THIS DOCUMENT COVERS

1. Dagster asset graph — asset count, partition model, freshness, dependency ordering, file sensor, instance configuration
2. Complete collector inventory — all 10 sources, all fields, no blanks
3. Bronze layer Iceberg specification — columns, partition transforms, append-only enforcement, 90-day retention, MinIO path layout
4. Great Expectations integration — extended catalog model, calibration mode, mechanical rule derivation, suite versioning
5. Per-source adapter specifications — all 10 sources with field mappings, cadence, known issues
6. BLC-01 specification — verified JSONL schema, aggregation formulas, window boundary, idempotency, file lifecycle, all failure modes
7. Forge decommission plan — current state, trigger, procedure
8. Migration plan — per-dataset with dependency ordering, verification criteria, rollback

---

## WHAT CARRIES FORWARD UNCHANGED

**The 10-responsibility adapter contract (verbatim from v1.0):**

1. Fetch data from source API (auth, rate limiting, pagination)
2. Write raw payload to Bronze Iceberg table (append-only, partitioned by source/date/metric)
3. Map source-specific field names to canonical metric names
4. Convert units to canonical units
5. Resolve source instrument identifiers to canonical `instrument_id`
6. Resolve source metric identifiers to canonical `metric_id`
7. Validate values against metric catalog definitions (range, type, nullability)
8. Write validated observations to ClickHouse `forge.observations`
9. Write rejected observations to ClickHouse `forge.dead_letter` with rejection code and raw payload
10. Write a run record to `agent_runs` on completion

**Also carries forward:** per-observation validation independence (one bad value does not fail the batch),
dead letter for every rejection with raw payload and rejection code, redistribution flags at source catalog
level, permanently excluded sources (Santiment, Glassnode, BSCScan, Solscan).

## WHAT CHANGES FROM v1.0

- Landing zone: Iceberg on MinIO — not schema-per-source PostgreSQL tables
- Orchestration: Dagster Software-Defined Assets — was absent entirely
- Collector inventory: verified from live pre-flight, fully classified, all 10 sources
- Great Expectations: fully mechanical from extended catalog with calibration mode — was absent
- BLC-01: complete specification from verified live JSONL schema — was absent
- Forge decommission: explicit trigger and procedure — was absent
- Migration plan: dependency-ordered, per-dataset with verification criteria — was thin

---

## CURRENT STATE (PRE-FLIGHT FINDINGS — 2026-03-05)

Before designing anything, pre-flight was executed against live infrastructure. Findings drive
design decisions in this document.

### Infrastructure Status

| Component | Status | Notes |
|---|---|---|
| Dagster | ❌ Not deployed | `empire_dagster` container does not exist. Clean Phase 1 build. |
| ClickHouse | ❌ Not deployed | `empire_clickhouse` container does not exist. Phase 0 item. |
| MinIO | ❌ Not deployed | `empire_minio` container does not exist. Phase 0 item. |
| PostgreSQL (5433) | ✅ Running | App state only. `forge.*` tables absent — live in empire_forge_db (5435). |
| empire_forge_db (5435) | ✅ Running | Legacy Forge database. Read-only 90-day safety net after Phase 1 gate. |

**Note:** The pre-flight commands in prompt_02 targeted `empire_postgres` (5433) for forge catalog
queries. All errored — the forge schema lives in `empire_forge_db` (5435). Pre-flight commands
for the Phase 1 build must target port 5435 for any Forge legacy queries.

### Forge Agent Reconciliation

| source_id | Forge agent | Agent status | Dagster asset | Action |
|---|---|---|---|---|
| coinalyze | forge_agent_coinalyze | ✅ Up 7 days | None yet | Continue until Phase 1 gate |
| defillama | forge_agent_defillama_ext | ✅ Up 7 days | None yet | Continue until Phase 1 gate |
| fred | forge_agent_fred | ✅ Up 3 days | None yet | Continue until Phase 1 gate |
| sosovalue | forge_agent_etf | ✅ Up 3 days | None yet | Continue until Phase 1 gate |
| explorer | forge_agent_explorer | ❌ **Not running** | None yet | Backfill from Etherscan V2 directly in Phase 1 |
| tiingo | None | N/A | None yet | Migration adapter in Phase 1 |
| coinpaprika | None | N/A | None yet | Migration adapter in Phase 1 |
| coinmetrics | None | N/A | None yet | Migration adapter in Phase 1 |
| bgeometrics | None | N/A | None yet | Build adapter in Phase 1 |
| binance_blc01 | LXC 203 collector | ✅ Active on Server2 | None yet | rsync routine + file sensor in Phase 1 |

**Explorer is down.** `forge_agent_explorer` is absent from docker ps. Duration unknown.
Exchange flows data has a gap of unknown length. The migration plan treats the Explorer dataset
as potentially stale — the new Explorer adapter backfills from Etherscan V2 API directly rather
than relying on the Forge dataset. The gap is accepted.

---

## 1. DAGSTER ASSET GRAPH

### Design Decision: Option B — Partitioned Assets

**One Dagster asset per (metric_id, source_id). Instruments are Dagster partitions within the asset.**

Instruments are not separate assets. A Coinalyze asset for `derivatives.perpetual.funding_rate`
has 121 partitions — one per instrument. A failed partition retries independently. Freshness is
tracked per partition. The asset graph stays at ~53 nodes and remains readable as the instrument
universe expands. Adding new instruments adds partition values to an existing asset, not new nodes.

Alternative considered and rejected: one asset per (metric_id, source_id, instrument_id).
At 121 instruments × 4 Coinalyze metrics alone, this produces 484 nodes from one source.
Full graph at Phase 1 exceeds 1,000 nodes. The Dagster UI becomes operationally unusable.
Partition model gives identical isolation semantics with a readable graph.

**Market-level metrics are unpartitioned.** FRED series, DeFiLlama aggregates, stablecoin totals,
and other market-level metrics have no instrument dimension — their assets run once per cadence
with no partition key.

### Asset Count — Phase 1

| Source | Assets | Partition type | Partition count |
|---|---|---|---|
| Coinalyze | 4 | instrument_id | 121 |
| DeFiLlama | 9 | instrument_id (protocols/stablecoins) or none | ~25 protocols, ~12 stablecoins, 3 unpartitioned |
| FRED | 24 | None (market-level) | — |
| Tiingo | 2 | instrument_id | Full instrument universe |
| SoSoValue | 2 | instrument_id (ETF products) | ~6 products |
| Explorer/Etherscan | 2 | instrument_id | 18 instruments |
| CoinPaprika | 3 | instrument_id or none | Full universe + 2 market-level |
| CoinMetrics | 1 | instrument_id | BTC + ETH only |
| BGeometrics | 4 | instrument_id | BTC + ETH only |
| Binance BLC-01 | 2 | instrument_id | 100+ symbols |
| **Total** | **53** | | |

**Asset count math:** 4 + 9 + 24 + 2 + 2 + 2 + 3 + 1 + 4 + 2 = **53 assets at Phase 1.**

ADR-005 estimate of "~200 at full buildout with 10 sources and ~50 metrics" assumes instrument-level
granularity (Option A). Under Option B the correct estimate is ~50–60 assets at Phase 1, growing
as new metrics are added to the catalog, not as instruments expand.

### Freshness Policies

Freshness policies derive from `cadence_hours` in `metric_catalog`. No freshness values are
hardcoded in Dagster configuration. The asset graph reads the catalog at startup and assigns
freshness policies dynamically.

```python
@asset(
    freshness_policy=FreshnessPolicy(
        maximum_lag_minutes=int(metric.cadence_hours * 60 * 1.5)
    )
)
```

The 1.5× multiplier gives a grace period before Dagster raises a freshness violation. A metric
with `cadence_hours = 8` gets `maximum_lag_minutes = 720` (12 hours). Freshness violations
appear in the Dagster UI as stale indicators — the primary 2am operational signal.

**Cadence reference:**

| Cadence | Sources |
|---|---|
| 8h | Coinalyze, BLC-01 (aggregated from daily file) |
| 24h | DeFiLlama, Tiingo, SoSoValue, Explorer, CoinPaprika, CoinMetrics, BGeometrics |
| 24h (file sensor) | BLC-01 (file completion triggers 8h aggregation job) |
| 24h (incremental) | FRED (daily update, incremental fetch) |
| Weekly | FRED series: FED_TOTAL_ASSETS, ECB_TOTAL_ASSETS, INITIAL_CLAIMS, yield spreads |
| Monthly | FRED series: NONFARM_PAYROLLS, MFG_EMPLOYMENT, CPI, CORE_PCE, M2, MONETARY_BASE, BOJ |
| Quarterly | FRED series: REAL_GDP_GROWTH |

**FRED staleness handling:** FRED reports '.' for missing values on weekends and holidays.
The adapter maps '.' → NULL with `SOURCE_MISSING_VALUE` annotation. These are structural gaps,
not data quality issues. The metric catalog `staleness_threshold` for FRED series is set to
accommodate the known reporting cadence — weekly series allow up to 10 days before triggering
`SOURCE_STALE` (accounts for holiday periods).

### Retry and Backoff

Retry and backoff are Dagster framework primitives — adapters do not implement their own.

```python
@asset(
    retry_policy=RetryPolicy(
        max_retries=3,
        delay=30,          # seconds
        backoff=Backoff.EXPONENTIAL
    )
)
```

After 3 retries: asset materialization fails. Dagster marks the asset as failed. The run record
in `agent_runs` is written with `status = 'failed'`. Freshness policy triggers a stale indicator
after `maximum_lag_minutes` passes without a successful materialization. No indefinite retry.

**Circuit breaker:** 3 consecutive failed materializations trigger a Dagster alert
(configured via Dagster's built-in alerting). Collection continues attempting on schedule.
No automatic source substitution. Health monitoring escalates to operator.

### Dependency Ordering from metric_lineage

The asset graph mirrors `metric_lineage` in the catalog. Assets that produce derived metrics
(e.g., `defi.aggregate.tvl_usd` aggregated from `defi.protocol.tvl_usd`) declare upstream
dependencies in Dagster. Dagster enforces materialization order — derived assets do not
run until upstream assets are fresh.

```python
@asset(deps=[defillama_protocol_tvl_asset])
def defillama_aggregate_tvl():
    ...
```

Assets with no catalog dependencies (all raw collection assets) have no Dagster upstream
dependencies — they run on their freshness schedule independently.

### File Sensor — BLC-01

BLC-01 is not wall-clock scheduled. It is triggered by a Dagster file sensor watching the
rsync landing directory on proxmox.

**Watched directory:** `/mnt/empire-data/blc01/landing/`
(rsync from Server2 deposits files here — see BLC-01 section for rsync specification)

**File pattern:** `*.jsonl.complete`

Active files use the extension `.jsonl`. The collector on LXC 203 renames the file to
`.jsonl.complete` at midnight UTC when the day's collection is closed. The file sensor
watches only for `.complete` files — it never processes an active `.jsonl` file.

**Sensor tick frequency:** 60 seconds. At midnight UTC, the completed file appears within
the rsync interval. The sensor detects it within 60 seconds and triggers the BLC-01
aggregation asset.

**Sensor configuration:**

```python
@sensor(
    job=blc01_ingest_job,
    minimum_interval_seconds=60
)
def blc01_file_sensor(context):
    landing_dir = "/mnt/empire-data/blc01/landing"
    for filename in os.listdir(landing_dir):
        if filename.endswith(".jsonl.complete"):
            date_str = filename.replace(".jsonl.complete", "")
            run_key = f"blc01_{date_str}"
            if not context.instance.has_run_with_tags({"blc01_date": date_str}):
                yield RunRequest(
                    run_key=run_key,
                    run_config={"ops": {"blc01_ingest": {"config": {
                        "file_path": os.path.join(landing_dir, filename),
                        "date": date_str
                    }}}}
                )
```

`run_key` is the idempotency mechanism — Dagster will not re-trigger a run for a
`run_key` it has already processed. If the same `.complete` file appears again (e.g.,
after a failed run that was manually cleared), the sensor triggers again only if the
run key is not in Dagster's history.

### Dagster Instance Configuration

Three Docker containers per ADR-005:

| Container | Role | Port | Persists across restart |
|---|---|---|---|
| `empire_dagster_webserver` | UI | 3010 (LAN only) | Run history (SQLite) |
| `empire_dagster_daemon` | Schedules, sensors, runs | — | Schedule state (SQLite) |
| `empire_dagster_code` | Asset definitions | — | Nothing — rebuilt from code |

**Metadata storage:** SQLite on `/mnt/empire-db/dagster/`. Backed up to NAS per the
infrastructure backup schedule. Dagster metadata (run history) is a log — loss is
operationally inconvenient but does not affect source data. Cold restart procedure
in thread_infrastructure §Cold-Start Sequence step 5.

**What is lost on restart without metadata backup:** Run history (which assets ran,
when, with what result). Schedule state (next scheduled run times). The asset definitions
themselves are in code — they reload immediately on code server restart.

**What is NOT lost on restart:** Bronze data (MinIO), Silver data (ClickHouse), Gold data
(MinIO), catalog data (PostgreSQL). All source data is outside Dagster's scope.

**Asset definitions load from:** `/opt/empire/pipeline/` on the code server container.
This is the rsync target from bluefin. Asset definitions are reloaded without restart
using Dagster's code location reload: Dagster UI → Deployment → Reload.

---

## 2. COMPLETE COLLECTOR INVENTORY

All 10 sources. All fields populated. No blanks.

| Field | Description |
|---|---|
| source_id | Canonical identifier in source_catalog |
| name | Human-readable name |
| metric_domains | Canonical metric domains covered |
| cadence_h | Collection interval in hours (decimal for sub-hourly) |
| collection_type | api_rest / api_websocket / file_sensor / csv_bulk |
| auth_type | none / api_key / bearer / oauth2 |
| rate_limit | Requests per minute or per day as documented |
| tos_status | unaudited / low_risk / paid_commercial / non_commercial |
| redistribution | yes / no / pending_audit |
| current_status | active_forge / active_new / inactive / planned |
| migration_priority | 1 (first) → 10 (last or deferred) |
| dependencies | Other source_ids that must be ingested first |

---

### Coinalyze

| Field | Value |
|---|---|
| source_id | `coinalyze` |
| name | Coinalyze |
| metric_domains | derivatives.perpetual |
| cadence_h | 8 |
| collection_type | api_rest |
| auth_type | api_key |
| rate_limit | Verify in integration test — not publicly documented |
| tos_status | unaudited |
| redistribution | pending_audit (Phase 6) |
| current_status | active_forge |
| migration_priority | 2 (after Tiingo — spot price needed by Explorer adapter) |
| dependencies | tiingo |

---

### DeFiLlama

| Field | Value |
|---|---|
| source_id | `defillama` |
| name | DeFiLlama |
| metric_domains | defi.protocol, defi.aggregate, defi.dex, stablecoin.supply, stablecoin.peg, defi.protocol.fees, defi.protocol.revenue |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | none |
| rate_limit | No documented limit. Conservative: 10 req/min. |
| tos_status | low_risk |
| redistribution | yes |
| current_status | active_forge |
| migration_priority | 3 |
| dependencies | none |

---

### FRED

| Field | Value |
|---|---|
| source_id | `fred` |
| name | Federal Reserve Economic Data |
| metric_domains | macro.rates, macro.fx, macro.credit, macro.equities, macro.volatility, macro.employment, macro.inflation, macro.money, macro.cb, macro.gdp |
| cadence_h | 24 (incremental fetch — only observations since last_fetched_at) |
| collection_type | api_rest |
| auth_type | api_key |
| rate_limit | 120 req/min |
| tos_status | none (public domain) |
| redistribution | yes |
| current_status | active_forge |
| migration_priority | 4 |
| dependencies | none |

**Phase 1 corrective actions before FRED adapter build:**
- Add `macro.credit.hy_oas` to metric_catalog (FRED series `BAMLH0A0HYM2`) — currently in feature catalog, not metric catalog
- Add `macro.employment.mfg_employment` to metric_catalog (FRED series `MANEMP`) — present in live adapter, absent from design documents

---

### Tiingo

| Field | Value |
|---|---|
| source_id | `tiingo` |
| name | Tiingo |
| metric_domains | spot.price, spot.volume |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | api_key (paid commercial tier) |
| rate_limit | Tier-dependent. Verify against paid account. |
| tos_status | paid_commercial |
| redistribution | yes (paid) |
| current_status | active_forge |
| migration_priority | 1 (first — spot price dependency for Explorer wei conversion) |
| dependencies | none |

**Known issue:** Equity volume is in shares, not USD. Adapter branches on `asset_class`:
if `asset_class == 'equity'`: `volume_usd = volume_shares × close_price`. Crypto volume
is already in USD.

---

### SoSoValue

| Field | Value |
|---|---|
| source_id | `sosovalue` |
| name | SoSoValue |
| metric_domains | etf.flows, etf.aum |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | api_key |
| rate_limit | Not publicly documented. Treat conservatively: 30 req/min. |
| tos_status | non_commercial |
| redistribution | **no** — hard constraint, non-commercial ToS |
| current_status | active_forge |
| migration_priority | 5 |
| dependencies | none |

**Hard constraint:** `redistribution = false` in source_catalog. This field is enforced at
the serving layer — any query that would return SoSoValue-sourced observations is filtered
before it reaches an external API response. This enforcement must be verified as a Phase 5
gate criterion. SoSoValue data is available for internal signal computation only.

---

### Explorer (Etherscan V2)

| Field | Value |
|---|---|
| source_id | `explorer` |
| name | Etherscan V2 / Explorer |
| metric_domains | flows.exchange |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | api_key |
| rate_limit | 5 req/sec (free tier), 10 req/sec (paid) |
| tos_status | unaudited |
| redistribution | pending_audit (Phase 6) |
| current_status | **inactive** (forge_agent_explorer not running — duration unknown) |
| migration_priority | 6 (backfill from API directly — not from Forge dataset) |
| dependencies | tiingo (spot price needed for wei→USD conversion on Gate.io) |

**Known issue — Gate.io wei bug:** Gate.io exchange flow values are returned in wei, not ETH.
Confirmed bug. Adapter applies: `eth_value = wei_value / 1e18`, then
`usd_value = eth_value × spot.price.close_usd`. Raw Bronze record preserves the original
wei value. The spot price is fetched from the canonical store — `spot.price.close_usd` for
the relevant instrument must exist before Explorer migration begins. This is the Tiingo
dependency.

**Coverage:** 9 exchanges, 18 instruments, ETH + Arbitrum chains only.

---

### CoinPaprika

| Field | Value |
|---|---|
| source_id | `coinpaprika` |
| name | CoinPaprika |
| metric_domains | spot.market_cap, spot.dominance |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | none (free tier for market cap data) |
| rate_limit | 25,000 req/month on free tier |
| tos_status | low_risk |
| redistribution | yes |
| current_status | planned |
| migration_priority | 7 |
| dependencies | none |

**Purpose:** Primary source for `spot.market_cap.usd` (per instrument), `spot.market_cap.total_crypto_usd`, and `spot.dominance.btc_pct`. Tiingo does not provide market cap. CoinPaprika fills this gap.

---

### CoinMetrics

| Field | Value |
|---|---|
| source_id | `coinmetrics` |
| name | CoinMetrics |
| metric_domains | flows.onchain |
| cadence_h | 24 |
| collection_type | csv_bulk (GitHub CSV files — community edition) |
| auth_type | none (community edition) |
| rate_limit | GitHub rate limits apply to CSV fetches |
| tos_status | unaudited |
| redistribution | **no** — pending Phase 6 ToS audit |
| current_status | planned |
| migration_priority | 8 |
| dependencies | none |

**Coverage:** `flows.onchain.transfer_volume_usd` for BTC and ETH only (community edition).
`redistribution = false` in source_catalog. Same enforcement as SoSoValue — serving layer
filters before external API response.

---

### BGeometrics

| Field | Value |
|---|---|
| source_id | `bgeometrics` |
| name | BGeometrics |
| metric_domains | onchain.valuation |
| cadence_h | 24 |
| collection_type | api_rest |
| auth_type | api_key |
| rate_limit | Not publicly documented. Verify in integration test. |
| tos_status | unaudited |
| redistribution | pending_audit (Phase 6) |
| current_status | planned |
| migration_priority | 9 |
| dependencies | none |

**Metrics provided:** MVRV, SOPR, NUPL, Puell Multiple — BTC and ETH only.
Canonical metric names to add to metric_catalog before Phase 1 BGeometrics adapter build:
`onchain.valuation.mvrv` · `onchain.valuation.sopr` · `onchain.valuation.nupl` · `onchain.valuation.puell_multiple`

---

### Binance BLC-01

| Field | Value |
|---|---|
| source_id | `binance_blc01` |
| name | Binance Liquidation Events (BLC-01) |
| metric_domains | derivatives.perpetual (liquidations only) |
| cadence_h | 24 (daily file) / 8 (aggregated output windows) |
| collection_type | file_sensor |
| auth_type | none (Binance WebSocket, no auth) |
| rate_limit | N/A — file-based, not API |
| tos_status | unaudited |
| redistribution | pending_audit (Phase 6) |
| current_status | active on Server2, rsync unbuilt |
| migration_priority | 10 (Phase 1, after rsync routine built) |
| dependencies | none |

---

## 3. BRONZE LAYER — ICEBERG SPECIFICATION

### Column Definitions

Every Bronze record is one row in the Iceberg table. Raw payload is preserved exactly.
No transformation occurs before Bronze write.

| Column | Type | Justification |
|---|---|---|
| `source_id` | STRING | Partition key. Identifies the collection source. Enables partition pruning when auditing a single source. |
| `metric_id` | STRING | Partition key. Enables partition pruning when auditing a single metric. UUID stored as string — Iceberg STRING avoids UUID type variance across engines. |
| `instrument_id` | STRING NULLABLE | NULL for market-level metrics. UUID as string. |
| `collection_date` | DATE | Partition key. Calendar date of collection (UTC). Enables retention enforcement via date-range partition deletion. |
| `observed_at` | TIMESTAMP (microseconds, UTC) | The timestamp the value represents — not when it was collected. Maps to `valid_from` in Silver. |
| `collected_at` | TIMESTAMP (microseconds, UTC) | When the adapter fetched this record from the source. |
| `raw_payload` | STRING | Complete source response for this record, JSON-serialized. Preserves original field names, original units, original values including any that fail validation. |
| `payload_hash` | STRING | SHA-256 of `raw_payload`. Idempotency check — duplicate Bronze writes detected by `(source_id, metric_id, instrument_id, observed_at, payload_hash)`. |
| `adapter_version` | STRING | Semver string of the adapter that produced this record. Enables audit of which adapter version collected which data. |
| `schema_version` | INTEGER | Iceberg schema version at write time. For time travel queries — ensures the reader knows which schema evolution was in effect. |

**Why STRING for IDs and not UUID type:** Iceberg's UUID logical type maps differently
across engines (DuckDB, PyIceberg, Spark). STRING is universally readable and avoids
type coercion bugs when the same table is read by multiple tools over its lifetime.

**Why TIMESTAMP at microsecond precision:** Source APIs return timestamps at varying precision —
milliseconds (Coinalyze `open_time`), seconds (FRED), microseconds (BLC-01 `_received_at`).
Microsecond precision preserves the finest-grained source timestamps without loss. Truncation
to lower precision is a transform — it happens in the adapter's Silver write, not in Bronze.

### Partition Transform

**Bronze:** `(source_id, collection_date, metric_id)`

Partition transform is `identity` on `source_id` and `metric_id`, `days` on `collection_date`.

**Justification against actual query patterns:**

The two queries Bronze must serve efficiently are:

1. **Audit query** — "Show me all raw records for Coinalyze funding_rate on 2026-03-04."
   Partition pruning on `(source_id, collection_date, metric_id)` reduces scan to exactly
   one partition. Without this partition structure, the audit query would scan all Bronze data.

2. **Retention enforcement** — "Delete all partitions older than 90 days."
   The `days` transform on `collection_date` means each day is a partition. Retention
   drops entire date partitions — a single metadata operation per day, not a row-level delete.

The alternative `(source_id, metric_id, month)` partition would be coarser — monthly retention
enforcement overshoots by up to 30 days. `(source_id, collection_date)` without `metric_id`
would require full partition scans for audit queries on a specific metric. The three-column
partition is the minimum that satisfies both query patterns.

**Gold:** `(metric_id, month)` — derived from thread_infrastructure ADR-002. Not repeated here.

### Append-Only Enforcement

Iceberg's append-only constraint is enforced at the table level:

```python
from pyiceberg.catalog import load_catalog
from pyiceberg.table import TableProperties

catalog = load_catalog("bronze", **minio_config)
table = catalog.load_table("forge.bronze")

# Enforce append-only at write time — no UPDATE, DELETE, or MERGE operations
table.append(records_as_arrow_batch)
# Never: table.overwrite(), table.delete(), table.merge()
```

There is no DDL-level append-only constraint in Iceberg (unlike ClickHouse `ReplacingMergeTree`).
Append-only is enforced by convention — adapters call only `table.append()`. The only process
permitted to delete Bronze data is the retention job, which calls
`table.expire_snapshots(older_than_ms=...).commit()` — this is snapshot expiry, not row deletion.

**Violation detection:** Any call to `table.overwrite()` or `table.delete()` on Bronze is a
code error. The Iceberg transaction log records all operations — `git log`-style audit of
every write to the table. Unexpected operation types in the log surface in code review.

### 90-Day Retention

Retention is enforced by a dedicated Dagster asset on a daily schedule (02:00 UTC).

```python
@asset(schedule="0 2 * * *")
def bronze_retention_job():
    catalog = load_catalog("bronze", **minio_config)
    table = catalog.load_table("forge.bronze")
    cutoff_ms = int((datetime.utcnow() - timedelta(days=90)).timestamp() * 1000)
    table.expire_snapshots(older_than_ms=cutoff_ms).commit()
    # Also clean up orphaned data files not referenced by any snapshot
    table.clean_orphan_files(older_than_ms=cutoff_ms).commit()
```

**What `expire_snapshots` does:** Marks snapshots older than the cutoff as expired.
The data files referenced only by expired snapshots become eligible for deletion.
`clean_orphan_files` removes them from MinIO.

**What it does NOT do:** Delete data files still referenced by a live snapshot.
If a snapshot at day 89 references a data file written at day 45, that file is
retained until the day-89 snapshot also expires. In practice, Bronze partitions
are written once (append) and never updated — each day's partition is referenced
by exactly one snapshot. Retention is predictably 90 days.

**Storage impact:** At projected volumes (~17 GB/year total), 90 days of Bronze is
approximately 4 GB. Well within `/mnt/empire-data` capacity.

### MinIO Path Layout

```
s3://bronze/
  forge/
    bronze/
      data/
        source_id=coinalyze/
          collection_date=2026-03-05/
            metric_id=<uuid>/
              00000-<uuid>.parquet
              00001-<uuid>.parquet
        source_id=defillama/
          collection_date=2026-03-05/
            metric_id=<uuid>/
              00000-<uuid>.parquet
        source_id=binance_blc01/
          collection_date=2026-03-05/
            metric_id=<uuid>/
              00000-<uuid>.parquet
      metadata/
        v1.metadata.json
        v2.metadata.json
        snap-<id>-1-<uuid>.avro

s3://gold/
  forge/
    gold/
      data/
        metric_id=<uuid>/
          month=2026-03/
            00000-<uuid>.parquet
      metadata/
        ...
```

**S3 compatibility:** The path layout is S3-compatible by construction. MinIO uses the S3 API.
When cloud migration triggers, `MINIO_ENDPOINT` changes to the AWS S3 regional endpoint.
All paths remain identical — S3 uses the same `s3://bucket/prefix/` scheme.
Zero code changes required. Confirmed per ADR-003.

---

## 4. GREAT EXPECTATIONS INTEGRATION

### Extended Source Catalog — Structural Validation Fields

The existing `source_catalog` is extended with the following fields to enable fully mechanical
GE suite generation:

```sql
ALTER TABLE forge.source_catalog ADD COLUMN expected_instruments_min     INTEGER;
ALTER TABLE forge.source_catalog ADD COLUMN expected_instruments_max     INTEGER;
ALTER TABLE forge.source_catalog ADD COLUMN expected_rows_per_run_min    INTEGER;
ALTER TABLE forge.source_catalog ADD COLUMN required_coverage_pct        NUMERIC(5,2);
ALTER TABLE forge.source_catalog ADD COLUMN validation_status            TEXT
    NOT NULL DEFAULT 'calibrating'
    CHECK (validation_status IN ('calibrating', 'active'));
ALTER TABLE forge.source_catalog ADD COLUMN calibration_runs_required    INTEGER
    NOT NULL DEFAULT 3;
ALTER TABLE forge.source_catalog ADD COLUMN calibration_runs_completed   INTEGER
    NOT NULL DEFAULT 0;
```

**Semantics:**

- `expected_instruments_min/max` — bounds on the number of distinct instruments per run
- `expected_rows_per_run_min` — minimum total observations expected per collection run
- `required_coverage_pct` — percentage of registered instruments that must appear in each run
- `validation_status` — `calibrating` (GE not yet enforcing) or `active` (GE enforcing)
- `calibration_runs_required` — how many runs before parameters are computed and enforcement activates
- `calibration_runs_completed` — how many calibration runs have completed so far

**Initial seed values at Phase 1:**

| source_id | calibration_runs_required | Note |
|---|---|---|
| coinalyze | 3 | Stable source, consistent instrument count |
| defillama | 3 | Stable |
| fred | 3 | Stable — series count never changes |
| tiingo | 3 | Stable |
| sosovalue | 3 | Stable |
| explorer | 3 | Stable |
| coinpaprika | 3 | Stable |
| coinmetrics | 3 | Stable |
| bgeometrics | 3 | Stable |
| binance_blc01 | **7** | High daily variance (15k–70k+ events). 7 days captures range. |

### Calibration Mode

New sources (or sources newly onboarded to the new system) enter calibration mode on first
run. During calibration:

- GE validation is skipped (Bronze → Silver writes proceed without GE check)
- Every run records: instrument count, total row count, coverage percentage into an
  `agent_run_calibration` table
- `calibration_runs_completed` increments after each successful run

After `calibration_runs_required` successful runs, a Dagster asset computes parameters and
writes them to `source_catalog`:

```python
def compute_calibration_params(source_id: str, runs: list[dict]) -> dict:
    instrument_counts = [r["instrument_count"] for r in runs]
    row_counts = [r["row_count"] for r in runs]
    coverage_pcts = [r["coverage_pct"] for r in runs]

    return {
        "expected_instruments_min": int(min(instrument_counts) * 0.90),
        "expected_instruments_max": int(max(instrument_counts) * 1.10),
        "expected_rows_per_run_min": int(min(row_counts) * 0.90),
        "required_coverage_pct": round(min(coverage_pcts) * 0.95, 2),
        "validation_status": "active",
    }
```

The operator can review and override computed parameters before enforcement activates.
The Dagster UI shows any source with `validation_status = 'calibrating'` in the asset
metadata. Override by direct `UPDATE` to `source_catalog` before the calibration asset runs.

### Mechanical Rule Derivation — Worked Example

**Catalog row:** `derivatives.perpetual.funding_rate`

```yaml
canonical_name:      derivatives.perpetual.funding_rate
value_type:          numeric
expected_range_low:  -0.05
expected_range_high:  0.05
is_nullable:         false
staleness_threshold: 16 hours   # 2× cadence of 8h
```

**Generated GE expectations:**

```python
def generate_expectations_for_metric(metric: dict) -> list[Expectation]:
    expectations = []

    # Type check
    expectations.append(
        expect_column_values_to_be_of_type("value_numeric", "float")
    )

    # Null check (when is_nullable = false)
    if not metric["is_nullable"]:
        expectations.append(
            expect_column_values_to_not_be_null("value_numeric")
        )

    # Range check (when bounds defined)
    if metric["expected_range_low"] is not None or metric["expected_range_high"] is not None:
        expectations.append(
            expect_column_values_to_be_between(
                "value_numeric",
                min_value=metric["expected_range_low"],
                max_value=metric["expected_range_high"]
            )
        )

    # Staleness check
    staleness_hours = metric["staleness_threshold_hours"]
    expectations.append(
        expect_column_max_to_be_between(
            "observed_at",
            min_value=datetime.utcnow() - timedelta(hours=staleness_hours),
            max_value=datetime.utcnow()
        )
    )

    return expectations
```

For `derivatives.perpetual.funding_rate` this produces four expectations:
1. `value_numeric` is float
2. `value_numeric` is not null
3. `value_numeric` is between -0.05 and 0.05
4. Most recent `observed_at` is within 16 hours of now

**Structural expectations from source_catalog** (generated when `validation_status = 'active'`):

```python
def generate_structural_expectations(source: dict) -> list[Expectation]:
    expectations = []

    if source["expected_instruments_min"] is not None:
        expectations.append(
            expect_table_row_count_to_be_between(
                min_value=source["expected_rows_per_run_min"]
            )
        )
        expectations.append(
            expect_column_distinct_values_to_be_in_set_size_to_be_between(
                "instrument_id",
                min_value=source["expected_instruments_min"],
                max_value=source["expected_instruments_max"]
            )
        )

    return expectations
```

**Complete suite for a Coinalyze run:** Metric-level expectations for all 4 metrics × 121 instruments,
plus structural expectations checking instrument count (expected ~121, bounded ±10%) and
total row count. The suite generates from catalog state at validation time — no hand-written rules.

### Pipeline Placement

GE validation runs at the **Bronze → Silver boundary**, inside the adapter, after Bronze write
and before Silver write. This is the correct placement because:

- Bronze has already been written — the raw payload is preserved regardless of validation outcome
- Validation failure dead-letters the observation to ClickHouse `forge.dead_letter` — Silver is
  never written with invalid data
- GE runs against the Bronze record, not the Silver-bound object — the raw payload is the ground truth

```
Adapter flow:
  fetch from source API
      ↓
  write raw payload to Bronze (always succeeds if Bronze is reachable)
      ↓
  map fields + convert units
      ↓
  run GE expectations per observation
      ↓ pass                    ↓ fail
  write to Silver            write to dead_letter
  (forge.observations)       (forge.dead_letter + rejection code)
```

### Failure Behavior

**A single bad value does not fail the batch.** GE validation is per-observation and independent.

The adapter iterates observations. For each:
- GE pass → observation queued for Silver batch write
- GE fail → observation written to dead_letter immediately; iteration continues

After all observations are processed: Silver batch write executes for all passing observations.
The run record in `agent_runs` includes:
- `total_observations`: total fetched
- `observations_passed`: written to Silver
- `observations_rejected`: written to dead_letter
- `rejection_rate`: rejected / total

A run with any rejections still completes with `status = 'success'` provided the rejection
rate is below 5%. Above 5% rejection rate: `status = 'degraded'`. The Dagster freshness
policy is not affected by `degraded` status — the asset is considered materialized.
The operator is alerted via Dagster's alerting channel.

### Suite Versioning Procedure

When `metric_catalog` gains a new metric:

1. Add catalog row for the new metric (canonical name, type, range, nullability, staleness)
2. The GE generator reads metric_catalog at runtime — no suite file to update
3. Verification: run `generate_expectations_for_metric(new_metric)` in a test and confirm
   the expected expectations are produced
4. The new expectations are active on the next adapter run

When a metric's range bounds change in `metric_catalog`:

1. Update the catalog row
2. The change takes effect on the next adapter run — no suite versioning required
3. If the change is significant (e.g., tightening the range after previously-passing observations
   would now fail): run the Bronze audit query for that metric to understand how many historical
   observations would have been rejected under the new rule. Log the decision.

When a source's structural parameters change (e.g., Coinalyze adds instruments):

1. The `calibration_runs_completed` counter is manually reset to 0 and `validation_status` set
   back to `calibrating` for that source
2. Calibration runs again and recomputes the structural bounds
3. The operator reviews computed parameters before `validation_status` returns to `active`

---

## 5. PER-SOURCE ADAPTER SPECIFICATIONS

### 5.1 Coinalyze

**Provides:** `derivatives.perpetual.funding_rate`, `derivatives.perpetual.open_interest_usd`,
`derivatives.perpetual.liquidations_long_usd`, `derivatives.perpetual.liquidations_short_usd`
(121 instruments)

**Cadence:** 8h. Collection offset 5 minutes past settlement: 00:05, 08:05, 16:05 UTC.

**Field mappings:**

| Source field | Canonical metric | Unit conversion | Notes |
|---|---|---|---|
| `funding_rate` | `derivatives.perpetual.funding_rate` | None | Rate per 8h period. Range [-0.05, 0.05]. |
| `open_interest_usd` | `derivatives.perpetual.open_interest_usd` | None | Use USD field. Do NOT use contracts field. Verify in integration test — units vary by endpoint. |
| `long_liquidations` | `derivatives.perpetual.liquidations_long_usd` | Verify in integration test — may be USD or contracts | If contracts: multiply by `perpetual.price_usd` |
| `short_liquidations` | `derivatives.perpetual.liquidations_short_usd` | Same | Same |
| `open_time` | `observed_at` (via `valid_from`) | Unix ms → TIMESTAMPTZ: `datetime.fromtimestamp(open_time / 1000, tz=UTC)` | |

**`derivatives.perpetual.price_usd` gap:** Coinalyze perpetual price not confirmed available.
Verify in integration test. If absent: add Binance perpetual price collection as a separate
adapter targeting `derivatives.perpetual.price_usd`.

**Known extreme value instruments:**
ANKR, FRAX, OGN have historically produced extreme funding rate values outside the [-0.05, 0.05]
range. These are not dead-lettered as `RANGE_VIOLATION` — they are dead-lettered as
`EXTREME_VALUE_PENDING_REVIEW` and queued for manual review. They are not silently rejected
or silently passed. The manual review queue is a view on `forge.dead_letter` filtered by
`rejection_code = 'EXTREME_VALUE_PENDING_REVIEW'`.

### 5.2 DeFiLlama

**Provides:** Protocol TVL, DEX volume, stablecoin supply/peg, lending proxy, fees, revenue

**Cadence:** 24h at 06:00 UTC. Three separate collection jobs.

**Three collection jobs and their Bronze partitions:**

| Job | Metric | Partition |
|---|---|---|
| defillama_protocols | `defi.protocol.tvl_usd`, `defi.protocol.fees_usd_24h`, `defi.protocol.revenue_usd_24h` | protocol slug → instrument_id |
| defillama_dex | `defi.dex.volume_usd_24h`, `defi.aggregate.tvl_usd` | market-level (instrument_id = NULL) |
| defillama_stablecoins | `stablecoin.supply.per_asset_usd`, `stablecoin.supply.total_usd`, `stablecoin.peg.price_usd` | stablecoin asset → instrument_id |

**Field mappings:**

| Source field | Canonical metric | Notes |
|---|---|---|
| `tvl` (protocol endpoint) | `defi.protocol.tvl_usd` | instrument_id = protocol slug resolved to instruments.instrument_id |
| Sum of protocol tvl | `defi.aggregate.tvl_usd` | Computed by adapter across all protocol rows. instrument_id = NULL. |
| `volume24h` (DEX endpoint) | `defi.dex.volume_usd_24h` | Market-level. instrument_id = NULL. |
| `fees24h` | `defi.protocol.fees_usd_24h` | Add to metric_catalog before Phase 1 build. |
| `revenue24h` | `defi.protocol.revenue_usd_24h` | Add to metric_catalog before Phase 1 build. |
| `circulating` (stablecoin endpoint) | `stablecoin.supply.per_asset_usd` | Per stablecoin asset. |
| Sum of circulating | `stablecoin.supply.total_usd` | Computed by adapter. instrument_id = NULL. |
| `price` | `stablecoin.peg.price_usd` | Range [0.90, 1.10]. Values outside = dead_letter as `RANGE_VIOLATION`. |

**Lending utilization proxy:** DeFiLlama does not provide utilization rate directly.
v1 adapter computes: `defi.lending.utilization_proxy = borrow_tvl / supply_tvl`.
Canonical metric name: `defi.lending.utilization_proxy` (distinguished from the v1.1
metric `defi.lending.utilization_rate` which will come from subgraph data).

**Known issue:** Protocol slugs change on rebrands. Maintain a normalization map in the
instruments catalog: `asset_aliases` table stores the old slug → canonical instrument_id
mapping. The adapter resolves via alias lookup before direct lookup.

**Backfill required:** DeFiLlama has shallow history in the Forge dataset (195 protocol rows,
180 stablecoin rows — both marked SHALLOW in migration table). The new adapter must run a
full historical backfill via DeFiLlama's historical API before live collection starts.
Backfill rate limit: 10 req/min conservative. Backfill is a separate Dagster job, not the
live collection asset.

### 5.3 FRED

**Provides:** 24 macro series (23 live + HY_OAS gap fill)

**Cadence:** 24h at 18:00 UTC, incremental. Only observations since `last_fetched_at`
are requested. FRED's `observation_start` parameter set to `last_fetched_at` date.

**Complete series mapping:**

| Canonical metric | FRED series_id | Release cadence | History from |
|---|---|---|---|
| `macro.rates.fed_funds_effective` | `EFFR` | Daily | 1954 |
| `macro.rates.yield_10y` | `DGS10` | Daily | 1962 |
| `macro.rates.yield_2y` | `DGS2` | Daily | 1976 |
| `macro.rates.yield_30y` | `DGS30` | Daily | 1977 |
| `macro.rates.yield_10y_2y_spread` | `T10Y2Y` | Daily | 1976 |
| `macro.rates.yield_10y_3m_spread` | `T10Y3M` | Daily | 1982 |
| `macro.rates.real_yield_10y` | `DFII10` | Daily | 2003 |
| `macro.rates.breakeven_inflation_10y` | `T10YIE` | Daily | 2003 |
| `macro.equities.sp500` | `SP500` | Daily | 2016 |
| `macro.volatility.vix` | `VIXCLS` | Daily | 1990 |
| `macro.fx.wti_crude` | `DCOILWTICO` | Daily | 1986 |
| `macro.fx.dxy` | `DTWEXBGS` | Daily (3-day lag) | 2006 |
| `macro.employment.nonfarm_payrolls` | `PAYEMS` | Monthly | 1950 |
| `macro.employment.initial_claims` | `ICSA` | Weekly | 1967 |
| `macro.employment.mfg_employment` | `MANEMP` | Monthly | 1950 |
| `macro.inflation.cpi_all_urban` | `CPIAUCSL` | Monthly | 1950 |
| `macro.inflation.core_pce` | `PCEPILFE` | Monthly | 1959 |
| `macro.money.m2_supply` | `M2SL` | Monthly | 1959 |
| `macro.money.monetary_base` | `BOGMBASE` | Monthly | 1959 |
| `macro.cb.fed_total_assets` | `WALCL` | Weekly | 2002 |
| `macro.cb.ecb_total_assets` | `ECBASSETSW` | Weekly | 1999 |
| `macro.cb.boj_total_assets` | `BOJASSETS` | Monthly | 1998 |
| `macro.gdp.real_growth` | `A191RL1Q225SBEA` | Quarterly | 1950 |
| `macro.credit.hy_oas` | `BAMLH0A0HYM2` | Daily | ~1997 |

**`macro.equities.sp500` history note:** FRED's SP500 series starts 2016 due to licensing
constraints. This is a known shallow history. For ML training requiring deeper SP500 history,
a supplementary source must be identified at v1.1. Signal-eligible use at Phase 1 is constrained
to post-2016 periods for any feature using this series.

**Missing value handling:** FRED returns the string `'.'` for missing values (weekends, holidays,
release lags). Adapter maps `'.'` → `NULL` with annotation `SOURCE_MISSING_VALUE` in the
dead_letter record. These are structural gaps — GE staleness checks are calibrated to
tolerate known reporting lags (DXY: up to 4 days, monthly series: up to 35 days).

**Staleness thresholds for FRED metrics by release cadence:**

| Release cadence | staleness_threshold |
|---|---|
| Daily | 4 days (weekend + holiday buffer) |
| Weekly | 10 days (holiday period buffer) |
| Monthly | 35 days (late-release buffer) |
| Quarterly | 100 days |

### 5.4 Tiingo

**Provides:** `spot.price.close_usd`, `spot.volume.usd_24h`

**Cadence:** 24h at 02:00 UTC (after global market settlement)

**Field mappings:**

| Source field | Canonical metric | Unit conversion |
|---|---|---|
| `close` | `spot.price.close_usd` | None for crypto. For equity: already USD. |
| `volume` | `spot.volume.usd_24h` | Crypto: already USD. Equity: `volume × close`. |
| `date` | `observed_at` | Date string → midnight UTC TIMESTAMPTZ |

**Asset class branching:**
```python
if instrument.asset_class == 'equity':
    volume_usd = row['volume'] * row['close']
else:
    volume_usd = row['volume']
```

### 5.5 SoSoValue

**Provides:** `etf.flows.net_flow_usd`, `etf.aum.total_usd` (BTC, ETH, SOL spot ETFs)

**Cadence:** 24h at 20:00 UTC (after US market close + settlement)

**Hard constraint:** `redistribution = false`. Every write to Silver for SoSoValue-sourced
observations includes `source_id = 'sosovalue'`. The serving layer filters observations
where `source.redistribution = false` before any external API response. This is verified
at the Phase 5 gate.

**Field mappings:** Verify exact field names in integration test — SoSoValue API schema
not yet confirmed. Canonical output is `etf.flows.net_flow_usd` (positive = inflow,
negative = outflow) and `etf.aum.total_usd`.

### 5.6 Explorer (Etherscan V2)

**Provides:** `flows.exchange.inflow_usd`, `flows.exchange.outflow_usd`
(9 exchanges, 18 instruments, ETH + Arbitrum)

**Cadence:** 24h at 04:00 UTC

**Coverage:** 18 instruments confirmed. BTC directional exchange flows not covered —
documented gap, null-propagates in Capital Flows pillar for BTC.

**Gate.io wei conversion:**
```python
def convert_wei_to_usd(wei_value: str, instrument_id: str, spot_store) -> float:
    eth_value = int(wei_value) / 1e18
    spot_price = spot_store.get_latest(
        metric_id='spot.price.close_usd',
        instrument_id=instrument_id
    )
    return eth_value * spot_price
```

Raw Bronze record preserves original wei value in `raw_payload`. The `value_numeric` written
to Silver is the converted USD value. The Bronze audit trail allows reconstruction of the
original wei value at any time.

**Explorer is currently down.** The migration plan treats Explorer as a clean new-source
onboarding, not a Forge migration. Backfill comes from the Etherscan V2 API directly.

### 5.7 CoinPaprika

**Provides:** `spot.market_cap.usd`, `spot.market_cap.total_crypto_usd`, `spot.dominance.btc_pct`

**Cadence:** 24h at 03:00 UTC

**Field mappings:** Verify exact field names in integration test against CoinPaprika API.
Canonical outputs: per-instrument market cap (instrument_id = instrument), total crypto
market cap (instrument_id = NULL), BTC dominance as percentage (instrument_id = NULL).

### 5.8 CoinMetrics

**Provides:** `flows.onchain.transfer_volume_usd` (BTC + ETH only, community edition CSV)

**Collection type:** GitHub CSV bulk download, not API. Files at:
`https://github.com/coinmetrics/data/blob/master/csv/{symbol}.csv`

**Cadence:** 24h at 05:00 UTC. Download latest CSV, diff against last ingested observation
date, write new rows only.

**Hard constraint:** `redistribution = false`. Same enforcement as SoSoValue.

**Idempotency:** CSV files are append-only by day. The adapter tracks `last_ingested_date`
per instrument in `agent_runs`. Re-running the adapter processes only rows with
`date > last_ingested_date`.

### 5.9 BGeometrics

**Provides:** MVRV, SOPR, NUPL, Puell Multiple (BTC + ETH)

**Canonical metric names** (add to metric_catalog before Phase 1 BGeometrics build):
`onchain.valuation.mvrv` · `onchain.valuation.sopr` · `onchain.valuation.nupl` · `onchain.valuation.puell_multiple`

**Field mappings, rate limits, and exact API schema:** Verify in integration test.
No public documentation reviewed prior to this spec — Phase 1 integration test is the
source of truth for this adapter's field mappings.

---

## 6. BLC-01 SPECIFICATION

**Verified from live file `2026-03-05.jsonl` on Server2 (LXC 203) — 2026-03-05.**
Nothing in this section is assumed. All field names and values are from the live file.

### JSONL Schema

Each line is a JSON object with this structure:

```json
{
  "e": "forceOrder",
  "E": 1772668803118,
  "o": {
    "s": "MANTRAUSDT",
    "S": "BUY",
    "o": "LIMIT",
    "f": "IOC",
    "q": "921",
    "p": "0.0241370",
    "ap": "0.0237063",
    "X": "FILLED",
    "l": "589",
    "z": "921",
    "T": 1772668803115
  },
  "_received_at": "2026-03-05T00:00:03.197769+00:00"
}
```

**Field reference:**

| Field | Path | Type | Description |
|---|---|---|---|
| `e` | top-level | string | Event type. Always `"forceOrder"` for liquidation events. |
| `E` | top-level | integer | Event timestamp, Unix milliseconds (when Binance emitted the event). |
| `_received_at` | top-level | string (ISO 8601) | When LXC 203 received the event from the WebSocket. Added by the collector. |
| `o.s` | order | string | Symbol (e.g., `"MANTRAUSDT"`, `"THETAUSDT"`). Maps to `instrument_id` via instruments catalog. |
| `o.S` | order | string | Side: `"BUY"` or `"SELL"`. **Determines long vs. short liquidation** (see below). |
| `o.o` | order | string | Order type. Always `"LIMIT"` for force orders. Not used in aggregation. |
| `o.f` | order | string | Time in force. Always `"IOC"`. Not used in aggregation. |
| `o.q` | order | string (numeric) | Original quantity in base asset. |
| `o.p` | order | string (numeric) | Order price. Not used for USD value — use `o.ap` (actual fill price). |
| `o.ap` | order | string (numeric) | Average fill price in USD. **Used for USD value calculation.** |
| `o.X` | order | string | Execution status. Only `"FILLED"` records are aggregated. |
| `o.l` | order | string (numeric) | Last filled quantity in this event. |
| `o.z` | order | string (numeric) | Cumulative filled quantity. For `X == "FILLED"`, equals `q`. **Used for USD value calculation.** |
| `o.T` | order | integer | Trade time, Unix milliseconds. **Used for 8h window bucketing.** |

### Side Semantics

When a long position is liquidated, the exchange closes it by placing a **SELL** order.
When a short position is liquidated, the exchange closes it by placing a **BUY** order.

- `o.S == "SELL"` → long position liquidated → contributes to `derivatives.perpetual.liquidations_long_usd`
- `o.S == "BUY"` → short position liquidated → contributes to `derivatives.perpetual.liquidations_short_usd`

### Aggregation Formulas

**Filter:** Only process records where `o.X == "FILLED"`. Records with other statuses
(partial fills, cancelled) are dead-lettered with rejection code `NON_FILLED_EXCLUDED`.

**USD value per event:**
```python
usd_value = float(record["o"]["ap"]) * float(record["o"]["z"])
```

`ap` is the actual average fill price (not the order price `p`). `z` is the cumulative filled
quantity. For `X == "FILLED"`, `z == q` always.

**Aggregation per (symbol, 8h window, side):**

```python
def aggregate_window(events: list[dict], window_start: datetime, window_end: datetime) -> list[dict]:
    results = []
    by_symbol_side = defaultdict(float)

    for event in events:
        trade_time_ms = event["o"]["T"]
        trade_dt = datetime.fromtimestamp(trade_time_ms / 1000, tz=UTC)

        if not (window_start <= trade_dt < window_end):
            continue
        if event["o"]["X"] != "FILLED":
            continue

        symbol = event["o"]["s"]
        side = event["o"]["S"]   # "BUY" or "SELL"
        usd_value = float(event["o"]["ap"]) * float(event["o"]["z"])
        by_symbol_side[(symbol, side)] += usd_value

    for (symbol, side), total_usd in by_symbol_side.items():
        metric = (
            "derivatives.perpetual.liquidations_long_usd"
            if side == "SELL"
            else "derivatives.perpetual.liquidations_short_usd"
        )
        results.append({
            "symbol": symbol,
            "metric": metric,
            "observed_at": window_start,
            "value_usd": total_usd,
        })

    return results
```

### Window Boundary

**UTC-aligned. Three fixed windows per day.**

| Window | Start (UTC) | End (UTC) |
|---|---|---|
| Window 1 | 00:00:00 | 07:59:59.999 |
| Window 2 | 08:00:00 | 15:59:59.999 |
| Window 3 | 16:00:00 | 23:59:59.999 |

**Bucketing:** `floor(o.T / 8h)` in UTC. Implemented as:
```python
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
window_index = int((trade_dt - EPOCH).total_seconds() // (8 * 3600))
window_start = EPOCH + timedelta(hours=window_index * 8)
```

**Why UTC-aligned, not rolling:** Coinalyze settlement times are UTC-aligned (00:00, 08:00,
16:00 UTC). BLC-01 windows must align with Coinalyze windows so that liquidation data from
both sources is comparable at the same `observed_at` timestamp. Misalignment would produce
systematic offsets in the Capital Flows and Derivatives Pressure pillars.

### Idempotency

Idempotency is enforced at two levels:

**1. File sensor level (Dagster):** The `run_key` is `blc01_{date}` (e.g., `blc01_2026-03-04`).
Dagster will not re-trigger a run for a run_key it has already processed successfully.
If a run fails and is manually cleared for retry, the run_key mechanism allows reprocessing.

**2. Bronze Iceberg level:** Each Bronze write includes a `payload_hash` column
(`SHA-256(raw_payload)`). The Iceberg table's uniqueness is not enforced at the storage level
(append-only), but a pre-write deduplication check queries Bronze for existing records with
the same `(source_id, metric_id, instrument_id, observed_at, payload_hash)` before appending.
If a match exists, the write is skipped and logged as `DUPLICATE_SKIPPED`.

The Silver ClickHouse write uses `ReplacingMergeTree` — duplicate rows with the same ordering
key `(metric_id, instrument_id, observed_at)` are deduplicated on merge. This is the final
safety net even if Bronze deduplication is bypassed.

### File Lifecycle

**Active file:** `/data/binance_liquidations/YYYY-MM-DD.jsonl`
Written continuously by the WebSocket collector on LXC 203 throughout the day.
**Never processed by the Bronze adapter.**

**Completed file:** `/data/binance_liquidations/YYYY-MM-DD.jsonl.complete`
Created at midnight UTC by the collector renaming the active file.
This rename is the sentinel. The file sensor watches for `*.jsonl.complete` only.

**After successful Bronze write:**
The `.jsonl.complete` file is moved to `/data/binance_liquidations/processed/YYYY-MM-DD.jsonl.complete`.
It is NOT deleted. Retention: 30 days in `/processed/`, then deleted. The canonical record
is Bronze — the Server2 copy is a backup.

**After failed Bronze write:**
The `.jsonl.complete` file remains in `/data/binance_liquidations/`. The Dagster file sensor
will re-detect it on the next tick and retry. The Dagster run_key for the failed run must be
cleared manually before retry is possible — this prevents silent double-processing after an
ambiguous failure.

**rsync path (to be built in Phase 1):**
```
Server2: /data/binance_liquidations/*.jsonl.complete
  → rsync → proxmox: /mnt/empire-data/blc01/landing/
```
rsync runs on a cron schedule every 5 minutes on proxmox, pulling from Server2.
The file sensor on proxmox detects the arrived `.complete` file within 60 seconds of the
rsync completing.

### Backlog Behavior at Startup

At Phase 1 launch, there will be N days of `.jsonl.complete` files already on Server2
(currently 2 complete files: 2026-03-03 and 2026-03-04, plus the active 2026-03-05).

Startup procedure:
1. rsync pulls all `.jsonl.complete` files to the landing directory
2. The file sensor detects all N files simultaneously
3. Dagster yields one `RunRequest` per file, with distinct `run_key` values
4. Dagster processes them in chronological order (oldest first, enforced by `run_key` sort order)
5. Each file produces 3 Bronze records (one per 8h window) per instrument present

**No special backlog mode is needed.** The file sensor's idempotency (run_key per date) and
Dagster's concurrency limits handle the startup case identically to steady-state operation.

### Failure Modes

| Failure | Detection | Response |
|---|---|---|
| Malformed JSON line | `json.decode.JSONDecodeError` | Dead-letter the line with raw bytes preserved. `rejection_code = 'MALFORMED_JSON'`. Continue to next line. |
| Unexpected schema (missing `o.ap`, `o.z`, `o.S`, etc.) | `KeyError` on field access | Dead-letter the record with `rejection_code = 'SCHEMA_UNEXPECTED'`. Log full record. Continue. |
| Out-of-range aggregation value (e.g., single window total > $10B) | GE range expectation on `value_numeric` | Dead-letter with `rejection_code = 'EXTREME_VALUE_PENDING_REVIEW'`. Queue for manual review. |
| Bronze write failure (MinIO unreachable) | `ConnectionError` on PyIceberg write | Dagster asset fails. Retry 3× with exponential backoff. If all retries fail: file remains in landing directory for next attempt. Run_key cleared after 3 failures to allow retry. |
| Silver write failure (ClickHouse unreachable) | `ClickHouseError` | Bronze write already succeeded. Log Silver write failure. Dagster asset marks as partial. The Silver → Gold export will not include this data until the Silver write is retried. Manual retry procedure: re-run the BLC-01 asset for the affected date from Dagster UI. Bronze deduplication prevents double-write. |
| LXC 203 offline / rsync failure | File sensor stops triggering. Asset shows stale after `maximum_lag_minutes`. | SSH to Server2: `ssh root@192.168.68.12`. Check LXC 203 status in Proxmox. Verify rsync cron. Data loss during downtime is permanent — tick data is real-time only. Accept gap. Signal degrades to `SOURCE_STALE` null state. |
| Duplicate file detected | `payload_hash` match in Bronze pre-write check | Skip write. Log `DUPLICATE_SKIPPED`. No error. |
| Non-FILLED status record | `o.X != "FILLED"` | Dead-letter with `rejection_code = 'NON_FILLED_EXCLUDED'`. Not an error — expected. |
| File present but empty (0 bytes) | Line count = 0 | Write zero Bronze records. Run record: `total_observations = 0`, `status = 'degraded'`. Alert operator. Do not raise exception. |

---

## 7. FORGE DECOMMISSION PLAN

### Current State

Four Forge agents are running against `empire_forge_db` (port 5435):

| Agent | Source | Write target | Duration |
|---|---|---|---|
| forge_agent_coinalyze | Coinalyze | empire_forge_db | Up 7 days |
| forge_agent_defillama_ext | DeFiLlama | empire_forge_db | Up 7 days |
| forge_agent_fred | FRED | empire_forge_db | Up 3 days |
| forge_agent_etf | SoSoValue | empire_forge_db | Up 3 days |

`forge_agent_explorer` is not running. No action needed.

The new system writes to ClickHouse (Silver) and MinIO (Bronze). Write targets are
completely isolated — there is no duplication risk while both stacks run simultaneously.
The Forge agents are a passive data safety net during Phase 1.

### Decommission Model: Phase Gate Cutover

All four Forge agents stop at the **Phase 1 gate close**. Not per-collector.

**Rationale:** Write targets are isolated, so there is no operational cost to running
both simultaneously. Per-collector decommission adds state tracking overhead
("which agents are off") for no benefit. Once the Phase 1 gate passes — meaning the
new system has ≥90% coverage completeness for all active metrics, all adapters running
at cadence, and migration spot-checks clean — the Forge agents are redundant by definition.

### Decommission Procedure (at Phase 1 gate)

```bash
# 1. Verify Phase 1 gate passes (all criteria — see thread_6_build_plan §Phase 1 gate)

# 2. Stop and remove Forge agents
docker stop forge_agent_coinalyze forge_agent_defillama_ext forge_agent_fred forge_agent_etf
docker rm forge_agent_coinalyze forge_agent_defillama_ext forge_agent_fred forge_agent_etf

# 3. empire_forge_db remains running, read-only, for 90 days
# No write access needed. Safety net for spot-checks only.
# Do NOT decommission empire_forge_db at Phase 1 gate.

# 4. After 90 days:
docker stop empire_forge_db
docker rm empire_forge_db
# Optionally: archive to NAS before deletion
```

### Forge DB as Safety Net

`empire_forge_db` (5435) is retained read-only for 90 days after the Phase 1 gate.
During this period it serves one purpose: if a data quality issue is discovered in the
new system's migration data, the Forge dataset can be queried for comparison.

Forge tables are not authoritative. No component of the new system reads Forge tables
for anything other than migration spot-checks. Forge is dead — the 90-day retention is
a data safety net, not an operational dependency.

---

## 8. MIGRATION PLAN

### Assessment Criteria

For each Forge dataset: are timestamps reliable? Are units documented? Are symbols
mappable to canonical instrument_ids? Is metric identity clear?

### Migration Order

Tiingo must migrate first — its spot prices are a dependency for the Explorer
wei→USD conversion. All other migrations can proceed in parallel or in any order
after Tiingo.

### Per-Dataset Migration

---

#### Dataset: Tiingo OHLCV
**Rows:** ~800k | **Status:** GREEN | **Priority:** 1

**Current EDS method:** Tiingo API, stored in EDS schema in empire_postgres or empire_forge_db.
**New adapter:** Tiingo REST adapter writing to Bronze (MinIO) → Silver (ClickHouse).
**Migration adapter:** Same 10-responsibility contract as production adapter. Reads Forge/EDS
Tiingo tables, maps to canonical schema, writes to Bronze + Silver. Not a direct table copy.

**Verification criteria:**
```sql
-- Spot-check: BTC close price on a known date
-- EDS/Forge query (5435):
SELECT value FROM forge_tiingo WHERE symbol = 'BTCUSD' AND date = '2024-01-01';

-- New system query (ClickHouse):
SELECT value_numeric FROM forge.observations
WHERE metric_id = (SELECT metric_id FROM forge.metric_catalog
                   WHERE canonical_name = 'spot.price.close_usd')
  AND instrument_id = (SELECT instrument_id FROM forge.instruments
                       WHERE canonical_symbol = 'BTC')
  AND observed_at = '2024-01-01T00:00:00Z';

-- Values must match within floating point tolerance.
```

**Rollback:** If migration produces systematic errors, delete affected Silver rows
(`ALTER TABLE forge.observations DELETE WHERE source_id = 'tiingo' AND ...`),
fix the migration adapter, re-run. Bronze records are preserved regardless — re-migration
reads the Forge source, not Bronze.

**Decommission criteria:** Tiingo production adapter has run for ≥3 successful cycles at
cadence AND spot-check verification passes for ≥5 instruments AND Phase 1 gate passes.

---

#### Dataset: Coinalyze Derivatives
**Rows:** 185,066 | **Status:** GREEN | **Priority:** 2

**Known issue:** Verify open interest units in Forge data — may be contracts or USD.
Migration adapter must apply same unit check as production adapter.

**Verification criteria:**
```sql
-- Spot-check: BTC funding rate on a known date (Coinalyze settlement ~00:00 UTC)
-- Compare Forge value to canonical store value for 3 instruments across 3 dates.
```

**Decommission criteria:** As Tiingo, applied to Coinalyze production adapter.

---

#### Dataset: FRED Macro
**Rows:** 140,261 | **Status:** GREEN | **Priority:** 3 (after Tiingo)

**Note:** Migration covers the 23 live series. `macro.credit.hy_oas` and
`macro.employment.mfg_employment` must be added to metric_catalog before migration.
These two series are backfilled from the FRED API directly — not from Forge data.

**Verification criteria:**
```sql
-- Spot-check: 10Y yield on 5 known dates. Compare Forge to canonical store.
-- Verify '.' values mapped to NULL, not to 0 or any numeric value.
```

---

#### Dataset: DeFiLlama DEX
**Rows:** 88,239 | **Status:** GREEN | **Priority:** 4

**Verification criteria:** Compare aggregate DEX volume for 5 known dates between
Forge and canonical store. Values should match within rounding tolerance.

---

#### Dataset: DeFiLlama Lending
**Rows:** 9,651 | **Status:** GREEN | **Priority:** 5

---

#### Dataset: CoinMetrics On-Chain
**Rows:** 10,137 | **Status:** GREEN | **Priority:** 6

**`redistribution = false` must be set in source_catalog before migration begins.**
Verify after migration: `SELECT redistribution FROM forge.source_catalog WHERE source_id = 'coinmetrics'` returns `false`.

---

#### Dataset: Exchange Flows (Explorer)
**Rows:** 2,177 | **Status:** RED (wei bug) | **Priority:** 7

**forge_agent_explorer is down.** The Forge dataset may have a gap of unknown duration.
The migration adapter applies the wei→USD fix to all Gate.io rows. After migration,
spot-check a Gate.io row: the Silver value should be in USD, the Bronze `raw_payload`
should preserve the original wei value.

**Verification criteria:**
```sql
-- For a known Gate.io instrument on a known date:
-- Bronze raw_payload: value should be in wei (large integer string, e.g., "15243000000000000000")
-- Silver value_numeric: should be in USD (e.g., 4.21)
-- Manual calculation: raw_wei / 1e18 × spot_price_on_that_date ≈ Silver value
```

**Alternative approach if Forge data is too stale:** Skip Forge migration for Explorer entirely.
Backfill from Etherscan V2 API directly. Given the agent has been down for an unknown period,
this is likely the cleaner path. Decision at Phase 1 start — check Forge data recency first.

---

#### Dataset: ETF Flows (SoSoValue)
**Rows:** 774 | **Status:** GREEN | **Priority:** 8

**`redistribution = false` must be set before migration begins.** Same requirement as CoinMetrics.

---

#### Dataset: DeFi Protocols
**Rows:** 195 | **Status:** SHALLOW | **Action:** Skip migration — backfill from DeFiLlama API

The Forge dataset is too shallow (195 rows) to be worth migrating. The DeFiLlama historical
API provides complete protocol TVL history. The backfill job runs after the DeFiLlama
production adapter is deployed and verified.

---

#### Dataset: Stablecoins
**Rows:** 180 | **Status:** SHALLOW | **Action:** Skip migration — backfill from DeFiLlama API

Same as DeFi protocols.

---

### Duplicate Prevention During Migration

The overlap period (Forge agents running + migration adapters running) does not produce
duplicates in the new system because:

1. Forge agents write to `empire_forge_db` (5435). Migration adapters write to ClickHouse.
   The write targets are completely separate.

2. Migration adapters process Forge data (historical). Production adapters collect live data.
   After migration completes, the production adapter's cadence begins from `NOW()` — it does
   not re-fetch historical data.

3. If a migration adapter and production adapter happen to write observations with the same
   `(metric_id, instrument_id, observed_at)` key (e.g., if migration runs right up to today
   and production adapter also collects today's data), ClickHouse `ReplacingMergeTree`
   deduplicates on merge. The higher `data_version` value wins.

No explicit overlap coordination is required. The deduplication is structural.

---

## DONE CRITERIA

Conditions that must be true before Prompt 03 (Phase 1 build session) begins:

- [ ] Pre-flight reconciliation table complete — forge.source_catalog queried against 5435 to confirm all 10 sources present
- [ ] Dagster asset graph: 53 assets, partition model confirmed, freshness policy derivation pattern confirmed
- [ ] File sensor: watched directory path, tick frequency, run_key pattern confirmed
- [ ] Dagster instance configuration: three containers, metadata on `/mnt/empire-db/dagster`, confirmed in docker-compose.yml plan
- [ ] All 10 sources in collector inventory — no blanks, no TBD
- [ ] Bronze: column types justified, partition transform justified against query patterns
- [ ] Append-only enforcement mechanism specified at Iceberg level
- [ ] 90-day retention: Dagster job at 02:00 UTC, `expire_snapshots` + `clean_orphan_files`, confirmed
- [ ] MinIO path layout shown; S3 compatibility explicitly confirmed
- [ ] GE: extended source_catalog DDL specified
- [ ] GE: calibration mode with auto-parameter computation specified
- [ ] GE: worked example from catalog row to suite confirmed
- [ ] GE: failure behavior — single bad value does not fail batch — confirmed
- [ ] GE: suite versioning procedure step-by-step confirmed
- [ ] BLC-01: JSONL schema from verified live file — not assumed
- [ ] BLC-01: aggregation formulas with actual field names from live file
- [ ] BLC-01: window boundary UTC-aligned, confirmed with rationale
- [ ] BLC-01: idempotency at two levels (run_key + payload_hash) specified
- [ ] BLC-01: file lifecycle (active → complete → processed) specified
- [ ] BLC-01: backlog behavior at startup specified
- [ ] BLC-01: all failure modes with response procedure
- [ ] Forge decommission: phase gate cutover model, exact procedure
- [ ] Migration: dependency-ordered, per-dataset, with verification criteria and rollback
- [ ] No PostgreSQL storage targets anywhere in this document
- [ ] Two metric_catalog additions documented: `macro.credit.hy_oas`, `macro.employment.mfg_employment`
- [ ] Two metric_catalog additions documented: `defi.protocol.fees_usd_24h`, `defi.protocol.revenue_usd_24h`
- [ ] Four metric_catalog additions documented: `onchain.valuation.mvrv/sopr/nupl/puell_multiple`
- [ ] Architect confirms before Phase 1 build session begins

---

*thread_5_collection.md — authoritative on collection architecture as of 2026-03-05.*
*Preceded by thread_infrastructure.md. Consumed by thread_6_build_plan.md §Phase 1.*
*Changes to locked decisions require architect approval and a version bump to this document.*
