# Single Source of Truth Reconciliation — Handoff

**Date:** 2026-03-09
**Pipeline item:** LH-69 (critical, blocks Phase 1)
**Prerequisite reading:** This file. Do not start editing without reading fully.

---

## Principle

**`FromTheBridge_design_v3.1.md` is the single source of truth.** All other documents either reference it or are archived. Session plans are evidence trails, not authority. CLAUDE.md summarizes v3.1 for agent context but does not override it. Migration files implement what v3.1 specifies.

**Going-forward rules:**
1. Each fact lives in exactly one place
2. Design docs state criteria and rules, not counts derived from database state
3. Counts that appear must reference their source (a table, a query, a constraint)
4. Session decisions must be pulled into v3.1 before the session is considered complete
5. No satellite document may claim authority over any slice of the design

---

## 10 Resolved Contradictions

| # | Topic | Decision | What to change |
|---|-------|----------|----------------|
| 1 | Total metrics at Phase 1 | **83** (74 seed + 8 original + 1 NVT proxy). Replace hardcoded count with query reference where possible. Gold and MOVE are EDS-exclusive, not FTB. | v3.1: change "82" to "83" at line ~1869. CLAUDE.md: change "85" to "83" and fix breakdown. |
| 2 | Phase 1 metric additions | **9** (8 original + 1 NVT proxy). FRED Q4 series (BREAKEVEN, REAL_YIELD) were already in the 18-series expansion, not additions. | v3.1: change "8 metrics" to "9 metrics" and add NVT proxy to breakdown. |
| 3 | FRED expansion count | **18 additional** (unchanged). Gold and MOVE are EDS-exclusive. BREAKEVEN and REAL_YIELD are already in the 18. | No change to v3.1's "18 additional" — it was correct. Fix thread_backfill_readiness if it says otherwise (or archive it). |
| 4 | Phase 3/4 sequencing | **Parallel** after Phase 2 gate (approved T5 Q6). | v3.1: add explicit statement in Phase 3 and Phase 4 sections that both tracks run in parallel. |
| 5 | Phase 3 pillars | **All 5 non-null at gate.** Phase 2 quality gate is the protection against sparse data. | v3.1: update Phase 3 gate to require all 5 pillars producing non-null scores. Remove "2 live + 3 null-state" framing. CLAUDE.md: "all 5 pillars scoring" becomes correct. |
| 6 | Phase 0 gate criteria | **8 criteria** (v3.1 is correct). | CLAUDE.md: fix internal contradiction — remove "13 criteria" reference, say "8 gate criteria". |
| 7 | `instrument_id` in ClickHouse | **Nullable(String)** per v3.1. Market-level metrics use NULL. `__market__` exists in PostgreSQL catalog only. | Migration fix: change to `Nullable(String)`. Cohesion audit resolution holds. |
| 8 | PostgreSQL PKs | **TEXT** (human-readable canonical names as PKs). Simpler, fewer joins, matches ClickHouse strings. | v3.1: update DDL specs to use TEXT PKs instead of UUID. Remove `canonical_name` columns (the PK *is* the canonical name). This is the biggest DDL change. |
| 9 | `current_values` MV engine | **AggregatingMergeTree with argMaxState** per v3.1. | Migration fix: rebuild MV with correct engine. |
| 10 | ClickHouse credentials | **ch_writer / ch_export_reader / ch_ops_reader** per v3.1. | Migration fix: rename users. |

---

## Missing Sections to Pull Into v3.1

### From solo-ops design (2026-03-08) — new section needed

