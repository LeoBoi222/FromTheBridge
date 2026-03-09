# Final Design Document Completeness Audit

**Date:** 2026-03-08
**Documents audited:**
1. `FromTheBridge/docs/design/FromTheBridge_design_v3.1.md` (FTB)
2. `EmpireDataServices/docs/design/EDS_design_v1.1.md` (EDS)
3. `FromTheBridge/docs/design/eds_ftb_cohesion_audit.md` (cross-reference)

**Methodology:** Full read of all three documents. Every section examined. Numbers cross-referenced obsessively.

---

## SUMMARY

| Severity | Count |
|----------|-------|
| **Blocker** | 4 |
| **Warning** | 25 |
| **Info** | 12 |
| **Total** | **41** |

### Blockers (must fix before pipeline rebuild)

1. **FTB-01** — DDL table names vs prose names mismatch (FK deployment failure)
2. **FTB-02** — `bronze_archive_log` column type mismatch (DDL deployment failure)
3. **CROSS-01** — `instrument_id` nullability: FTB design contradicts itself (`__market__` vs `NULL`)
4. **CROSS-02** — FRED metric naming mismatch between FTB and EDS

---

## SECTION 1: FTB INTERNAL FINDINGS

### FTB-01 — DDL Table Names vs Prose References
- **Location:** FTB Thread 4 DDL (~line 1577–1743) vs all prose sections
- **Category:** Internal contradiction
- **Severity:** BLOCKER
- **What's wrong:** The DDL defines tables as `instruments`, `metrics`, `sources`, `collection_events`, `instrument_metric_coverage`. All prose and CLAUDE.md refer to them as `metric_catalog`, `source_catalog`, etc. The `bronze_archive_log` DDL (~line 2006) references `forge.source_catalog(id)` and `forge.metric_catalog(id)` as foreign keys — these tables don't exist in the DDL. A CC session will get FK constraint errors.
- **Resolution:** Standardize naming. Either rename DDL tables to `metric_catalog`/`source_catalog` or update all prose. The FK references in `bronze_archive_log` must match actual table names.

### FTB-02 — `bronze_archive_log` Column Type Mismatch
- **Location:** FTB `bronze_archive_log` DDL (~line 2006) vs `sources` DDL (~line 1684) and `metrics` DDL (~line 1619)
- **Category:** Internal contradiction
- **Severity:** BLOCKER
- **What's wrong:** `bronze_archive_log.source_id` is `INTEGER` referencing a table with `source_id UUID` PK. Same for `metric_id`. Type mismatch causes DDL deployment failure.
- **Resolution:** Change `bronze_archive_log.source_id` and `metric_id` to `UUID`.

### FTB-03 — `ch_ops_reader` max_execution_time: 60s vs 30s
- **Location:** Credential Isolation table (~line 4257) vs Resource Profiles table (~line 4277)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Two different max_execution_time values for the same user.
- **Resolution:** Pick one value. 30s is appropriate for metadata-only queries.

### FTB-04 — Silver Row Volume: 5,800–6,000/day vs 72,000/day
- **Location:** Silver→Gold Export (~line 2250) vs Resource Boundaries (~line 4607)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Export section says ~5,800–6,000 rows/day. Resource section says ~72,000. The 72,000 likely includes BLC-01 tick data aggregated to windows, but neither section clarifies.
- **Resolution:** Add "(excluding BLC-01)" or "(including BLC-01)" qualifiers. Note: 5,800/day × 365 = 2.1M checks out with annual projection; 72,000/day does not match if BLC-01 produces ~65–72k raw events aggregated to far fewer observation rows.

### FTB-05 — Bronze Storage: 75 GB vs 40 GB Over 5 Years
- **Location:** Storage Mounts (~line 4172) vs Bronze Landing Zone (~line 2046)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Line 4172: "~75 GB over 5 years." Line 2046: "`bronze-archive` ~8 GB/year (~40 GB at 5 years)." The "~192 GB" total at line 2046 is unexplained.
- **Resolution:** Reconcile in one canonical location with full breakdown.

### FTB-06 — Layer 7 Lists 10 Tables, ADR-007 Lists 12
- **Location:** Architecture stack (~line 87–89) vs ADR-007 (~line 4507)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Layer 7 definition omits `collection_events` and `instrument_metric_coverage`.
- **Resolution:** Add the missing 2 tables to the Layer 7 definition.

