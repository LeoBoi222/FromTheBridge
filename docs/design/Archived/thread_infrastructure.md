# thread_infrastructure.md
## FromTheBridge — Infrastructure Architecture
## Empire Architecture v2.0

**Date:** 2026-03-05
**Status:** Authoritative. Locked decisions require architect approval to reopen.
**Owner:** Stephen (architect, sole operator)
**Supersedes:** Infrastructure sections of thread_4_data_universe.md, thread_5_collection.md, thread_6_build_plan.md (all stale on infrastructure — do not treat as authoritative)

---

## OVERVIEW

This document defines the infrastructure architecture for the FromTheBridge data platform. It covers the seven technology decisions as Architecture Decision Records, layer boundary enforcement mechanisms, physical deployment topology, resource boundaries and scaling thresholds, managed service migration triggers, and one-operator operational procedures.

All technology decisions in this document are locked. Build sessions reference section numbers here. If a build requirement cannot be traced to a specification in this document, stop and surface it to the architect before proceeding.

---

## LAYER STACK (INFRASTRUCTURE VIEW)

The system is organized as nine layers. Data flows downward only. No layer reads a layer above itself. This is a hard rule enforced by credential isolation and structural DDL — not operational discipline.

```
Layer 8: Serving
  Decoupled API process. DuckDB reads Gold + Marts.
  Arrow Flight (bulk timeseries), REST JSON (signals).
  /v1/signals, /v1/timeseries, webhooks, Telegram.
  Never reads ClickHouse or PostgreSQL directly.
  Phase 6 scope — not built until all prior layers verified.

Layer 7: Catalog
  PostgreSQL. Relational integrity only.
  metric_catalog, source_catalog, instruments, assets,
  asset_aliases, venues, metric_lineage, event_calendar,
  supply_events, adjustment_factors.
  No time series data here — ever.

Layer 6: Marts
  Feature Store. dbt (SQL transforms) + Python
  (rolling window, cross-sectional features).
  forge_compute lives here. PIT enforced.
  Feature catalog entry required before compute.

Layer 5: Gold
  Analytical Layer. Iceberg tables on MinIO.
  DuckDB reads here. Feature compute reads here ONLY.
  Never reads ClickHouse directly — hard rule.
  Populated by incremental export from Silver every 6h.

Layer 4: Silver
  Observation Store. ClickHouse.
  EAV: (metric_id, instrument_id, observed_at, value).
  ReplacingMergeTree. Bitemporal: observed_at + ingested_at.
  dead_letter table here. current_values materialized view.
  Write-only except for the 6h export job.

Layer 3: Bronze
  Raw Landing. Apache Iceberg tables on MinIO.
  Partitioned by (source_id, date, metric_id).
  ACID, schema evolution, time travel native.
  Append-only, 90-day retention. Raw payload preserved.
  Great Expectations validation at Bronze → Silver boundary.

Layer 2: Adapters
  Per-source. 10-responsibility contract.
  Auth, rate limiting, pagination, schema normalization,
  timestamp normalization, unit normalization, validation,
  extreme value handling, idempotency, observability.

Layer 1: Orchestration
  Dagster (dedicated Docker service).
  Software-Defined Assets. One asset per (metric_id, source_id).
  Asset graph mirrors metric_catalog + metric_lineage.
  Freshness from cadence_hours. Retry, backoff, alerting
  as framework primitives. BLC-01: file-sensor trigger.

Layer 0: Sources
  Coinalyze, DeFiLlama, FRED, Tiingo, SoSoValue,
  Etherscan/Explorer, CoinPaprika, BGeometrics, CoinMetrics,
  Binance (BLC-01). 10 sources at v1.
```

---

## THREE HARD RULES

### Rule 1: Layer boundary is a one-way gate

Data flows down only. Nothing reads a layer above its own. Feature compute reads Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6) via DuckDB. No exceptions.

**Enforcement mechanism:** Dagster asset dependency graph. An asset that reads a layer above its own cannot be defined without explicitly declaring an upstream dependency on a higher-layer asset — which Dagster will reject as a cycle. Violations fail at pipeline definition time, not silently at runtime.

**Violation detection:** Any asset materialization that produces output without a declared upstream at the correct layer is structurally impossible to construct. Attempted workarounds (direct DB connections outside Dagster) are caught by credential isolation — see Rule 2.

### Rule 2: ClickHouse is write-only except for the export job

The only process that reads ClickHouse is the Dagster Software-Defined Asset that runs the incremental Silver → Gold export every 6h. All analytical workloads go through Gold (Iceberg on MinIO, read by DuckDB).

**Enforcement mechanism:** ClickHouse credentials (host, port, username, password) are issued exclusively to the export asset's environment. No other Docker service has a ClickHouse connection string in its environment. forge_compute, dbt, and the serving layer have no ClickHouse credentials.

**Violation detection:** Any direct ClickHouse connection from an unauthorized service fails at authentication. The ClickHouse query log records all connections — unexpected client IPs or usernames are immediately visible.

**What a violation looks like in practice:** A developer adds a direct ClickHouse read to forge_compute to avoid waiting for the next Gold export. This bypasses the 6h export cycle and introduces a dependency on Silver's merge state (ReplacingMergeTree deduplication is not guaranteed until OPTIMIZE runs). Features computed against unmerged Silver data produce silently incorrect results. The credential isolation makes this a hard failure rather than a silent bug.

### Rule 3: PostgreSQL holds no time series data

The catalog layer holds relational integrity only. No `observed_at + value` columns exist in any PostgreSQL table. No metric observations, no derived computations, no feature values in PostgreSQL.

**Enforcement mechanism:** Structural DDL. The schema contains no time series tables. A developer adding time series to PostgreSQL must write DDL that explicitly creates the columns — there is no accidental path to violation.

**Violation detection:** `SELECT column_name FROM information_schema.columns WHERE table_schema = 'forge' AND column_name IN ('observed_at', 'value', 'value_numeric', 'ingested_at') AND table_name NOT IN ('observations', 'dead_letter')` returns zero rows if the rule is intact.

---

## ARCHITECTURE DECISION RECORDS

### ADR-001: ClickHouse as Silver (Observation Store)

**Status:** Accepted | **Date:** 2026-03-05

#### Context