Pull into v3.1 as "§Solo Operator Operations" or similar:
- event_calendar schema extension: ALTER TABLE adding system_id, severity, metadata, expires_at, recurring_rule
- Expanded event_type CHECK constraint (14 values)
- calendar_writer role definition (EDS exception to "EDS never writes forge.*")
- risk_writer role definition (FTB INSERT to empire.risk_assessment)
- ftb_ops.adapter_health asset specification (5 monitored fields)
- ftb_ops.export_health asset specification (5 monitored fields)
- Failure mode taxonomy table (8 modes, dominant mode = silent data degradation)
- Alert routing: Red (immediate push), Yellow (daily digest), Green (silent)
- 5 red alert trigger conditions
- Phase 1 runbooks FTB-01 through FTB-08 (index only — full runbooks in docs/runbooks/)
- 6 Phase 1 gate additions for ops (ops assets healthy, runbooks written, credentials deployed)

Source file: `docs/plans/2026-03-08-solo-operator-ops-design.md`

### From instrument admission (2026-03-09) — expand existing section

v3.1 already has a Coverage Framing section that references the admission design. Expand it with:
- Core principle: "Admission criteria are canonical. Instrument lists are ephemeral."
- Full 6-criterion admission table with thresholds (180d depth, 90% completeness, Tiingo coverage, top-95% market cap, ≥10 metrics from ≥3 sources, no 7d staleness)
- Demotion criteria table (staleness >30d, completeness <80% for 30d, source loss)
- Capacity caps: collection (uncapped), scoring (uncapped), signal_eligible (≤200)
- USDC/USDT/WBTC are metric inputs, not instruments
- Auto-promote/auto-demote Dagster scheduled asset (weekly)
- Phase 0 seed: BTC, ETH, SOL at collection + __market__ at system — no instrument starts as signal_eligible
- Coinalyze backfills all instruments returned by API discovery (not a fixed list)

Source file: `docs/plans/2026-03-09-instrument-admission-design.md`

### From T5 patch (2026-03-07) — specific decisions

- NVT proxy: `macro.nvt_txcount_proxy` = market_cap / tx_count. Evidence-gated: revisit if ML F1 drop > 3%. Add metric entry with computation formula.
- Market cap sourcing: CoinMetrics `CapMrktCurUSD` for BTC/ETH historical; Tiingo price × CoinPaprika circulating supply forward; CoinGecko rejected (non-commercial ToS).
- Etherscan Pro deferred — free tier backfill first; exchange flows Priority 2.
- Token unlock data deferred from Phase 3 — accept null in v1.
- Polygon.io integration design as Phase 1 blocker (add to known gaps and Phase 1 prerequisites).
- Phase 3/4 parallel execution (add explicit statement).

Source file: `docs/plans/T5_patch_handoff.md`

### From T3b/backfill readiness — data quality findings

- Coinalyze effective derivatives data floor: Feb 2022 (pre-2022 OI at 3.4%)
- 450-day ML wait assumption eliminated — all models have sufficient historical depth
- Per-model training floor dates (Derivatives: 2022-02, Capital Flow: 2020-05, Macro: 2014-01, DeFi: 2020-05, Volatility: 2014-01)
- Backfill priority matrix (Priority 1 blocks ML, Priority 2 improves quality, Priority 3 hard floor)
- BLC-01 decision: Option B (hourly rsync of .complete files), landing dir /opt/empire/blc01/landing/
- 7 proposed Phase 1 gate additions with SQL verification queries (backfill_depth, Priority-1 verification, BLC-01 rsync, training window viability, wei fix, dead letter triage, HY OAS/Gold/MOVE)
- T3b data quality audit is an explicit Phase 1 prerequisite

Source files: `docs/plans/T3b_handoff.md`, `docs/design/thread_backfill_readiness.md`

### From EDS cross-source assessment (2026-03-09)

- 5 candidate cross-source metrics for Phase 2 review (funding_rate_dispersion, oi_migration, liquidation.concentration, flow_liquidation_lag, onchain_vs_exchange_volume_divergence)
- 8 EDS-exclusive metrics (FTB can derive in Marts if needed)
- Phase 2 gate: evaluate 5 candidates after EDS-59/60 live ≥7 days
- Promotion prerequisites: 5 conditions before any candidate enters forge.metric_catalog
- LH-70 pipeline item reference