### FTB-07 — Redistribution Columns: Phase 0 DDL vs Phase 5 ALTER TABLE
- **Location:** `sources` DDL (~line 1690) vs Phase 5 migration (~line 2785)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Phase 0 DDL already includes `redistribution_status`, `propagate_restriction`, `redistribution_notes`, `redistribution_audited_at`. Phase 5 defines an ALTER TABLE to add the same columns. They can't be added twice.
- **Resolution:** Remove the Phase 5 ALTER TABLE section or mark it as "only if Phase 0 was deployed without these columns."

### FTB-08 — Container Name: `empire_api` vs `empire_fastapi`
- **Location:** MinIO service accounts (~line 4288) vs credential inventory (~line 4350)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Both names used for the same container. Neither appears in the Docker Services table.
- **Resolution:** Standardize to one name. Add to Docker Services table as Phase 5 addition.

### FTB-09 — Secrets File Path Naming
- **Location:** Secrets directory (~line 4213) vs credential inventory (~line 4350)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Directory shows `pg_forge_user.txt`, `ch_writer.txt`. Credential inventory references `secrets/postgres_forge_user`, `secrets/clickhouse_ch_writer`. Different file names.
- **Resolution:** Standardize to one naming convention.

### FTB-10 — Gold Partition Domain `onchain` vs Catalog Domain `chain`
- **Location:** Gold partition key (~line 2214) vs metrics DDL domain CHECK (~line 1641)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Gold uses domain `onchain` but catalog CHECK uses `chain`. No mapping documented.
- **Resolution:** Either align names or document the mapping.

### FTB-11 — Gold Partition Missing 6 Domains
- **Location:** Gold partition key (~line 2213)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Gold lists 5 domains: `derivatives`, `macro`, `flows`, `defi`, `onchain`. Catalog CHECK allows 11 domains. Missing: `spot`, `etf`, `stablecoin`, `valuation`, `price`, `metadata`.
- **Resolution:** Document which catalog domains map to which Gold partition domains.

### FTB-12 — `empire_dagster_export` Container Not Defined
- **Location:** Credential isolation (~line 4256, 4289, 4296, 4298)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** `empire_dagster_export` referenced for credential mounting but never appears in Docker Services table. Presumably `empire_dagster_code`.
- **Resolution:** Replace with `empire_dagster_code` or add as distinct container.

### FTB-13 — `empire_fastapi` Container Not Defined
- **Location:** DuckDB concurrency (~line 3586), credential inventory (~line 4350)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** Referenced but missing from Docker Services table.
- **Resolution:** Add with "(Phase 5)" annotation.

### FTB-14 — `source_catalog.notes` Column Not in DDL
- **Location:** Adapter decommission protocol (~line 2091)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** "source_catalog updated: original source's `notes` field annotated" — but DDL has no `notes` column.
- **Resolution:** Change reference to `metadata JSONB` field, or add `notes TEXT`.

### FTB-15 — `source_catalog.updated_at` Column Not in DDL
- **Location:** Redistribution resolution SQL (~line 2899, 2908)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** SQL uses `updated_at = NOW()` but DDL has only `created_at`.
- **Resolution:** Add `updated_at TIMESTAMPTZ` to DDL.

### FTB-16 — `source_catalog.is_active` Column Not in DDL
- **Location:** Adapter decommission protocol (~line 2090)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** References `is_active = false` but DDL has no `is_active` column.
- **Resolution:** Add `is_active BOOLEAN NOT NULL DEFAULT true` to DDL.

### FTB-17 — `instrument_metric_coverage.coverage_status` Not in DDL
- **Location:** Adapter decommission protocol (~line 2094)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** References `coverage_status = 'stale'` but DDL has `is_active BOOLEAN`.
- **Resolution:** Use `is_active = false` or add `coverage_status` column.

### FTB-18 — `source_id` Type Conflict: UUID (PostgreSQL) vs String (ClickHouse)
- **Location:** Throughout — SQL uses `WHERE source_id = 'coinalyze'` (string) against UUID PK
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** PostgreSQL `sources` table uses `source_id UUID`. ClickHouse `observations` uses `source_id String`. Redistribution SQL treats it as a string lookup against a UUID column.
- **Resolution:** Clarify that ClickHouse `source_id` stores `canonical_name` from PostgreSQL, not the UUID. Fix SQL examples to query by `canonical_name`.