The observation store holds every metric value collected from every source across every instrument, bitemporally (observed_at + ingested_at). At full buildout with BLC-01 active, write volume reaches ~72,000 rows/day, dominated by Binance tick liquidation events (~70,000/day). The store must support: idempotent writes (sources resend data), revision handling (values are corrected), PIT-correct historical queries, and a single daily export to the analytical layer. The store is write-heavy and append-oriented. The single analytical read pattern (full-table sequential scan for export) is not latency-sensitive.

#### Options considered

**PostgreSQL + TimescaleDB (prior design — rejected)**
TimescaleDB hypertables are PostgreSQL with a time-partitioning extension. At 70,000+ rows/day and multi-year history, TimescaleDB performs adequately on small datasets but degrades on full sequential scans as data accumulates. The observation store's primary read pattern — a full incremental export scanning all rows since the last export — is exactly the workload TimescaleDB handles poorly relative to a native columnar store. More critically: PostgreSQL's row-oriented storage compresses EAV data (many short rows, few columns) at 2-4x. ClickHouse compresses the same data at 10-20x due to columnar layout. On a self-hosted single-node system with finite disk, this matters. TimescaleDB is also PostgreSQL-ecosystem-only — migration to ClickHouse Cloud requires a full data migration, not a config change. Disqualified: compression ratio, export scan performance, and migration path.

**InfluxDB (rejected)**
Purpose-built for time series. Strong on write throughput. Weak on EAV schema — InfluxDB assumes a fixed tag/field schema per measurement. The metric_catalog-driven EAV model requires schema-free writes where metric_id drives the schema, not a hardcoded tag set. Adding a new metric to InfluxDB requires schema coordination; in ClickHouse it is a new row in a catalog table and a new row type in the same table. InfluxDB's query language (Flux) adds a non-standard dependency. Disqualified: EAV incompatibility, schema evolution model, non-standard query surface.

**DuckDB for Silver as well as Gold (rejected)**
DuckDB is an embedded analytical engine, not a server. It cannot accept concurrent writes from multiple Dagster asset materializations running in parallel. The observation store receives writes from multiple adapters simultaneously. DuckDB's lack of a server mode and its MVCC limitations under concurrent write workloads make it structurally unsuitable as a write target. Disqualified: no concurrent write support.

#### Decision

ClickHouse self-hosted, single node, Docker container on proxmox. EAV schema with `ReplacingMergeTree(data_version)` engine. Ordered by `(metric_id, instrument_id, observed_at)`. Partitioned by `toYYYYMM(observed_at)`. This gives: columnar compression (10-20x), idempotent revision handling via `data_version`, the export-optimized scan pattern that columnar storage was designed for, and a managed migration path to ClickHouse Cloud that is schema-compatible.

#### Consequences

**Enables:** Billions of rows on a single self-hosted node without storage or performance degradation. Idempotent writes — adapters can re-send any observation and `ReplacingMergeTree` deduplicates on merge. Export scans are fast by design. ClickHouse Cloud migration is a connection string change and data migration — zero code changes.

**Constrains:** ClickHouse `ReplacingMergeTree` deduplication is eventual — rows are merged during background OPTIMIZE operations, not immediately on write. Queries against unmerged data must use the `FINAL` keyword or accept duplicate rows. The export job must use `SELECT ... FINAL` or `OPTIMIZE TABLE ... FINAL` before export to ensure clean data reaches Gold. This is a known ClickHouse operational pattern, not a defect.

**To reverse this decision:** Full data migration from ClickHouse to the replacement store. Adapter write targets change. Export job changes. Estimated effort: 2-3 weeks.

#### Migration path

**Managed equivalent:** ClickHouse Cloud
**What changes:** Connection string (host, port, credentials). Zero application code changes. Schema is fully compatible.
**Data migration:** Required — ClickHouse to ClickHouse Cloud uses the native replication protocol or a bulk export/import via S3. Estimated duration: hours to days depending on accumulated row count.
**Trigger:** See Split Triggers section.

---

### ADR-002: Apache Iceberg as Bronze and Gold Storage Format

**Status:** Accepted | **Date:** 2026-03-05

#### Context

Two layers require object storage with structured query capability: Bronze (raw landing, 90-day retention, append-only) and Gold (analytical layer, read by DuckDB for feature compute). Both layers require: schema evolution without DDL (new metrics added as catalog rows, not schema changes), time travel queries (PIT model requires querying Bronze at any historical point for audit and backfill validation), ACID writes (multiple adapters writing concurrently must not corrupt the table state), and S3-compatibility (cloud migration must be a config change, not a rewrite).

#### Options considered

**Raw Parquet files on MinIO (rejected)**
Parquet files are immutable once written. Schema evolution requires rewriting files. No native time travel — point-in-time queries require manual file management by timestamp. No ACID guarantees — concurrent writes can produce corrupt state. Audit queries require external tooling to reconstruct historical state. Disqualified: no time travel, no ACID, no schema evolution.

**Delta Lake (rejected)**
Functionally equivalent to Iceberg for this use case. The deciding factor is ecosystem: DuckDB has native Iceberg read support as a first-class extension. Delta Lake support in DuckDB is less mature. Given that DuckDB is the Gold → Marts read engine, Iceberg's tighter DuckDB integration reduces operational friction. Disqualified relative to Iceberg: DuckDB ecosystem alignment.

**PostgreSQL partitioned tables for Bronze (rejected)**
Bronze holds raw payloads as JSON strings — arbitrarily large, schema-free, append-only. PostgreSQL JSONB storage for raw payloads at scale is operationally expensive. More critically: Bronze's 90-day retention is enforced via Iceberg snapshot expiration — a single metadata operation. In PostgreSQL it requires partition management and scheduled delete jobs. And PostgreSQL is excluded from time series storage by Rule 3. Disqualified: Rule 3 violation, retention management complexity.

#### Decision

Apache Iceberg tables on MinIO for both Bronze and Gold. Partitioned by `(source_id, date, metric_id)` for Bronze. Partitioned by `(metric_id, month)` for Gold. PyIceberg for write operations from adapters. DuckDB with `iceberg` extension for reads from forge_compute and serving layer.

#### Consequences