Source file: `docs/plans/eds-cross-source-metrics-assessment.md`

---

## DDL Reconciliation

### Decision: TEXT PKs (keep deployed pattern, update v3.1 spec)

All PostgreSQL catalog tables use TEXT primary keys with human-readable canonical names. This is what's deployed. v3.1 DDL specs need updating from UUID to TEXT throughout.

Implications:
- Remove `canonical_name` columns from spec (PK *is* the canonical name)
- All FK references become TEXT
- `asset_aliases` needs redesign — currently a name-history table in migration, spec wants source-symbol lookup. Decide which model to keep.
- `metric_lineage` needs redesign — migration has 1:1 (one lineage per metric), spec has many-to-many (per metric×source for Dagster asset graph). **Spec's many-to-many is required for Dagster.** Migration needs fixing.

### Migration Blockers (must fix before Phase 1)

| Table/Object | Issue | Fix |
|---|---|---|
| `forge.current_values` | Wrong engine (ReplacingMergeTree) | Rebuild as AggregatingMergeTree with argMaxState |
| `forge.observations.value` | Non-nullable Float64 | Change to Nullable(Float64) |
| `forge.instruments` | Missing `canonical_symbol` column? | With TEXT PKs, the instrument_id IS the canonical symbol. Verify this works for CH resolution. |
| `forge.instruments.tier` CHECK | Missing 'system' value | Add 'system' for __market__ |
| `forge.collection_events` status CHECK | Missing 'running', 'completed' | Add values |
| `forge.metric_lineage` | Wrong structure (1:1 vs many:many) | Rebuild as many-to-many per spec |
| `forge.metric_catalog` | Missing 11 columns | Add: description, value_type, granularity, staleness_threshold, expected_range_low/high, is_nullable, methodology, status, deprecated_at, signal_pillar |
| All PG tables | Zero indexes | Add all indexes from spec |
| ClickHouse credentials | Wrong names | Recreate as ch_writer, ch_export_reader, ch_ops_reader |

### Note on TEXT PKs + instruments

With TEXT PKs, `instrument_id` in PostgreSQL IS the canonical string written to ClickHouse. No join needed to resolve names. The `canonical_symbol` column from the UUID spec becomes unnecessary — the PK serves that purpose. Verify this assumption holds for the adapter contract (thread_5) and Dagster asset graph construction.

---

## Documents to Archive After Reconciliation

| Document | Action | Prerequisite |
|----------|--------|-------------|
| `docs/design/thread_backfill_readiness.md` | Archive to `docs/design/Archived/` | Content pulled into v3.1 per above |
| `docs/design/FINAL_AUDIT_REPORT.md` | Archive | Extract FTB-23 (feature catalog storage), FTB-24 (Gold/Marts schema), CROSS-11 (Track 3 metric naming) as pipeline items first |
| `docs/design/eds_ftb_cohesion_audit.md` | Archive | Resolution table summary added to v3.1 EDS integration section |
| All `docs/plans/*.md` session plans | Archive to `docs/Historical/` | Decisions absorbed into v3.1 |
| `docs/design/V3*.md` review handoffs | Move to `docs/Historical/` | Already archival |
| `docs/design/design_index.md` | Strip to Phase Reading Map only | Remove Locked Decisions, Conventions, Sources Catalog, Known Gaps — all stale duplicates of v3.1 |

---

## CLAUDE.md Fixes