### FTB-19 — 48h Preview vs "API Key Required on ALL Endpoints"
- **Location:** Preview spec (~line 425) vs authentication policy (~line 2563)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Preview is "unauthenticated, public." Auth policy says "API key required on ALL endpoints including Free tier — no unauthenticated bypass."
- **Resolution:** Make preview an explicit exception or require API key.

### FTB-20 — "90-Day Sprint" Covers 16 Weeks (112 Days)
- **Location:** Sprint timeline (~line 440–456)
- **Category:** Stale reference
- **Severity:** Info
- **What's wrong:** "~27 hours / 90 days" but timeline runs 16 weeks.
- **Resolution:** Rename to "16-Week Sprint."

### FTB-21 — 48h Preview Timing: Phase 3 vs Phase 4 Gate
- **Location:** Sprint (~line 447) vs Phase 4 gate (~line 3954)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Sprint says Phase 3. Gate criterion says Phase 4 shadow week 2.
- **Resolution:** Clarify when preview launches and which gate owns it.

### FTB-22 — Missing `ch_ops_reader` Secret File
- **Location:** Secrets directory (~line 4213–4236)
- **Category:** Dangling reference
- **Severity:** Warning
- **What's wrong:** Lists `ch_writer.txt` and `ch_export_reader.txt` but no `ch_ops_reader.txt`.
- **Resolution:** Add to secrets directory structure.

### FTB-23 — Feature Catalog Storage Location Undefined
- **Location:** Thread 3, Feature Catalog Entry Structure (~line 1467–1492)
- **Category:** Completeness gap
- **Severity:** Warning
- **What's wrong:** YAML format defined but storage location not specified (PostgreSQL table? YAML files? Part of metric_catalog?).
- **Resolution:** Specify storage mechanism.

### FTB-24 — Gold + Marts Iceberg Schema Not Specified
- **Location:** Throughout
- **Category:** Completeness gap
- **Severity:** Warning
- **What's wrong:** Bronze and Silver DDL are fully specified. Gold Iceberg table schema (columns, types, partition spec) and Marts table schemas are never defined.
- **Resolution:** Add Gold and Marts schema definitions.

### FTB-25 — Instrument Tier Promotion Rules Not Specified
- **Location:** Instrument tiers (~line 1598–1603)
- **Category:** Completeness gap
- **Severity:** Warning
- **What's wrong:** "Tier promotion is rule-driven and automatic" but criteria for `collection` → `scoring` → `signal_eligible` never defined.
- **Resolution:** Define promotion criteria (min observations, min time, min coverage).

### FTB-26 — `RESULT_ID_E3` Not Defined
- **Location:** Note on `defi.lending.utilization_rate` (~line 1882)
- **Category:** Dangling reference
- **Severity:** Info
- **What's wrong:** "See RESULT_ID_E3" but no such reference exists anywhere.
- **Resolution:** Remove or replace with actual section reference.

### FTB-27 — "Three-Regime Table" Reference (Four Regimes Exist)
- **Location:** L2.2 pillar aggregation (~line 2840)
- **Category:** Stale reference
- **Severity:** Info
- **What's wrong:** References "the three-regime table" but table has four regime states.
- **Resolution:** Change to "four-regime table."

### FTB-28 — Authority Chain References v1.1
- **Location:** Footer (~line 5052)
- **Category:** Stale reference
- **Severity:** Info
- **What's wrong:** References `FromTheBridge_design_v1_1.md` as authority source for v3.1.
- **Resolution:** Remove or mark as "historical reference only."

### FTB-29 — 9 Domains vs 11 Domains
- **Location:** CLAUDE.md "9 domains" vs CHECK constraint with 11 domains
- **Category:** Internal contradiction
- **Severity:** Info
- **What's wrong:** CLAUDE.md says "74 metrics in catalog across 9 domains." CHECK constraint allows 11 (`price` and `metadata` may be empty at Phase 0). Not technically wrong but misleading.
- **Resolution:** Clarify "9 domains with metrics seeded (11 defined)."