**Enables:** Native time travel queries on Bronze — audit any raw payload at any historical point. Schema evolution without DDL — new partition values appear automatically as new metrics are collected. ACID writes — concurrent adapter materializations cannot corrupt table state. S3 migration is a MinIO endpoint swap in config. DuckDB reads Iceberg tables natively with the same SQL interface used everywhere else.

**Constrains:** Iceberg metadata management requires a catalog service or file-based catalog. At this scale, the Hadoop FileSystem catalog (metadata stored alongside data files in MinIO) is sufficient — no separate catalog service needed. At larger scale (100+ tables, high-frequency small writes), a dedicated catalog service (AWS Glue, Nessie) becomes relevant. This is a Phase 6+ concern.

**To reverse this decision:** Rewrite Bronze adapter write targets. Rewrite Gold export job. Rewrite DuckDB read configuration. Estimated effort: 1-2 weeks, primarily adapter changes.

#### Migration path

**Managed equivalent:** AWS S3 + AWS Glue Catalog (for catalog management at scale) or S3 alone if file-based catalog suffices.
**What changes:** MinIO endpoint → S3 endpoint in environment config. Optionally, file-based Iceberg catalog → Glue catalog (one-time migration of metadata). Zero application code changes.
**Data migration:** Not required — files on MinIO are moved to S3. The files themselves are Iceberg-format and remain readable after the move.
**Trigger:** See Split Triggers section.

---

### ADR-003: MinIO as Object Storage

**Status:** Accepted | **Date:** 2026-03-05

#### Context

Both Iceberg layers (Bronze and Gold) require S3-compatible object storage. The requirement is: S3 API compatibility (so that cloud migration is a config change), sufficient throughput for ~15 GB/year write volume at full buildout, and self-hosted operation on proxmox.

#### Options considered

**AWS S3 directly (rejected for Phase 0–5)**
S3 is the migration target, not the starting point. Running S3 during development and pre-revenue phases introduces cost for a system that can self-host without performance constraint. S3 becomes correct when managed cloud migration triggers. Disqualified for v1: unnecessary cost at current scale.

**Ceph (rejected)**
Distributed object storage designed for multi-node clusters. Operational complexity is disproportionate to a single-node self-hosted deployment. Ceph's value is horizontal scalability across nodes — irrelevant until cloud migration triggers. Disqualified: over-engineered for single-node deployment.

**Local filesystem (NFS/bind mount) (rejected)**
No S3 API. Application code would need to distinguish between local and S3 storage paths. Migration to cloud would require code changes, not config changes. Disqualified: breaks cloud migration portability.

#### Decision

MinIO self-hosted, Docker container on proxmox, writing to `/mnt/empire-data` (4TB SSD). S3-compatible API — all application code uses the S3 SDK with endpoint override pointing to MinIO. When cloud migration triggers, the endpoint changes to the AWS region endpoint and credentials rotate. Zero code changes.

#### Consequences

**Enables:** Complete S3-API portability. All storage code is written once and runs unchanged against MinIO (local) or S3 (cloud). Sufficient throughput for projected write volumes with significant headroom.

**Constrains:** Single-node MinIO has no built-in replication. Disk failure on `/mnt/empire-data` loses Bronze and Gold data. Mitigation: NAS backup is in the topology (read-only backup destination). Bronze has 90-day retention — loss of recent Bronze is recoverable from source re-fetch. Gold is reconstructed by re-running the export job from Silver. Silver (ClickHouse) must be the durability anchor.

**To reverse this decision:** Change endpoint config. Zero code changes.

#### Migration path

