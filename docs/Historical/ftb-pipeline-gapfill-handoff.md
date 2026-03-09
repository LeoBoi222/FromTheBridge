# FTB Pipeline Gap-Fill — Session Handoff

**Date:** 2026-03-08
**Prior session:** EDS pipeline reconciliation (in EmpireDataServices repo)
**Design doc:** `FromTheBridge/docs/design/FromTheBridge_design_v3.1.md`
**Reconciliation report:** `EmpireDataServices/docs/plans/pipeline-reconciliation-report.md`

---

## TASK: Fill FTB Pipeline Gaps (Phases 0, 2, 3, 5)

Cross-referencing the FTB design doc v3.1 against `bridge.pipeline_items` revealed that FTB Phases 0, 2, 3, and 5 are under-tracked or misaligned. The EDS reconciliation SQL should be executed FIRST (see `EmpireDataServices/docs/plans/pipeline-execution-handoff.md`).

### Prerequisites

- EDS pipeline reconciliation SQL has been executed (31 stale W-*/REM-*/B-* items archived)
- Both design docs are final: `EDS_design_v1.1.md` (v1.1.2) and `FromTheBridge_design_v3.1.md`

---

## Gap 1: Phase 0 — Verification Only

Phase 0 was already implemented. No new pipeline items needed.

**Action:** Run verification queries to confirm current DB state matches Phase 0 gate criteria:

```sql
-- Metric catalog: all Thread 3 metrics seeded?
SELECT count(*) FROM forge.metric_catalog;

-- Source catalog: all 11 sources with correct redistribution flags?
SELECT source_id, redistribution_status FROM forge.source_catalog ORDER BY source_id;
-- Expected: SoSoValue + CoinMetrics = 'blocked'; Coinalyze/BGeometrics/Etherscan/BLC-01 = 'pending'; rest = 'allowed'

-- Instrument universe seeded?
SELECT count(*) FROM forge.instruments;
```

Check if v3.1 added any metrics/sources that weren't in the original Phase 0 seed. If so, create a single LH-* item for the seed update.

---

## Gap 2: Phase 2 — Misaligned Items + Missing Coverage

**Problem:** 3 items tagged `ftb_p2` don't belong there:
- **LH-21** (Forge Tier 3 composite metrics) — composites are Phase 3/5, not Phase 2 feature engineering
- **LH-23** (GPU compute approximate on-chain) — infrastructure, not feature engineering
- **LH-24** (GPU compute true UTXO) — EDS Track 1 work, not FTB feature engineering

**Action 1:** Reassign LH-21/23/24 to correct phases (or backlog if no clear phase).

**Action 2:** Create ~5 new LH-* items for Phase 2 actual scope (FTB design §Phase 2, 6 steps, 11 gate criteria):

| Proposed Item | Scope | Design Doc Reference |
|---------------|-------|---------------------|
| Feature catalog population | All Thread 3 features cataloged before code | Phase 2 Step 1 |
| Categories A-C feature implementation | Rolling stats (27), cross-sectional ranks (4), ratio/interaction (7) | Phase 2 Step 3, Thread 3 §Categories A-C |
| Categories D-G feature implementation | Regime labels (7), calendar (6), breadth (8), cross-asset (5) | Phase 2 Step 3, Thread 3 §Categories D-G |
| PIT constraint audit + dbt models | Zero look-ahead bias verified; all dbt models pass | Phase 2 Steps 5-6, gate criteria |
| Historical feature matrix generation | DuckDB against Gold Iceberg; full history | Phase 2 Step 6, gate criterion |

---

## Gap 3: Phase 3 — Zero Items

**Problem:** No LH-* items exist for Phase 3 (Signal Generation — EDSx). The REM-* items that previously covered this are being archived as pre-design-doc artifacts.

**Action:** Create ~5-6 new LH-* items for Phase 3 (FTB design §Phase 3, 7 steps, 10 gate criteria):

| Proposed Item | Scope | Design Doc Reference |
|---------------|-------|---------------------|
| Pillar rule set methodology documents | Written BEFORE code for all live pillars | Phase 3 Step 1, gate criterion |
| Pillar scoring implementation (trend_structure + liquidity_flow) | 2 live pillars producing scores | Phase 3 Step 2 |
| Regime classifier implementation | H2 Volatility-Liquidity Anchor rule-based baseline | Phase 3 Step 4 |
| marts.signals_history Dagster SDA | Python asset (not dbt), writes to Gold Iceberg | Phase 3 gate criterion |
| Composite formation + regime-adjusted weights | Threshold calibration, F1 maximization | Phase 3 Steps 3, 6 |
| Phase 3 backtesting + output schema verification | >52% directional accuracy OOS, schema validates against §L2.8 | Phase 3 Steps 5-7, gate criteria |

---

## Gap 4: Phase 5 — Severely Under-Tracked

**Problem:** Phase 5 has 6 tracks, 25 work items, 49 gate criteria. Only 2 pipeline items exist (LH-20, LH-22).

**Action:** Create ~10-15 new LH-* items organized by track:

| Track | Proposed Items | Design Doc Reference |
|-------|---------------|---------------------|
| **A — Entitlement DDL** | Deploy entitlement schema (12 tables), bootstrap audit partitions, seed plan data | Phase 5 Track A (3 items) |
| **B — Dagster Assets** | forge_redistribution_refresh + audit_partition_creator | Phase 5 Track B (2 items) |
| **C — FastAPI Serving** | LH-20 covers this partially. May need: synthesis logic (§L2.1-L2.8), EntitlementMiddleware, webhook delivery | Phase 5 Track C (5 items) |
| **D — Test Suite** | T0-1 through T0-4 (gate-blocking), Tier 1+2 tests | Phase 5 Track D (2 items) |
| **E — Performance History** | signal_outcomes dbt model, performance_metrics model, performance API endpoint, ingested_at audit | Phase 5 Track E (5 items) |
| **F — Signal Snapshot Cache** | SignalCache + snapshot_writer + warm start + redistribution filter + Prometheus metrics | Phase 5 Track F (9 items) |
| **F1 — Customer Deliverables** | First API key, pricing page, API docs, performance summary, methodology doc | Phase 5 F1 |

**Note:** Some of these may be better as sub-items of LH-20 rather than individual pipeline items. Use judgment on granularity.

---

## Gap 5: Calendar Schema — CLOSED

LH-27 already covers this: "Extend forge.event_calendar with system_id, severity, metadata, expires_at, recurring_rule columns. Expand event_type CHECK for ops events."

EDS-31 depends on LH-27. Both items exist. No action needed.

---

## DB access

Same as EDS:
- Host: `192.168.68.11` (proxmox, SSH as root, key auth)
- Container: `empire_postgres`
- User: `crypto_user`
- Database: `crypto_structured`
- Schema: `bridge`
- Table: `pipeline_items`

---

## Process

1. Read `FromTheBridge_design_v3.1.md` Phase 2, 3, 5 sections
2. For each gap: draft LH-* items with title, description, phase_gate, tier, dependencies
3. Present to Stephen for review — do NOT execute SQL without approval
4. After approval, INSERT new items and UPDATE misaligned items
5. Run verification counts per phase