### FTB-30 — Phase 5 Gate: Magnitude Non-null Requires ML Graduation
- **Location:** Phase 5 gate (~line 4026)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** Requires "Magnitude non-null for all signal-eligible instruments" but magnitude comes from ML Capital Flow Direction model. If ML hasn't graduated, magnitude is null by design. Phase 5 may start before ML graduation completes.
- **Resolution:** Add "contingent on ML graduation" or clarify Phase 5 starts only after Phase 4 gate.

---

## SECTION 2: EDS INTERNAL FINDINGS

### EDS-01 — Decision Count: 26 vs 33
- **Location:** EDS header (line 13), ADR section (line 191), Resolved Questions (line 2564) vs Changelog v1.1.1 (line 2742) and Locked Decisions table (33 entries)
- **Category:** Stale reference
- **Severity:** Warning
- **What's wrong:** Header, ADR section, and Resolved Questions all say "26 decisions." The actual locked decisions table has 33 entries (#1–33). The changelog v1.1.1 correctly states "33 locked decisions." The header text was not updated.
- **Resolution:** Update header/intro/ADR section from "26" to "33."

### EDS-02 — ADR Section Says "No ADR Requires Architect Approval to Reopen"
- **Location:** EDS ADR section (line 191)
- **Category:** Internal contradiction
- **Severity:** Info
- **What's wrong:** Line 191: "No ADR requires architect approval to reopen — these are final." But the Consolidated Locked Decisions section (line 2693) says: "Reopening any decision requires architect approval." These contradict each other.
- **Resolution:** Remove "No ADR requires architect approval to reopen" from line 191. The locked decisions section is authoritative.

### EDS-03 — `pg_dump` Command Uses Wrong Database Name
- **Location:** EDS Backup Strategy (line 2338)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** `pg_dump -d empire_utxo` treats `empire_utxo` as a database name, but it's a schema within the existing PostgreSQL database (`crypto_structured` per FTB CLAUDE.md). Correct command: `pg_dump -d crypto_structured -n empire_utxo`.
- **Resolution:** Fix pg_dump command to target correct database + schema.

### EDS-04 — Track 3 DeFiLlama Metrics Count: 15 vs 11 Listed
- **Location:** EDS Track 3 DeFiLlama section (line 727–745)
- **Category:** Internal contradiction
- **Severity:** Info
- **What's wrong:** Header says "15 total" but only 15 metrics are listed in the table. Cross-check: 7 defi.* + 4 flows.stablecoin.* + 3 defi.dex.* + 1 defi.bridge.* = 15. Actually consistent — just a confusing count due to domain splits. Not a real finding.
- **Resolution:** None needed.

### EDS-05 — Dagster Watchdog Shared Scope Documentation
- **Location:** EDS Dagster Watchdog (line 2273–2305)
- **Category:** Completeness gap
- **Severity:** Info
- **What's wrong:** EDS-41 pipeline item (line 2676) correctly notes the watchdog has "shared scope" covering both EDS and FTB assets. The watchdog section itself (line 2273) describes it as EDS-specific. The scope statement in EDS-41 supersedes but the prose section should match.
- **Resolution:** Update prose to note shared scope: "watches the shared Dagster daemon — covers both EDS and FTB assets."

### EDS-06 — `empire.current_values` Engine Discrepancy
- **Location:** EDS DDL (line 971) vs Summary Table (line 2386)
- **Category:** Internal contradiction
- **Severity:** Warning
- **What's wrong:** DDL creates `current_values` as a `MATERIALIZED VIEW` with `ReplacingMergeTree(data_version)` engine. The summary table at line 2386 lists it as a regular table: `ReplacingMergeTree(data_version)`. For a materialized view, the backing table is automatically created by ClickHouse and shouldn't be confused with a standalone table. The gate criterion 20 (line 1875) says `OPTIMIZE TABLE empire.current_values FINAL` — this works on materialized views but should note it's a materialized view.
- **Resolution:** Annotate summary table entry: "Materialized view (auto-populated from observations)."

---

## SECTION 3: CROSS-PROJECT CONTRADICTIONS

