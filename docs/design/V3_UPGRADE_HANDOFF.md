# v3.1 Design Document Upgrade — Handoff

**Date:** 2026-03-06
**Status:** Complete. All 9 results applied.

## Task

Apply the 9 design synthesis results from `FromTheBridge_v3_consolidated_plan.md` to the v2 design document, producing `FromTheBridge_design_v3.1.md`.

**Source:** `docs/design/FromTheBridge_design_v2.md` (2526 lines, unchanged)
**Target:** `docs/design/FromTheBridge_design_v3.1.md` (all 9 results applied: B1 + C1 + E3/E4 + D2 + A2 + D1 + B3 + F1 + C2/C3)
**Plan:** `docs/design/FromTheBridge_v3_consolidated_plan.md` (describes all 9 results)

## Completed

### 1. B1 — Layer 2 Synthesis Algorithm ✅
- **Source file:** `RESULT_ID_B1_Layer2_Synthesis.md`
- **Changes applied:**
  - Replaced "Signal Synthesis (Layer 2 — Future Design Session)" placeholder (~50 lines) with full §L2.1–L2.8 algorithm specification (~300 lines)
  - Replaced `/v1/signals/{instrument_id}` JSON example with §L2.8 schema reference
  - Updated 4 "Decisions Locked" references from "future session" to "designed and locked"
  - Resolved Layer 2 gap in Known Gaps section
  - Updated Phase 5 steps to reference §L2.1–L2.8

### 2. C1 — Silver→Gold Hybrid Export ✅
- **Source file:** `RESULT_ID_C1_SILVERGOLD_EXPORT_SLARESOLUTION.docx`
- **Changes applied:**
  - Replaced "every 6h" in Layer 5 summary (line 102) with event-triggered + fallback reference
  - Updated Layer 4 summary (line 109) from "6h export job" to event-triggered description
  - Rewrote Rule 2 section with hybrid trigger detail, `ch_export_reader` credential, and `SELECT ... FINAL` explanation
  - Updated ClickHouse Database Engine Summary with FINAL + 3-min lag floor detail
  - Updated ADR-001 key constraint text with watermark delta and cross-reference to §Silver→Gold Export
  - Added full §Silver→Gold Export section (~100 lines) after Adapter Contract: trigger model, query pattern, watermark, partition overwrite, anomaly guard, SLA verification table (44min worst-case), cold start, failure modes, volume projections, monthly maintenance
  - Added 4 export benchmark criteria to Phase 1 hard gate table (round-trip, 50k FINAL, 500k FINAL, baseline documentation)
  - Fixed Known Infrastructure Gaps: export asset resolution trigger changed from "Phase 2" to "Phase 1"
  - Added resolved gap entry to Known Gaps table

## Remaining (in order)

### 3. E3+E4 — DeFiLlama Yields + CFTC COT ✅
- **Source file:** `RESULT_ID_E3_E4_DefiLlamaYieldsAPI.md` (E3 only; E4 from consolidated plan §2)
- **Changes applied:**
  - Added 3 new DeFi lending metrics to metric catalog list: `supply_apy`, `borrow_apy`, `reward_apy`
  - Updated `utilization_rate` note: proxy retired Phase 1, replaced by direct DeFiLlama `/yields`
  - Added `reward_apy` signal_eligible=false note
  - Added 4 CFTC COT metrics to new macro.cot domain block with full note (Socrata API, Tuesday as-of date, publication lag, instruments, signal relevance)
  - Added 4 new lending features to DeFi feature table: utilization zscore (52w), utilization momentum (4w), supply/borrow APY values, borrow-supply spread
  - Added 5 CFTC COT features to macro feature table
  - Rewrote DeFiLlama adapter section: 4 collection jobs (added yields), added yields field mapping table, pool scope, Dagster asset fan-out, validation rules (null borrow_apy vs null utilization_rate)
  - Added full CFTC COT adapter section after FRED
  - Resolved utilization_rate gap in Source Gap Analysis table
  - Updated Sources Catalog: added CFTC COT row, updated DeFiLlama description, count 10→11
  - Updated Layer 0 source list: added CFTC COT, count 10→11
  - Updated all "10 sources" references to "11 sources" (Phase 0 seed, Phase 6 ToS audit)
  - Updated "10 collection asset keys" to "11" in multi_asset_sensor references
  - Updated Dagster asset count ~53→~65 at Phase 1 launch (+4 yields + ~8 CFTC)
  - Added E3+E4 Phase 1 steps (items 4–5: yields adapter extension, CFTC adapter build)
  - Added 4 E3+E4 gate criteria to Phase 1 hard gate: PF-6 utilization unit, DeFiLlama yields Silver, CFTC COT Silver, dead letter nullability tests
  - Resolved utilization_rate gap in Known Gaps table