**Managed equivalent:** AWS S3
**What changes:** `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` → `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. One environment variable swap per service that writes to object storage.
**Data migration:** `aws s3 sync` from MinIO bucket to S3 bucket. One-time operation.
**Trigger:** See Split Triggers section.

---

### ADR-004: DuckDB as Analytical Engine

**Status:** Accepted | **Date:** 2026-03-05

#### Context

Feature compute (Layer 6 Marts) and the serving layer (Layer 8) need a query engine that reads Gold (Iceberg on MinIO). Requirements: SQL interface, Iceberg native support, high performance on multi-year sequential scans for rolling window features, zero operational overhead (no server to manage), embedded execution in Python (forge_compute, dbt Python models).

#### Options considered

**Spark (rejected)**
Distributed processing framework. Correct choice at multi-terabyte scale with multi-node clusters. At the projected Gold layer size (5 GB over 5 years), Spark's cluster overhead is entirely wasted. JVM startup time alone exceeds the total query time DuckDB requires for the same scan. Disqualified: operational overhead grossly disproportionate to data volume.

**Trino/Presto (rejected)**
Distributed SQL query engine. Same disqualifier as Spark — designed for cluster deployments. Requires a coordinator and worker node architecture. On a single-node self-hosted system, this is a server process to manage for no performance benefit over DuckDB. Disqualified: server overhead, no benefit over DuckDB at this scale.

**PostgreSQL with foreign data wrapper for Parquet (rejected)**
PostgreSQL FDWs for Parquet/Iceberg exist but are not first-class — performance is poor on large scans, Iceberg support is limited. More critically: PostgreSQL is excluded from time series storage by Rule 3, and using it as a query engine for Gold creates an operational coupling between the catalog layer and the analytical layer. Disqualified: Rule 3 coupling risk, poor Iceberg scan performance.

#### Decision

DuckDB embedded, no server mode. Runs inside forge_compute Python processes and dbt Python models. Reads Iceberg tables on MinIO via the `iceberg` DuckDB extension. Zero operational overhead — DuckDB is a library, not a service. No port to manage, no daemon to monitor, no connection pool to configure.

#### Consequences

**Enables:** Sub-second query performance on multi-year Gold scans. Native Iceberg reads. Python embedding — forge_compute and dbt models call DuckDB directly without a network hop. Arrow-native output — DuckDB produces Arrow record batches that integrate with ML training pipelines and the serving layer's Arrow Flight endpoint.

**Constrains:** DuckDB is not a server — it cannot accept concurrent connections from multiple processes writing to the same database file simultaneously. This is not a constraint here because DuckDB is read-only against Gold (writes go through ClickHouse → export job → Iceberg). Multiple forge_compute processes can read Gold concurrently without conflict.

**To reverse this decision:** Replacement requires rewriting all forge_compute query logic and dbt Python model connectors. Estimated effort: 2-3 weeks.

#### Migration path

DuckDB has no managed cloud equivalent — it is an embedded library. At the scale where DuckDB becomes insufficient (terabyte-range Gold layer, sub-second serving latency requirements), the replacement is Trino or Spark with a proper cluster. This is a Phase 6+ architectural decision and requires a full query engine migration. The Iceberg table format is preserved — only the query engine changes.

---

### ADR-005: Dagster as Orchestration

**Status:** Accepted | **Date:** 2026-03-05

#### Context

The system has one asset per (metric_id, source_id) combination. At full buildout with 10 sources and ~50 metrics, this is ~200 Dagster assets. The orchestration layer must: model the asset graph to mirror metric_lineage (so downstream assets know when their upstream data is fresh), enforce freshness from cadence_hours in the metric catalog, provide retry and backoff as framework primitives (not bespoke adapter code), alert on staleness, and trigger BLC-01 ingestion via file sensor rather than wall-clock schedule.

#### Options considered

**Apache Airflow (rejected)**
Task-centric, not asset-centric. Airflow models "run this DAG on a schedule" — it has no native concept of "this data asset is stale and must be refreshed." Modeling the metric_catalog asset graph in Airflow requires manually maintaining DAG dependencies that duplicate the metric_lineage table. When metric_lineage changes, both the database and the DAG must be updated — a consistency hazard. Dagster's Software-Defined Assets model derives the execution graph from the asset definitions, which can be built to mirror metric_catalog directly. Disqualified: no native asset model, metric_lineage duplication hazard.

**Prefect (rejected)**
Closer to Dagster than Airflow in its flow-centric model, but still not asset-native. Prefect's value is in its managed cloud offering (Prefect Cloud) which is operationally convenient but adds a cloud dependency that is not warranted at this stage. Self-hosted Prefect server adds operational overhead comparable to Dagster without the asset graph benefit. Disqualified relative to Dagster: no asset model, managed dependency not yet warranted.

**APScheduler (current EDS approach — rejected for new system)**
Simple Python in-process scheduler. No UI, no retry framework, no asset graph, no staleness monitoring, no file sensors. Failure is silent until a downstream system notices missing data. Suitable for a prototype; not suitable for a production data platform where data quality directly affects customer-facing signals. Disqualified: no observability, no asset model, no framework retry.

**Cron + scripts (rejected)**
Same disqualifiers as APScheduler, worse. No retry, no dependency graph, no monitoring.

#### Decision

Dagster self-hosted, dedicated Docker service on proxmox. Software-Defined Assets with one asset per (metric_id, source_id). Asset graph built from metric_catalog + metric_lineage at startup. Freshness policies derived from `cadence_hours` in metric_catalog. BLC-01 triggered by Dagster file sensor watching the rsync landing directory. Dagster metadata stored on `/mnt/empire-db` (2TB SSD).

**Correction from design_index:** The design_index specified "Dagster dedicated LXC." This is incorrect and is corrected here. LXC containers are Proxmox-specific and do not translate to cloud deployment. All services including Dagster run as Docker containers. At cloud migration, Docker containers are portable to ECS, Cloud Run, or Kubernetes without modification.

#### Consequences

**Enables:** The asset graph mirrors metric_lineage — adding a new source or metric adds a Dagster asset, not a DAG. Freshness policies are data-driven from metric_catalog, not hardcoded in scheduler config. Retry, backoff, and alerting are framework primitives — adapters do not implement their own retry logic. The Dagster UI provides real-time visibility into every asset's materialization status, last run time, and failure details. This is the 2am operational interface.

**Constrains:** Dagster adds a service to manage. The Dagster daemon (schedules, sensors), webserver (UI), and code server (asset definitions) are three processes. On proxmox Docker, these run as a single docker-compose service group. Dagster's metadata database (SQLite or PostgreSQL) must be backed up. SQLite is sufficient at this scale — stored on `/mnt/empire-db`.

**To reverse this decision:** Rewrite all adapter scheduling logic. Estimated effort: 2+ weeks, primarily rebuilding retry and freshness logic that Dagster provides as primitives.

#### Migration path

**Managed equivalent:** Dagster Cloud
**What changes:** Dagster agent configuration (local → cloud agent). Asset definitions are fully portable — they run identically against Dagster Cloud.
**Data migration:** Dagster run history can be migrated but is not operationally critical — it's a log, not source data.
**Trigger:** See Split Triggers section.

---

### ADR-006: dbt + Python for Marts (Feature Store)

**Status:** Accepted | **Date:** 2026-03-05

#### Context

Layer 6 Marts contains two categories of transforms: SQL transforms (ratio features, simple aggregations, joins across Gold tables) and Python transforms (rolling window calculations, cross-sectional ranks, breadth scores, LightGBM feature assembly). Both categories require versioning, testing, PIT integrity enforcement, and a feature catalog entry before compute. The compute layer reads Gold via DuckDB and writes feature outputs to the Marts store (also Iceberg on MinIO).

#### Options considered

**dbt alone (rejected as sole solution)**
dbt SQL models cover the SQL transform category well — versioned, tested, documented. dbt Python models (using dbt-duckdb adapter) cover simple Python transforms. However, rolling window features with variable lookback periods and cross-sectional rank computation across 100+ instruments in a single pass are awkward to express in dbt Python models and produce poor execution plans. The ML training pipeline requires direct Python control over feature assembly to guarantee PIT correctness at each training window boundary. dbt alone is insufficient for the Python-heavy compute. Used for SQL transforms only.

**Pure Python pipeline (rejected as sole solution)**
A custom Python pipeline (e.g., pandas-based) for all feature compute avoids the dbt dependency but loses versioning, testing, and documentation for SQL transforms. dbt's model testing framework catches feature compute regressions that a custom pipeline would miss silently. Disqualified as sole solution: no SQL transform testing framework.

#### Decision

dbt with dbt-duckdb adapter for all SQL transforms. forge_compute Python service for rolling window, cross-sectional, and ML-assembly features. Both use DuckDB to read Gold. Both write outputs to Marts (Iceberg on MinIO). dbt models are tested with dbt's built-in test framework. forge_compute is tested with pytest. Feature catalog entry required before any feature is computed — enforced by forge_compute startup check.

#### Consequences

**Enables:** SQL transforms are versioned in git as dbt model files. dbt's test framework runs schema tests and custom data tests on every feature compute run. Python transforms have full flexibility for complex windowing logic. Both layers share the same DuckDB + Iceberg read interface.

**Constrains:** Two compute systems to maintain (dbt and forge_compute). Boundary between what belongs in dbt SQL vs. forge_compute Python must be respected: if it can be expressed cleanly in SQL, it goes in dbt. If it requires Python (rolling windows, cross-sectional operations across all instruments simultaneously), it goes in forge_compute.

#### Migration path

No managed equivalent with a direct migration path. dbt Cloud exists for dbt orchestration but the models themselves are portable. forge_compute runs anywhere Python runs. This layer has no cloud migration trigger — it scales horizontally by adding compute instances, not by migrating to a managed service.

---

### ADR-007: PostgreSQL as Catalog

**Status:** Accepted | **Date:** 2026-03-05

#### Context

The system requires a relational store for catalog data: metric definitions, source definitions, instrument registry, metric lineage, asset registry, venue registry, event calendar, supply events, and adjustment factors. Requirements: foreign key integrity (metric_lineage references metric_catalog and source_catalog), audit trail (catalog changes are tracked), support for Dagster asset graph construction at startup (Dagster reads metric_catalog + metric_lineage to build the asset graph), zero time series data.

#### Options considered

**SQLite (rejected)**
No concurrent write support. The catalog is written by catalog seed migrations and read by multiple Dagster workers simultaneously. SQLite's write serialization would create contention. Disqualified: concurrent write limitation.

**ClickHouse for catalog as well (rejected)**
ClickHouse has no foreign key enforcement. Catalog integrity depends on referential constraints — a metric_lineage row that references a nonexistent metric_id must be rejected at write time, not discovered at query time. ClickHouse does not enforce this. Disqualified: no foreign key support.

**MongoDB (rejected)**
Document store. Relational integrity requires application-level enforcement rather than database-level constraints. The catalog's value is its enforced integrity — metric_id in metric_lineage always resolves to a valid metric_catalog row. Application-level enforcement is weaker and requires additional testing. Disqualified: no referential integrity.

#### Decision

PostgreSQL self-hosted, Docker container on proxmox, writing to `/mnt/empire-db` (2TB SSD). Existing empire_postgres container extended with `forge` schema for catalog tables. No time series data in PostgreSQL — ever.

#### Consequences

**Enables:** Full referential integrity across catalog tables. Dagster reads metric_catalog and metric_lineage at startup to build the asset graph — PostgreSQL is the authoritative source for this. Foreign key constraints catch catalog corruption at write time.

**Constrains:** PostgreSQL must be the first service started in the cold-start sequence — all other services depend on catalog availability. PostgreSQL backup is critical — catalog loss requires manual reconstruction from source documentation.

#### Migration path

**Managed equivalent:** AWS RDS for PostgreSQL
**What changes:** Connection string (host, port, credentials). Zero application code changes. Schema is fully compatible.
**Data migration:** `pg_dump` / `pg_restore`. Standard operation.
**Trigger:** See Split Triggers section.

---

## PHYSICAL DEPLOYMENT TOPOLOGY

### Machines

| Machine | IP | Role |
|---------|-----|------|
| proxmox | 192.168.68.11 | Production. All new-architecture services run here. |
| Server2 (srv-rack-02) | 192.168.68.12 | Binance Collector only. LXC 203 + VPN. Single-purpose. |
| bluefin | 192.168.68.64 | Development. Build and test here. Never edit on proxmox. |
| NAS | 192.168.68.91 | Backup destination only. No service or agent writes here. |

### Storage Mounts (proxmox)

| Mount | Capacity | Type | Contents |
|-------|----------|------|---------|
| `/` | 4TB | NVMe | OS, Docker engine, container layers |
| `/mnt/empire-db` | 2TB | SSD | PostgreSQL data, ClickHouse data, Dagster metadata |
| `/mnt/empire-data` | 4TB | SSD | MinIO data (Bronze Iceberg + Gold Iceberg) |

**Storage allocation rationale:**
- MinIO on `/mnt/empire-data` (4TB): Bronze raw payloads are the largest storage consumer. Projected 5-year total ~75 GB — well within capacity. Gold adds ~5 GB. Significant headroom.
- ClickHouse on `/mnt/empire-db` (2TB): Compressed Silver data projected at ~2.5 GB over 5 years. Shares the 2TB SSD with PostgreSQL (catalog, negligible size) and Dagster metadata (negligible size).
- Spare NVMe slot: Available for I/O isolation if ClickHouse write performance degrades under high BLC-01 ingest load. Not needed at current projected volumes.

### Docker Services (New Architecture)

All new-architecture services join the existing `docker-compose.yml` on proxmox. New services added:

| Service | Container Name | Port | Volume Mount | Notes |
|---------|---------------|------|-------------|-------|
| ClickHouse | empire_clickhouse | 8123 (HTTP), 9000 (native) | `/mnt/empire-db/clickhouse` | Silver observation store |
| MinIO | empire_minio | 9001 (API), 9002 (console) | `/mnt/empire-data/minio` | Bronze + Gold object storage |
| Dagster webserver | empire_dagster_webserver | 3010 | `/mnt/empire-db/dagster` | UI — internal only |
| Dagster daemon | empire_dagster_daemon | — | `/mnt/empire-db/dagster` | Schedules, sensors, runs |
| Dagster code server | empire_dagster_code | — | `/opt/empire/pipeline` | Asset definitions |

### BLC-01 Data Path

```
Binance WS (live)
  → LXC 203 on Server2 (192.168.68.12)
    → JSONL files (rolling, local storage)
      → rsync to proxmox landing directory
        → Dagster file sensor detects new files
          → Bronze adapter aggregates 8h, writes Iceberg to MinIO
            → Validation (Great Expectations)
              → Silver adapter writes to ClickHouse