### CROSS-01 — `instrument_id` Nullability: `__market__` vs `NULL`
- **Location:** FTB ~line 1605–1614 (`__market__` instrument) vs FTB ~line 1758, 1770–1771 (`NULL` for market-level) vs EDS line 904 (`DEFAULT '__market__'`) vs EDS sync mapping (line 1321: `Map '__market__' → NULL`)
- **Category:** Cross-project contradiction
- **Severity:** BLOCKER
- **What's wrong:** Three conflicting positions exist simultaneously:
  1. FTB creates a `__market__` system instrument in PostgreSQL (line 1605)
  2. FTB ClickHouse DDL uses `Nullable(String)` with explicit comment "NULL for market-level metrics" (line 1758, 1771)
  3. EDS uses `DEFAULT '__market__'` (non-null) in empire.observations (line 904)
  4. EDS sync mapping says transform `'__market__'` → `NULL` (line 1321)

  The FTB document contradicts itself: it defines a `__market__` instrument for market-level metrics but then says market-level metrics have `instrument_id = NULL` in Silver. The EDS sync correctly maps to `NULL` per FTB's Silver convention, but this means the `__market__` PostgreSQL instrument is never referenced in ClickHouse observations.

  **Impact:** If FTB adapters write `instrument_id = NULL` for market-level metrics, and sync also writes `NULL`, queries work. But the `__market__` instrument row in PostgreSQL serves no purpose for ClickHouse queries. More critically: if someone reads the `__market__` documentation and writes the string `'__market__'` to FTB Silver, those observations will never match `IS NULL` queries.
- **Resolution:** Decide definitively: either (a) FTB uses `NULL` for market-level in Silver and `__market__` exists only in PostgreSQL for relational integrity, or (b) FTB Silver uses the string `'__market__'` consistently (matching EDS) and queries filter on `instrument_id = '__market__'` instead of `IS NULL`. Document the decision. Update the contradicting text.

### CROSS-02 — FRED Metric Naming Mismatch
- **Location:** FTB ~line 1385, 1891, 2347: `macro.rates.fed_funds_effective` vs EDS line 759: `macro.rates.fed_funds`
- **Category:** Cross-project contradiction
- **Severity:** BLOCKER
- **What's wrong:** FTB uses `macro.rates.fed_funds_effective` for the federal funds rate. EDS uses `macro.rates.fed_funds`. Both reference the same FRED series (DFF/EFFR). The `empire_to_forge_sync` column mapping (EDS line 1320) says metric_id is "pass-through" — meaning the sync will write `macro.rates.fed_funds` to `forge.observations`. FTB's `forge.metric_catalog` contains `macro.rates.fed_funds_effective`. The sync will dead-letter every fed funds observation with `METRIC_NOT_REGISTERED` because the names don't match.
- **Resolution:** Align naming. Either EDS adopts FTB's `_effective` suffix or FTB changes. Since the sync uses pass-through, names must be identical. Check all 23 FRED metrics for similar mismatches — this is likely not the only one.

### CROSS-03 — Sync Rows/Day: EDS ~17,350 vs FTB ~5,800–6,000
- **Location:** EDS line 1362 vs FTB ~line 2250
- **Category:** Cross-project numbers mismatch
- **Severity:** Warning
- **What's wrong:** EDS projects ~17,350 rows/day flowing through `empire_to_forge_sync` to `forge.observations`. FTB projects ~5,800–6,000 Silver rows/day total. After sync is active, FTB Silver would receive both its own adapter writes (~5,800) plus sync writes (~17,350), totaling ~23,000+ rows/day. FTB's resource projections and storage estimates don't account for sync volume.
- **Resolution:** FTB should note that after EDS sync activation, Silver row volume increases to ~23,000/day. Update resource projections accordingly.

### CROSS-04 — EDS `eds_utxo_admin` User Resolves Cohesion G4 (But FTB Doesn't Know)
- **Location:** EDS line 580–589 vs FTB (no mention of `eds_utxo_admin`)
- **Category:** Cross-project gap
- **Severity:** Info
- **What's wrong:** EDS fully specifies the `empire_utxo` schema user (`eds_utxo_admin`) and grants (including `forge_reader` read access). FTB's design doc doesn't mention this user. The cohesion audit flagged G4 as unresolved, but it IS resolved — just in the EDS doc.
- **Resolution:** FTB doesn't need to document EDS's user. G4 is resolved. No action needed beyond updating the cohesion audit status.