| Section | Fix |
|---------|-----|
| Design documents table | Replace thread file list with single row: "FromTheBridge_design_v3.1.md — All layers, all threads (canonical)" |
| Architecture 9-layer table | Keep but label "summary — see v3.1 for canonical" |
| CURRENT STATE metric count | Change to 83 (or reference metric_catalog query) |
| CURRENT STATE breakdown | Fix: "+9 Phase 1 additions (+8 original + 1 NVT proxy)" |
| Phase 0 gate reference | Change "13 criteria" to "8 gate criteria" |
| Phase 3 gate summary | "All 5 pillars scoring non-null" (now correct per decision #5) |
| Phase 5 name | "Signal Synthesis and Serving" |
| Data sources redistribution | Change `redistribution = false` to `redistribution_status = 'blocked'` |
| Legal compliance reference | Change from archived thread_7 to v3.1 |
| CURRENT STATE blockers | Add "Polygon.io integration design session required before Phase 1" |
| FRED source description | "23 macro series" is correct at Phase 1 completion; add "(5 Phase 0, 18 Phase 1 expansion)" |
| BLC-01 events/day | Change to "volume varies with market conditions" or drop count |

---

## Cohesion Audit Re-verification Needed

After v3.1 reconciliation, re-check these cohesion audit resolutions:

| Finding | Why re-check |
|---------|-------------|
| C1 (CH user naming) | v3.1 now canonical; migration will be fixed to match |
| C2 (instrument_id nullability) | Confirmed: Nullable(String) in v3.1, migration will be fixed |
| C4 (domain CHECK) | v3.1 added 'chain' and 'valuation' domains — verify these are in v3.1 DDL |
| R1 (data_version collision) | Mutual exclusion protocol — verify still in v3.1 after edits |

Also verify: `empire_to_forge_sync` column mapping still correct after TEXT PK decision (it should be — sync writes string values to ClickHouse, which is what TEXT PKs are).

---

## Execution Order for Fresh Thread

**The output is v4.0, not v3.2.** The scope of changes (DDL rewrite, 6 sessions absorbed, new sections, authority model change) justifies a major version. v3.1 → v4.0 signals this is the SSOT consolidation, not a patch. Copy v3.1 to v4.0 and work from there. v3.1 is archived as historical.

1. Copy `FromTheBridge_design_v3.1.md` → `FromTheBridge_design_v4.0.md`
2. Resolve DDL: update all table specs from UUID to TEXT PKs, add missing columns, fix metric_lineage structure
3. Pull missing sections into v4.0 (solo-ops, instrument admission, T5 decisions, T3b findings, cross-source assessment)
4. Fix hardcoded counts (metric count → 83, add NVT proxy to additions, Phase 3/4 parallel statement, Phase 3 gate all 5 non-null)
5. Add SSOT authority statement at top of v4.0 — this document is the single source of truth, all other documents reference it
6. Strip design_index.md to navigation only, update to point at v4.0
7. Fix CLAUDE.md (per table above), update all references from v3.1 to v4.0
8. Archive satellite documents + move v3.1 to Archived/
9. Write corrective migration (0004_phase0_corrective.sql for PG, 0002 for CH) — or single canonical rewrite
10. Re-verify cohesion audit resolutions against v4.0
11. Update pipeline items (LH-01 remove "121 instruments", LH-59 parametrize row count)
12. Update memory files

---

## EDS Equivalent Audit

This same process needs to happen for `EDS_design_v1.1.md` in the Nexus-Council repo. Scope:
- Inventory all EDS satellite documents
- Check for decisions made in sessions that didn't propagate to EDS design
- Verify EDS pipeline items don't carry stale assumptions
- Cross-check shared boundary items against reconciled FTB v3.1
- Verify cohesion audit resolutions hold after FTB reconciliation

This should be a separate session after FTB reconciliation is complete.

---

## DB Access (for verification during execution)

- Forge catalog: `ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured -c \"SQL\""`
- Pipeline items: same, schema `bridge`, table `pipeline_items`
- ClickHouse: `ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --query \"SQL\""`
- Metric count verification: `SELECT COUNT(*) FROM forge.metric_catalog;` (expect 74)
- Source count verification: `SELECT COUNT(*) FROM forge.source_catalog;` (expect 10)