```

The rsync pull routine from Server2 to proxmox is unbuilt as of 2026-03-05. This is a Phase 1 item. BLC-01 data is not available in the new system until this routine is built and the Bronze adapter is implemented.

### Network Paths

ClickHouse: internal Docker network only. Not exposed externally. The export Dagster asset connects via Docker internal network. No external port exposure required.

MinIO: internal Docker network for all service access. Console port (9002) accessible on LAN for operational use. Not exposed via Cloudflare tunnel.

Dagster webserver: accessible on LAN only (port 3010). Not exposed via Cloudflare tunnel. The 2am operational interface is accessed directly on the LAN.

---

## RESOURCE BOUNDARIES AND SCALING THRESHOLDS

### ClickHouse

| Metric | Threshold | Action | How to measure |
|--------|-----------|--------|----------------|
| Disk usage on `/mnt/empire-db` | 80% (1.6TB) | Evaluate cloud migration trigger | `df -h /mnt/empire-db` |
| Uncompressed row count in observations | 1 billion rows | Evaluate cloud migration trigger | `SELECT count() FROM forge.observations` |
| Background merge queue depth | > 100 parts | Investigate write throughput, consider OPTIMIZE scheduling | `SELECT * FROM system.merges` |
| Query latency for export scan | > 60 seconds | Investigate partition strategy, consider adding ordering key | Time the export asset in Dagster |
| Replication lag (if future multi-node) | N/A at single node | — | — |

**Current projected values:** ~72,000 rows/day, ~26M rows/year. At 1B row threshold: ~38 years at current volume. ClickHouse is not the scaling constraint.

### MinIO

| Metric | Threshold | Action | How to measure |
|--------|-----------|--------|----------------|
| Disk usage on `/mnt/empire-data` | 80% (3.2TB) | Migrate to S3 | `df -h /mnt/empire-data` or MinIO console |
| Bronze partition count | > 10,000 partitions | Evaluate Iceberg catalog service (Nessie) | `aws s3 ls --recursive s3://bronze/ \| wc -l` |
| MinIO write throughput | > 80% of SSD sustained write speed | Investigate write batching, consider dedicated NVMe | `iostat -x 1` during peak collection window |