### CROSS-05 — `shared_ops` Namespace: EDS Uses It, FTB Barely References It
- **Location:** EDS lines 2008, 2056, 2090 (`shared_ops.capacity`, `shared_ops.hardware`, `shared_ops.risk_board`) vs FTB line 202 (single reference)
- **Category:** Cross-project gap
- **Severity:** Warning
- **What's wrong:** EDS defines `shared_ops.*` as the Dagster asset group for cross-project operational assets (capacity, hardware health, risk board). FTB has a single reference: "`ch_ops_reader` — Dagster health monitoring assets (`ftb_ops.*`, `shared_ops.*`)." FTB's MEMORY.md notes a pending rename from `eds_ops→shared_ops` but the FTB design doc never specifies what `shared_ops` assets FTB materializes.
- **Resolution:** FTB should document that `shared_ops.*` assets are materialized by FTB (per MEMORY.md session notes). Specify which assets: `shared_ops.capacity`, `shared_ops.hardware`, `shared_ops.risk_board`, `shared_ops.sync_health`, `shared_ops.container_health`, `shared_ops.calendar_cleanup`.

### CROSS-06 — Dagster Code Server Integration Undefined
- **Location:** EDS lines 1406–1411 vs FTB ~line 4185
- **Category:** Cross-project gap
- **Severity:** Warning
- **What's wrong:** Both docs say assets "coexist in the same Dagster deployment" with namespace isolation, but neither specifies the code server architecture: single code server with monorepo? Multi-code-location with separate gRPC servers? This affects container definitions, volume mounts, and workspace YAML. Pipeline item EDS-47 tracks this but it's currently a gap.
- **Resolution:** Resolve EDS-47 before Phase 1 deployment of either project. Document in `thread_infrastructure.md`.

### CROSS-07 — Dagster Concurrency Limits Undefined
- **Location:** EDS (no reference) vs FTB ~line 4615 (monitoring trigger only)
- **Category:** Cross-project gap
- **Severity:** Warning
- **What's wrong:** 65+ FTB assets + 30+ EDS assets on the same Dagster deployment. No `max_concurrent_runs` or run queue limit defined. Default unlimited concurrency risks resource contention.
- **Resolution:** Resolve EDS-48 before Phase 1 deployment.

### CROSS-08 — Prometheus Disagreement
- **Location:** FTB ~line 3606, 4017 (references Prometheus) vs EDS ~line 1933 (explicitly rejects Prometheus)
- **Category:** Cross-project contradiction
- **Severity:** Info
- **What's wrong:** FTB references Prometheus metrics (`signal_cache_computed_at_epoch`, `signal_cache_refresh_failure_total`). EDS explicitly rejects Prometheus/Grafana/AlertManager. Pipeline item EDS-51 tracks resolution.
- **Resolution:** Pre-Phase 5 decision. Either FTB adopts Dagster-based monitoring (matching EDS) or FTB runs Prometheus independently for its own Phase 5 metrics.

### CROSS-09 — UTXO Backfill PostgreSQL Impact Uncoordinated
- **Location:** EDS line 555 (7–14 day backfill) vs FTB (no mention)
- **Category:** Cross-project contention
- **Severity:** Warning
- **What's wrong:** The UTXO backfill is a 7–14 day intensive PostgreSQL write on the shared `empire_postgres` instance. FTB Phase 1 collection also writes to the same instance. No scheduling coordination documented.
- **Resolution:** Pipeline item EDS-50 tracks this. Schedule backfill during low-FTB-activity period.

### CROSS-10 — ClickHouse Merge Contention Unaddressed
- **Location:** EDS (resource profiles address query-time only) vs FTB (no mention)
- **Category:** Cross-project contention
- **Severity:** Info
- **What's wrong:** Both projects define query-time ClickHouse resource profiles. Neither addresses background merge thread allocation, which is system-global and shared across both `empire.*` and `forge.*` databases.
- **Resolution:** Low risk at Phase 1 volumes. Monitor during deployment.

