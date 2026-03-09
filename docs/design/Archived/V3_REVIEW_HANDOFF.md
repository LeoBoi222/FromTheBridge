# v3.1 Design Document Review — Handoff

**Date:** 2026-03-06
**Status:** Ready for review
**Document:** `docs/design/FromTheBridge_design_v3.1.md` (4,142 lines)
**Purpose:** Systematic review to find holes, contradictions, and ambiguities before this document is used to generate CC implementation prompts.

## Context

The v3.1 document was produced by applying 9 design synthesis results (from `FromTheBridge_v3_consolidated_plan.md`) to the v2 base document. Each result was applied in sequence across 3 conversation sessions. The upgrade process is documented in `V3_UPGRADE_HANDOFF.md` (now marked complete).

**Risk:** Because results were applied sequentially, later results may have introduced inconsistencies with earlier ones. Additionally, some v2 language may have survived unchanged when it should have been updated by a result.

## Review Scope

### 1. Internal Contradictions

Cross-reference these known areas where multiple results touched the same concept:

| Concept | Touched By | Risk |
|---|---|---|
| Source count ("10 sources" vs "11 sources") | E3/E4 changed 10→11. Check ALL references. | Stale "10" may remain |
| Tier model (Free/Paid vs 4-tier) | D2 introduced 4-tier. F1 added pricing. B3 added field-tier mapping. | Old "Free/Paid" binary language may survive |
| Redistribution enforcement | A2 introduced three-state enum. D2 had `redistribution_blocked_metrics`. A2 renamed to `metric_redistribution_tags`. | Check for stale D2 naming |
| MinIO buckets | v2 had `bronze` and `gold`. C2 split bronze into `bronze-hot` + `bronze-archive`. | Stale `bronze` (singular) references may remain |
| Export cadence | v2 said "every 6h". C1 changed to event-triggered hybrid. | Stale "6h" references may remain |
| Dagster asset count | E3/E4 changed ~53→~65. Verify consistency. | Multiple references to asset count |
| Phase 5 gate criteria | D1, D2, B3, C3 all added criteria. | Verify no duplicates, no conflicts |
| Phase 1 gate criteria | C1, E3/E4, C2 all added criteria. | Same risk |

### 2. Specification Gaps

Things that a CC prompt writer would need but may not be fully specified:

- **`forge.bronze_archive_log` DDL** — C2 provides the CREATE TABLE but does it appear in the migration file list?
- **`signal_snapshot_writer` Dagster asset** — referenced in C3 build steps but is the asset dependency graph fully specified?
- **`/internal/cache/refresh` endpoint** — C3 specifies it but is it in the API endpoints list? (It shouldn't be — it's internal. But verify no confusion.)
- **`/healthz/ready` endpoint** — same question. Is it in the Docker health check spec?
- **`bronze_cold_archive` and `bronze_expiry_audit` Dagster assets** — are they in the Dagster asset count?
- **Performance marts (`signal_outcomes`, `performance_metrics`)** — B3 specifies them in Thread 7. Thread 3 references them. Are they in the dbt model list / Dagster asset graph?
- **CFTC COT adapter** — E3/E4 added it. Is it fully specified in the per-source adapter section?
- **48h-delayed public preview** — F1 references it. Is there an endpoint spec? Is it a separate endpoint or a public route on the website?
- **Methodology documentation sections §3.1–§4.5** — B3 maps performance metrics to these sections. Are all 8 methodology sections consistent with this mapping?

### 3. Ambiguities That Would Force Assumptions

- What is the exact Dagster asset name for the Silver→Gold export? (C1 specifies the trigger model but does it name the asset?)
- `forge_compute` — is this a Docker service? A Python package? Both? Where does it run?
- The `collection_events` table — is `observations_written` a column? Is the full DDL in the document?
- Rate limit backend: D2 says PostgreSQL sliding window against `audit_access_log`. Is this the same as `audit_access_log` partitioned table in ADR-007? Naming consistency?
- `plan_field_access` vs the B3 field-tier mapping — are these the same access control? Or does B3 add a separate performance-specific field gate?

### 4. Stale References Check