**Current projected values:** ~17 GB/year total. At 3.2TB threshold: ~188 years at current volume. Storage is not the constraint.

### Dagster

| Metric | Threshold | Action | How to measure |
|--------|-----------|--------|----------------|
| Run history database size | > 10 GB | Archive old runs, consider Dagster Cloud | `du -sh /mnt/empire-db/dagster` |
| Concurrent asset materializations | > 20 simultaneous | Tune Dagster executor concurrency limits | Dagster UI → Runs tab |
| Sensor evaluation lag | > 5 minutes for file sensor | Investigate sensor tick frequency | Dagster UI → Sensors |

### PostgreSQL

| Metric | Threshold | Action | How to measure |
|--------|-----------|--------|----------------|
| Catalog table row count (any single table) | > 100,000 rows | Investigate — catalog tables should be small by design | `SELECT COUNT(*) FROM forge.metric_catalog` |
| Connection pool saturation | > 80% of `max_connections` | Tune PgBouncer or increase max_connections | `SELECT count(*) FROM pg_stat_activity` |

---

## MANAGED SERVICE MIGRATION TRIGGERS

### MinIO → AWS S3

```
Component:           MinIO (Bronze + Gold object storage)
Managed equivalent:  AWS S3 (+ optionally AWS Glue for Iceberg catalog)
Trigger metric:      Monthly active users on the external API
Trigger threshold:   50 paying customers OR /mnt/empire-data disk usage > 80%
How measured:        API auth logs for unique authenticated users per month;
                     df -h /mnt/empire-data
Lead time:           1-2 days (data sync) + 1 day (config + verification)
Code changes:        Zero. Endpoint config swap only.
                     MINIO_ENDPOINT → AWS region endpoint
                     MINIO_ACCESS_KEY → AWS_ACCESS_KEY_ID
                     MINIO_SECRET_KEY → AWS_SECRET_ACCESS_KEY
```

### ClickHouse Self-Hosted → ClickHouse Cloud

```
Component:           ClickHouse (Silver observation store)
Managed equivalent:  ClickHouse Cloud
Trigger metric:      Monthly active users on the external API OR
                     background merge queue depth consistently > 100 parts
Trigger threshold:   50 paying customers OR sustained merge queue > 100 parts
                     for > 7 consecutive days
How measured:        API auth logs; SELECT * FROM system.merges
Lead time:           1 week (data migration via S3 export/import)
Code changes:        Zero. Connection string swap only.
                     CLICKHOUSE_HOST, CLICKHOUSE_PORT, CLICKHOUSE_USER,
                     CLICKHOUSE_PASSWORD — all environment variables.
Schema compatibility: Full. ClickHouse Cloud runs identical engine and DDL.
```

### Dagster Self-Hosted → Dagster Cloud

```
Component:           Dagster (orchestration)
Managed equivalent:  Dagster Cloud (with local agent)
Trigger metric:      Operator time spent on Dagster infrastructure maintenance
Trigger threshold:   > 4 hours/month on Dagster infrastructure (upgrades,
                     daemon restarts, metadata DB maintenance)
How measured:        Operator judgment. Track time explicitly.
Lead time:           1-2 days
Code changes:        Zero. Asset definitions are fully portable.
                     Dagster Cloud runs a local agent that executes assets
                     in the existing Docker environment. Only the scheduler
                     and UI move to the cloud.
```

### PostgreSQL Self-Hosted → AWS RDS

```
Component:           PostgreSQL (catalog)
Managed equivalent:  AWS RDS for PostgreSQL
Trigger metric:      Monthly active users (drives need for managed HA and backups)
Trigger threshold:   50 paying customers
How measured:        API auth logs
Lead time:           1 day (pg_dump / pg_restore + config swap)
Code changes:        Zero. Connection string swap only.
                     DB_HOST, DB_PORT, DB_USER, DB_PASSWORD,
                     DB_NAME — all environment variables.
```

---

## ONE-OPERATOR VIABILITY

### Monitoring Signals

The 2am operational interface is the Dagster UI (port 3010, LAN access). Every data collection failure, staleness violation, and export failure is visible here before it affects downstream systems.

**Primary signals to watch:**

| Signal | Location | Meaning |
|--------|----------|---------|
| Asset materialization failures | Dagster UI → Assets | Adapter or validation failure |
| Freshness violations | Dagster UI → Assets (stale indicator) | Source missed cadence |
| File sensor not ticking | Dagster UI → Sensors | BLC-01 rsync failure |
| Export asset failure | Dagster UI → Assets | Gold not updated, features stale |
| ClickHouse merge queue depth | ClickHouse system.merges | Write throughput degradation |
| MinIO disk usage | `df -h /mnt/empire-data` | Storage pressure |