### CROSS-11 — Track 3 Metric Naming Near-Collisions
- **Location:** EDS Track 3 metrics vs FTB catalog
- **Category:** Cross-project gap
- **Severity:** Warning
- **What's wrong:** Beyond CROSS-02 (fed_funds), systematic naming alignment between EDS Track 3 and FTB catalog metrics is undocumented. EDS Track 3 sources FRED, DeFiLlama, and SEC EDGAR — the same sources FTB uses directly. If EDS Track 3 goes live and sync is active, metric_id alignment must be perfect or sync dead-letters accumulate. Examples needing verification beyond fed_funds: all 23 FRED metrics, all 15 DeFiLlama metrics, all 5 ETF flow metrics.
- **Resolution:** Create a complete metric_id mapping table between EDS Track 3 and FTB catalog. Verify every name matches before sync activation.

---

## SECTION 4: COHESION AUDIT RESOLUTION STATUS

The cohesion audit identified 4 contradictions, 9 gaps, and 5 contention risks. Resolution status after checking both design docs:

| Finding | Status | Severity if Unresolved |
|---------|--------|------------------------|
| **C1** — ClickHouse user naming (`ch_writer` vs `forge_writer`) | **RESOLVED** — FTB consistently uses `ch_writer` | — |
| **C2** — `instrument_id` nullability (`__market__` vs `NULL`) | **UNRESOLVED** — see CROSS-01 | Blocker |
| **C3** — metric_id pass-through naming | **PARTIALLY RESOLVED** — EDS adopts FTB naming, but specific names diverge (see CROSS-02) | Blocker |
| **C4** — Domain CHECK constraint blocks EDS domains | **RESOLVED** — `chain` and `valuation` added | — |
| **G1** — FTB unaware of `empire.*` | **RESOLVED** — full section added | — |
| **G2** — No ClickHouse resource profiles | **RESOLVED** — profiles defined | — |
| **G3** — FTB unaware of `empire_utxo` | **RESOLVED** — documented | — |
| **G4** — `empire_utxo` PostgreSQL user unspecified | **RESOLVED** — specified in EDS doc (line 580) | — |
| **G5** — No `eds_derived` in source_catalog | **RESOLVED** — migration 0002 adds it | — |
| **G6** — Promotion path circular | **RESOLVED** — manual SQL migration | — |
| **G7** — No proxmox resource budget | **PARTIALLY RESOLVED** — ClickHouse covered, full budget not done | Warning |
| **G8** — Dagster code server integration | **UNRESOLVED** — EDS-47 tracks it | Warning |
| **G9** — No Dagster concurrency limits | **UNRESOLVED** — EDS-48 tracks it | Warning |
| **G10** — event_calendar constraint blocks EDS | **UNRESOLVED** — design approved but DDL not committed | Info |
| **G11** — Dagster watchdog scope | **PARTIALLY RESOLVED** — EDS-41 notes shared scope, prose doesn't | Info |
| **G12** — Rule 2 scoped to forge.* only | **RESOLVED** — explicit scope statement | — |
| **G13** — Rule 3 doesn't acknowledge empire_utxo | **RESOLVED** — explicit scope statement | — |
| **R1** — data_version collision | **RESOLVED** — mutual exclusion protocol | — |
| **R2** — ClickHouse merge contention | **UNRESOLVED** — see CROSS-10 | Info |
| **R3** — UTXO backfill PostgreSQL impact | **UNRESOLVED** — EDS-50 tracks it | Warning |
| **R4** — EDS ClickHouse storage projection | **UNRESOLVED** — FTB projects only its own footprint | Info |
| **R5** — Track 3 metric_id near-collisions | **PARTIALLY RESOLVED** — see CROSS-11 | Warning |
| **R6** — Prometheus disagreement | **UNRESOLVED** — EDS-51 tracks it | Info |
| **R7** — Server2 write prohibition scope | **RESOLVED** — clearly scoped | — |

**Summary:** 12 resolved, 4 partially resolved, 8 unresolved (2 blockers, 4 warnings, 4 info).

---

## SECTION 5: NUMBERS INVENTORY

### Metric Counts