Grep for these patterns that should no longer appear (or should appear only in specific historical/context sections):

- `"10 sources"` — should be 11 (post-E3/E4)
- `"every 6h"` or `"6h export"` — should reference event-triggered hybrid (post-C1)
- `"Free/Paid"` or `"free/paid binary"` — should be 4-tier (post-D2), except in "supersedes" context
- `"redistribution_blocked_metrics"` — should be `metric_redistribution_tags` (post-A2), except in ADR-007 historical reference
- `"bronze"` as a single bucket — should be `bronze-hot` (post-C2), except in partition path references
- `"Forge DB"` or `"empire_forge_db"` — should only appear in migration/decommission context
- `"~53 assets"` — should be ~65 (post-E3/E4)

### 5. Gate Coverage Analysis

For each major design decision, verify there is a corresponding gate criterion that would catch an incorrect implementation:

| Decision | Expected Gate | Phase |
|---|---|---|
| Rule 2 (ClickHouse write-only) | Credential isolation test | Phase 1 + Phase 5 |
| Rule 3 (no time series in PG) | Schema audit | Phase 0 |
| PIT correctness | `ingested_at ≤ computation_timestamp` audit | Phase 2 |
| Redistribution three-state enum | T0 tests | Phase 5 |
| Signal snapshot cache p95 <50ms | Latency gate | Phase 5 |
| Performance endpoint p95 ≤500ms | Latency gate | Phase 5 |
| Bronze archive 88-day safety | Expiry audit asset | Phase 1 |
| Customer API key argon2id hashing | ? | Phase 5 |
| Annual rotation calendar entry | ? | Phase 6 |
| Neutral threshold ±0.10 fixed | ? | Phase 5 |

### 6. Cross-Thread Consistency

| Check | Threads | What to verify |
|---|---|---|
| Metric catalog count | Thread 4 seed data vs Thread 5 adapter specs vs CLAUDE.md | All say same number (was 74, E3/E4 added 7 = should be ~81) |
| Instrument count | Thread 4 vs Thread 7 vs B3 | All say 121 |
| Feature count | Thread 3 feature tables vs Thread 6 Phase 2 gate | Consistent |
| API endpoint list | Thread 7 endpoint specs vs Thread 7 Decisions Locked vs Consolidated Locked | All list same endpoints |
| Entitlement table count | D2 (12 tables) vs ADR-007 vs Phase 5 gate | Consistent count |
| dbt model list | Thread 3 (features) + B3 (performance marts) vs Thread 6 Phase 2/5 | All models accounted for |

## Process

1. Read `FromTheBridge_design_v3.1.md` end-to-end (use parallel subagents for different sections)
2. Run stale reference greps (Section 4)
3. Build contradiction/gap/ambiguity report
4. Rank findings by severity:
   - **Critical:** Would produce incorrect CC prompt or broken implementation
   - **High:** Ambiguity that forces assumption during implementation
   - **Medium:** Inconsistency that causes confusion but wouldn't break build
   - **Low:** Cosmetic or stylistic issue
5. Propose specific fixes for Critical and High items
6. Present report for architect review before applying fixes

## Reference Files

| File | Purpose |
|---|---|
| `docs/design/FromTheBridge_design_v3.1.md` | The document under review |
| `docs/design/FromTheBridge_v3_consolidated_plan.md` | Source of truth for what each result specified |
| `docs/design/V3_UPGRADE_HANDOFF.md` | Record of what was applied and where |
| `docs/design/FromTheBridge_design_v2.md` | Original baseline (unchanged, for comparison) |
| `docs/design/RESULT_ID_*.docx` | Individual result source files (9 total) |
| `CLAUDE.md` | Project rules — verify v3.1 doesn't contradict |

## Notes

- The v3.1 document will be used to generate Phase 1–6 CC implementation prompts. Every ambiguity in the design becomes a forced assumption in the implementation.
- The consolidated plan (`FromTheBridge_v3_consolidated_plan.md`) Section 3 contains the full consolidated gate criteria for all phases — cross-reference against what actually appears in v3.1 Thread 6.
- CLAUDE.md references "74 metrics" and "14 sources" — these may need updating after the review identifies the correct counts.