### Component Failure Modes and Recovery

#### PostgreSQL failure

**Failure mode:** Container crash, disk corruption, or connection exhaustion.
**Detection:** Dagster asset materializations fail immediately — catalog reads fail at adapter startup. All adapters log PostgreSQL connection errors.
**Recovery procedure:**
1. `docker restart empire_postgres` — resolves most container crashes (< 2 minutes)
2. If data corruption: restore from most recent backup on NAS. `pg_restore` from backup file. Catalog data is slowly-changing — daily backup is sufficient. (< 30 minutes)
3. If connection exhaustion: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle'` to clear idle connections. Investigate connection pooling. (< 5 minutes)

**Data loss risk:** Low. Catalog data changes only when new sources or metrics are added. Daily backup is adequate.

#### ClickHouse failure

**Failure mode:** Container crash, disk full, or merge storm (background merges consuming all I/O).
**Detection:** Adapter writes fail with connection errors. Dagster asset materializations fail. Export asset fails.
**Recovery procedure:**
1. `docker restart empire_clickhouse` — resolves most container crashes (< 5 minutes)
2. If disk full: `TRUNCATE TABLE forge.dead_letter` if dead_letter has grown unexpectedly large. Check `/mnt/empire-db` usage. (< 10 minutes)
3. If merge storm: `SYSTEM STOP MERGES` to halt background merges temporarily. Investigate with `SELECT * FROM system.merges`. Resume with `SYSTEM START MERGES`. (< 15 minutes)
4. If data corruption: ClickHouse has built-in checksums. Corrupted parts are logged in `system.errors`. Remove corrupt parts with `ALTER TABLE forge.observations DROP DETACHED PARTITION`. Re-run export job to rebuild Gold from surviving Silver data. (< 60 minutes)

**Data loss risk:** Low. ClickHouse writes are durable to disk on commit. Container crash does not lose committed data. ReplacingMergeTree handles duplicate writes from adapter retries — re-running any adapter after recovery is safe.

#### MinIO failure

**Failure mode:** Container crash, disk full on `/mnt/empire-data`, or object corruption.
**Detection:** Bronze adapter writes fail. Export job fails (cannot write Gold).
**Recovery procedure:**
1. `docker restart empire_minio` — resolves most container crashes (< 2 minutes)
2. If disk full: Bronze 90-day retention policy should prevent this. Run manual Iceberg snapshot expiration to recover space. `python -c "from pyiceberg.catalog import load_catalog; ..."` (< 15 minutes with runbook)
3. If Gold corruption: Gold is reconstructable. Re-run the Silver → Gold export Dagster asset from scratch. Full Gold rebuild from Silver takes as long as the export job runs — at current volume, under 30 minutes.
4. Bronze corruption is more serious — raw payloads are lost. Recovery requires re-fetching from source APIs. Most sources support historical data fetch. BLC-01 tick data has no historical refetch path — the only copy is in JSONL on Server2.

**Data loss risk:** Bronze data loss is the highest-severity failure. NAS backup is the mitigation. Verify NAS backup cadence before Phase 1 goes live.

#### Dagster failure

**Failure mode:** Daemon crash (schedules stop firing), webserver crash (UI unavailable but schedules continue if daemon is running), code server crash (asset definitions unavailable).
**Detection:** Schedules miss their cadence. Assets show as stale in UI (when UI recovers).
**Recovery procedure:**
1. `docker restart empire_dagster_daemon empire_dagster_webserver empire_dagster_code` (< 3 minutes)
2. If metadata database corrupted: Dagster run history is a log, not source data. Delete the SQLite file and restart — Dagster rebuilds its metadata from asset definitions. No source data is lost. (< 10 minutes)
3. Missed runs: Dagster's `catchup` behavior is configurable per asset. Default is to run once when the daemon recovers, not to replay all missed intervals. Verify this is correct per asset — for Bronze landing, one run per cadence is correct. (No action needed beyond restart.)

**Data loss risk:** None for source data. Dagster metadata (run history) can be rebuilt.

#### BLC-01 (Server2 / rsync) failure

**Failure mode:** LXC 203 crash, VPN disconnection, rsync failure, or Server2 network partition.
**Detection:** Dagster file sensor stops triggering (no new files in landing directory). Asset shows stale.
**Recovery procedure:**
1. SSH to Server2: `ssh root@192.168.68.12`. Check LXC 203 status in Proxmox on Server2.
2. Verify VPN connection. Verify Binance WS collector process is running.
3. Verify rsync service/cron is running. Check rsync logs for errors.
4. BLC-01 data loss during outage is not recoverable — tick liquidation data is real-time only. Accept the gap. The system handles null states in feature compute (SOURCE_STALE).

**Data loss risk:** Any BLC-01 downtime results in permanent tick data loss. No recovery path. This is an accepted risk documented in the design. The BLC-01 signal is null-propagated during gaps — the signal degrades honestly rather than crashing.

---

## COLD-START SEQUENCE

This is the procedure to reconstruct the full system from a bare proxmox machine. Execute in order. Do not skip steps.