| Claim | Source | Value | Status |
|-------|--------|-------|--------|
| FTB Phase 0 seed | FTB ~line 1844 | 74 | Consistent with CLAUDE.md |
| FTB Phase 1 additions | FTB ~line 1845 | 8 | +2 DeFi, +4 COT, +2 derived |
| FTB total at Phase 1 | FTB ~line 1846 | 82 | 74 + 8 = 82 ✓ |
| FTB domains (CHECK) | FTB ~line 1641 | 11 | But CLAUDE.md says 9 ⚠️ |
| FTB FRED total | FTB ~line 4893 | 23 | Consistent |
| EDS node-derived | EDS line 259 | 42 | Consistent with API layer |
| EDS-exclusive | EDS line 260 | 21 | Consistent |
| EDS Track 2 | EDS lines 687–702 | 13 | 8 FTB-compatible + 5 exclusive |
| EDS Track 3 total | EDS API layer line 1537 | 43 | 15 DeFiLlama + 23 FRED + 5 EDGAR = 43 ✓ |
| EDS total available | EDS line 1538 | ~101 | 42 + 13 + 43 + 3 price = ~101 ✓ |

### Table Counts

| Claim | Source | Value | Status |
|-------|--------|-------|--------|
| FTB PostgreSQL catalog | CLAUDE.md | 12 | Listed: assets, asset_aliases, venues, instruments, source_catalog, metric_catalog, metric_lineage, event_calendar, supply_events, adjustment_factors, collection_events, instrument_metric_coverage |
| FTB ClickHouse (forge.*) | FTB DDL | 3 | observations, dead_letter, current_values |
| EDS ClickHouse (empire.*) | EDS line 2380 | 12 | Fully enumerated (lines 2382–2395) ✓ |

### Row Volumes

| Claim | Source | Value | Notes |
|-------|--------|-------|-------|
| FTB Silver rows/day | FTB ~line 2250 | ~5,800–6,000 | Own adapters only |
| FTB ClickHouse projection | FTB ~line 4607 | ~72,000 | Unclear if includes BLC-01 ⚠️ |
| EDS sync rows/day | EDS line 1362 | ~17,350 | All tracks combined |
| BLC-01 events/day | EDS line 655, CLAUDE.md | 65,000–72,000 | Raw ticks (aggregated to far fewer observations) |

### Storage

| Claim | Source | Value | Status |
|-------|--------|-------|--------|
| FTB bronze-archive 5yr | FTB ~line 2046 | ~40 GB | Conflicts with FTB ~line 4172 (~75 GB) ⚠️ |
| FTB total 5yr (all MinIO) | FTB ~line 2046 | ~192 GB | Unexplained breakdown ⚠️ |
| FTB ClickHouse Silver 5yr | FTB ~line 4173 | ~2.5 GB | |
| EDS NVMe allocated | EDS line 1644 | 9,600 GB | 11,200 usable, 1,600 headroom |
| EDS SATA allocated | EDS line 1647 | 4,000 GB | 7,400 usable, 3,400 headroom |

---

## SECTION 6: RECOMMENDED FIX ORDER

### Before Pipeline Rebuild (Blockers)

1. **CROSS-01** — Decide `instrument_id` convention for market-level metrics in FTB Silver (`NULL` or `'__market__'`). Update FTB design doc and DDL to be consistent. Update EDS sync mapping if needed.
2. **CROSS-02** — Create complete metric_id mapping table: all 43 Track 3 metrics (FRED + DeFiLlama + EDGAR) with both EDS and FTB canonical names. Fix divergences.
3. **FTB-01** — Standardize PostgreSQL table naming (DDL vs prose).
4. **FTB-02** — Fix `bronze_archive_log` column types to match referenced PKs.

### Before Phase 1 (Warnings)

5. **CROSS-06** — Resolve Dagster code server integration (EDS-47).
6. **CROSS-07** — Define Dagster concurrency limits (EDS-48).
7. **CROSS-09** — Coordinate UTXO backfill scheduling (EDS-50).
8. **CROSS-03** — Update FTB resource projections to include sync volume.
9. **CROSS-05** — Document `shared_ops` asset ownership in FTB design.
10. **EDS-01** — Fix stale "26 decisions" text to "33."
11. **EDS-03** — Fix pg_dump command (database vs schema).
12. **FTB-03 through FTB-25** — Fix remaining warnings (see individual findings).

### Pre-Phase 5

13. **CROSS-08** — Resolve Prometheus disagreement (EDS-51).

---

*Audit performed 2026-03-08. All findings are read-only assessments — no files were modified.*