### 4. D2 — Entitlement & Tenant Model ✅
- **Source file:** `RESULT_ID_D2_ENTITLEMENT_TENANTMODEL.docx`
- **Changes applied:**
  - Replaced Free/Paid binary with 4-tier plan matrix table (Free/Pro/Protocol/Institutional)
  - Added key format, rate limit backend, concurrent limit, entitlement middleware (12-step chain), bundle cache, redistribution blocked set specs
  - Updated API endpoints from "Paid Tier" to "Pro Tier (and above)"
  - Added 6 entitlement error codes, 12 entitlement tables to ADR-007
  - Rewrote Phase 5 steps as 4 tracks, added 8 D2 gate criteria
  - Updated Consolidated Locked Decisions and Thread 7 Decisions Locked tables
  - Updated onboarding step 5, "What is not in v1" line

### 5. A2 — Redistribution Enforcement ✅
- **Source file:** `RESULT_ID_A2_REDISTRIBUTION_ENFORCEMENT.md`
- **Changes applied:**
  - Expanded redistribution enforcement paragraph in tier matrix section: three-state enum, Option C propagation reference, null-with-flag (replacing D2's "omitted" language), v1 source status table (all 11 sources)
  - Replaced redistribution blocked set description with `metric_redistribution_tags` cache design + pg_notify invalidation
  - Added full §Redistribution Enforcement section (~160 lines) after Entitlement Middleware: three-state enum table, Option C propagation rule with `propagate_restriction`, `source_catalog` ALTER TABLE DDL, `metric_redistribution_tags` CREATE TABLE DDL, tag computation function description, null-with-flag response schema with JSON examples, `_redistribution_notice` top-level block format, composite score degradation rules (4 priority cases), operator resolution path (SQL templates for pending→allowed and relax-propagation-only), 5 audit evidence query pattern descriptions, 7 open assumptions requiring architect confirmation
  - Updated `source_catalog` DDL: added `redistribution_status`, `propagate_restriction`, `redistribution_notes`, `redistribution_audited_at` columns
  - Updated Thread 5 Decisions Locked: redistribution entry now references three-state enum, Option C, `metric_redistribution_tags`, A2 spec
  - Updated Phase 0 gate redistribution criterion: now requires correct `redistribution_status` values per source (not just boolean `redistribution = false`)
  - Updated ADR-007: replaced `redistribution_blocked_metrics` with `metric_redistribution_tags` (A2 authoritative naming)
  - Updated Known Gaps: added resolved A2 entry, expanded source-specific redistribution gaps with propagation behavior notes

### 6. D1 — Security Posture Baseline ✅
- **Source file:** `RESULT_ID_D1_SECURITY_POSTURE_BASELINE.md`
- **Changes applied:**
  - Updated Docker Services network section: ports 3010 and 9002 not published to host, SSH port forward for Dagster, mc CLI for MinIO, Phase 6 Cloudflare upgrade path
  - Added 8 new Infrastructure subsections (~160 lines total):
    - §Secrets Management: directory structure, file permissions, .env scope, Docker injection pattern, `read_secret()` utility, `crypto_user` isolation
    - §Credential Isolation: ClickHouse user matrix (ch_writer/ch_export_reader/ch_admin), MinIO service account matrix (bronze_writer/gold_reader/export_writer), critical isolation enforcement summary
    - §Customer API Key Lifecycle: argon2id hashing, key_prefix (12 chars), 1Password delivery, key format (240 bits), verification flow, rotation
    - §Encryption Posture: in-transit (TLS 1.3 at Cloudflare, inter-container unencrypted), at-rest (unencrypted v1, GPG for NAS backups)
    - §Rotation Policy: annual March window, first rotation March 2027, runbook scope
    - §Incident Response Playbooks: 4 scenarios (customer key, external key, Cloudflare token, PostgreSQL forge_user) with severity and containment targets
    - §Cloudflare Zero Trust: 5-route map with protection levels
    - §Phase 0 Security Corrective Actions: SEC-01 through SEC-06 checklist
  - Added 3 D1 security gate criteria to Phase 5 hard gate (secrets initialized, credential isolation verified, file permissions)
  - Added 3 D1 security gate criteria to Phase 6 hard gate (annual rotation calendar, secrets runbook, production isolation verified)
  - Added D1 security posture decision to Thread 6 Decisions Locked table
  - Added resolved D1 entry to Known Gaps table

### 7. B3 — Performance History Endpoint ✅
- **Source file:** `RESULT_ID_B3_PERFORMANCE_HISTORY_INPUT.docx`
- **Changes applied:**
  - Added full §Performance History Endpoint section (~250 lines) in Thread 7 after `/v1/health`: design principles, REST contract (6 parameters), response schema (meta block + 7 performance blocks), null contract, 12-metric definition table with PIT verification, computation architecture (signal_outcomes mart, performance_metrics mart, Dagster dependency graph), PIT compliance verification (JOIN SQL, outcome resolution rule, backfill guard), cross-sectional performance (equal-weight, sparse handling, cross-sectional Sharpe), field-tier mapping table (4 tiers × 10 fields), methodology documentation mapping table (16 metrics → doc sections)
  - Added `GET /v1/signals/performance` row to Latency SLAs table (p50: 200ms, p95: 500ms, p99: 1000ms)
  - Updated Methodology Documentation section: added §3.1–§3.5 and §4.1–§4.5 mapping reference to B3 performance fields, "must be drafted before Phase 6 gate"
  - Added Performance Marts → Layer 6 (B3) subsection in Thread 3 Data Requirements: `signal_outcomes` and `performance_metrics` mart descriptions with PIT discipline reference
  - Added Track E (B3 Performance History) to Phase 5 steps: 5 new build steps (ingested_at audit, signal_outcomes, performance_metrics, endpoint implementation, entitlement integration)
  - Added 8 B3 gate criteria to Phase 5 hard gate (ingested_at audit, PIT JOIN, metrics materialised, null contract, cross-sectional, entitlement tiers, reliability diagram latency, endpoint SLA)
  - Added 8 B3 locked decisions to Thread 7 Decisions Locked table (endpoint, PIT rules, neutral threshold, Sharpe, return definition, cross-sectional, reliability diagram, window=all, pillar attribution)
  - Added 9 B3 locked decisions to Consolidated Locked Decisions — Output Delivery section
  - Added resolved B3 entry to Known Gaps table

### 8. F1 — Customer Acquisition Plan ✅
- **Source file:** `RESULT_ID_F1_CUSTOMER_ACQUISITION_PLAN.docx`
- **Changes applied:**
  - Expanded Thread 1 Revenue Streams: replaced Stream 1 placeholder with 4-tier pricing table (Free Preview/$0, Pro/$199, Protocol/$4,500–$18,000, Institutional/$2,500), annual plan, OPEX ($85/month), 3-milestone break-even table
  - Expanded Stream 3 with protocol priority targets, engagement pricing table (founding/standard/retainer/quarterly), payment terms
  - Added full §Customer Profiles and Acquisition (F1) section in Thread 1: Profile A definition + acquisition motion, Profile B definition + sequencing, 48h-delayed public preview spec, pre-launch content strategy (PIT post, GitHub repo, 90-day sprint), 16-week sprint timeline table
  - Added 12 F1 locked decisions to Thread 1 Decisions Locked table (pricing for all 3 tiers, Stripe, Profile A/B acquisition, public preview, primary content asset, GTM sprint, Pro tier timing)
  - Rewrote Thread 7 Product Surface: updated v1 customer description to Profile A (F1), added Profile B description, retained "What is not in v1" with Stripe trigger
  - Rewrote Thread 7 First Customer Onboarding as dual-track: Profile A (Pro, 9-step self-serve/light-touch) and Profile B (Protocol, 5-step relationship-driven), plus Institutional note
  - Added Stripe/billing gap to Known Gaps table (trigger: 20+ subscribers)
  - Added 9 F1 decisions to Consolidated Locked Decisions — Revenue & Product

### 9. C2+C3 — Bronze Cold Archive + Signal Snapshot Cache ✅
- **Source file:** `RESULT_ID_C2_C3_BRONZE_COLD_SINGLESNAP.docx`
- **Changes applied:**
  - Rewrote Thread 5 Layer 1 Bronze section: two-bucket architecture table (`bronze-hot`/`bronze-archive`), credential isolation, archive job spec (daily 02:00 UTC, 2-day lag, 88-day safety), idempotency via `forge.bronze_archive_log`, reprocessing path (8 steps), storage projections
  - Added full §Signal Snapshot Cache (C3) section in Thread 7: in-process dict, Option C entitlement, Dagster POST trigger, warm start <5s, staleness behavior (9h TTL), response envelope JSON, cache-hit latency targets table
  - Updated Cold-Start Sequence step 4: `bronze-hot` + `bronze-archive` buckets, service account setup, lifecycle policy
  - Updated MinIO credential isolation table: added `bronze_archive_writer` account (C2)
  - Added 7 C2 gate criteria to Phase 1 hard gate
  - Added Track F (C3, 9 build steps) to Phase 5 steps
  - Added 11 C3 gate criteria to Phase 5 hard gate
  - Added 2 entries to Known Infrastructure Gaps (C2 Phase 1, C3 Phase 5)
  - Added 2 resolved entries to Known Gaps table
  - Added 7 C2+C3 decisions to Consolidated Locked Decisions — Infrastructure

## Process

For each remaining result:
1. Read the source result file (in `docs/design/`)
2. Read the corresponding sections in `FromTheBridge_design_v3.1.md`
3. Apply the changes described in the consolidated plan
4. Confirm with user before moving to the next result

## Notes

- Some source files are .docx — use the Read tool which handles them
- The v3.1 file already has B1 changes applied — do NOT re-apply
- The header has been updated to "v3.1" with "Upgraded from: v2.0"
- Preserve all existing content that isn't explicitly changed by a result
- The consolidated plan §2 describes what each result changes; the result files contain the full detail