```
1. STORAGE
   Verify /mnt/empire-db (2TB SSD) and /mnt/empire-data (4TB SSD) are mounted.
   df -h /mnt/empire-db /mnt/empire-data
   Both must show correct capacities before proceeding.

2. POSTGRESQL (Catalog — all other services depend on this)
   docker compose up -d empire_postgres
   Wait for healthy: docker compose ps empire_postgres
   Run catalog seed migration:
   cat db/migrations/0001_phase0_schema.sql | docker exec -i empire_postgres psql \
     -U crypto_user -d crypto_structured
   Verify: SELECT COUNT(*) FROM forge.metric_catalog;
   Expected: > 0 rows (seed count from Phase 0 completion report)

3. CLICKHOUSE (Silver — must exist before adapters write)
   docker compose up -d empire_clickhouse
   Wait for healthy: curl http://localhost:8123/ping
   Run ClickHouse DDL migration:
   cat db/migrations/clickhouse/0001_silver_schema.sql | \
     docker exec -i empire_clickhouse clickhouse-client --multiquery
   Verify: SHOW TABLES FROM forge; (expect: observations, dead_letter, current_values)

4. MINIO (Bronze + Gold — must exist before adapters write)
   docker compose up -d empire_minio
   Wait for healthy: curl http://localhost:9001/minio/health/live
   Create buckets if not restored from backup:
   mc alias set local http://localhost:9001 $MINIO_ACCESS_KEY $MINIO_SECRET_KEY
   mc mb local/bronze local/gold
   Verify: mc ls local/

5. DAGSTER (Orchestration — last, after all targets verified reachable)
   docker compose up -d empire_dagster_daemon empire_dagster_webserver \
     empire_dagster_code
   Wait for webserver: curl http://localhost:3010
   Verify asset graph loaded: check Dagster UI → Assets tab
   Expected: all assets from metric_catalog visible

6. CONNECTIVITY VERIFICATION
   From Dagster code server, verify all three targets reachable:
   - PostgreSQL: catalog read test
   - ClickHouse: SELECT 1 FROM forge.observations LIMIT 1 (may return empty — that is fine)
   - MinIO: list bronze bucket

7. FIRST COLLECTION RUN
   Trigger one adapter manually in Dagster UI to verify full path:
   Bronze write → Great Expectations validation → Silver write → dead_letter on rejection
   Verify row appears in ClickHouse: SELECT count() FROM forge.observations

8. EXPORT VERIFICATION
   Manually trigger the Silver → Gold export asset in Dagster UI.
   Verify Iceberg table created in MinIO gold bucket.
   Verify DuckDB can read it: duckdb -c "SELECT count(*) FROM iceberg_scan('s3://gold/...')"
```

---

## KNOWN INFRASTRUCTURE GAPS

| Gap | Description | Resolution trigger |
|-----|-------------|-------------------|
| BLC-01 rsync routine | Pull routine from Server2 to proxmox is unbuilt | Phase 1 |
| ClickHouse DDL migration file | 0001_silver_schema.sql does not yet exist | Phase 0 corrective action |
| MinIO bucket initialization | Buckets not yet created | Phase 0 corrective action |
| Dagster service definition | Docker service not yet in docker-compose.yml | Phase 1 |
| Great Expectations setup | GE not yet installed; validation rules not yet derived from metric_catalog | Phase 1 |
| Silver → Gold export asset | Dagster asset not yet written | Phase 2 |
| NAS backup cadence for MinIO | Backup job not yet configured | Phase 1, before live collection |
| Dagster metadata DB backup | Backup job not yet configured | Phase 1 |

---

## APPENDIX: SOURCE CATALOG (v1)

10 sources at v1. All other sources from the legacy EDS system are excluded, deprecated, or deferred.

| Source | Provides | ToS status | Redistribution |
|--------|----------|-----------|----------------|
| Coinalyze | Perpetual futures — funding, OI, liquidations, L/S ratio (121 instruments) | Unaudited | Pending Phase 6 audit |
| DeFiLlama | Protocol TVL, DEX volume, lending rates, stablecoins, fees, revenue | Low risk | Yes |
| FRED | 23 macro series (yields, VIX, SPX, DXY, employment, CB balance sheets) | None (public domain) | Yes |
| Tiingo | OHLCV (crypto + equities) | Paid commercial tier | Yes (paid) |
| SoSoValue | ETF flows (BTC/ETH/SOL) | Non-commercial only | No |
| Etherscan/Explorer | Exchange flows — ETH + ARB, 9 exchanges, 18 instruments | Unaudited | Pending Phase 6 audit |
| CoinPaprika | Market cap, price data | Low risk | Yes |
| CoinMetrics | On-chain transfer volume (GitHub CSVs) | Unaudited | No (pending audit) |
| BGeometrics | MVRV, SOPR, NUPL, Puell (BTC/ETH) | Unaudited | Pending Phase 6 audit |
| Binance (BLC-01) | Tick-level liquidation events (~70k/day, 100+ symbols) | Unaudited | Pending Phase 6 audit |

**Excluded permanently:** Santiment, Glassnode (deprecated), BSCScan (deprecated), Solscan (deprecated)
**Parked (paid, not in budget):** CoinGlass, CryptoQuant, CoinMarketCap
**T3 fallback (not catalogued):** CoinGecko, KuCoin

---

## APPENDIX: METRIC CATALOG ADDITIONS IDENTIFIED

The following metrics are collected in the legacy EDS system and should be added to the new metric catalog during Phase 1. All are covered by existing sources with no new infrastructure cost.

**FRED expansion (add to metric catalog — currently missing):**

`macro.rates.yield_30y` · `macro.rates.yield_10y_2y_spread` · `macro.rates.yield_10y_3m_spread` · `macro.rates.real_yield_10y` · `macro.rates.breakeven_inflation_10y` · `macro.equities.sp500` · `macro.volatility.vix` · `macro.fx.wti_crude` · `macro.money.m2_supply` · `macro.money.monetary_base` · `macro.cb.fed_total_assets` · `macro.cb.ecb_total_assets` · `macro.cb.boj_total_assets` · `macro.employment.nonfarm_payrolls` · `macro.employment.initial_claims` · `macro.inflation.cpi_all_urban` · `macro.inflation.core_pce` · `macro.gdp.real_growth`

**FRED gap fill (add series to FRED adapter — missing from current 23):**
`macro.credit.hy_oas` — FRED series `BAMLH0A0HYM2`. Already in feature catalog and signal design. Not currently in FRED adapter. Add before Phase 1 FRED adapter build.

**DeFiLlama expansion (add to metric catalog):**
`defi.protocol.fees_usd_24h` · `defi.protocol.revenue_usd_24h`

**BTC exchange flows (documented gap):**
BTC directional exchange flows (inflow/outflow by exchange) are not covered by any v1 source. CoinMetrics provides transfer volume but not exchange-specific directional flows. This metric null-propagates in Capital Flows pillar for BTC. Documented as a v1.1 gap — resolution requires either CryptoQuant (paid, parked) or a dedicated on-chain address tracking solution.

---

*Architecture locked: 2026-03-05. All technology decisions in this document require architect approval to reopen. Build sessions reference section and ADR numbers. thread_infrastructure.md is the authoritative infrastructure reference until superseded by a versioned revision.*
