# FromTheBridge — Design Index

**Date:** 2026-03-09
**Status:** Navigation only. `FromTheBridge_design_v4.0.md` is the single source of truth.
**Owner:** Stephen (architect, sole operator)

---

## DOCUMENT AUTHORITY

```
1. FromTheBridge_design_v4.0.md (SSOT — all layers, all threads, all session decisions)
2. design_index.md (navigation only — this file)
```

**Archived (historical only):**
- `Archived/FromTheBridge_design_v3.1.md` — superseded by v4.0
- `Archived/thread_*.md` — merged into v3.1, then v4.0
- `Archived/FINAL_AUDIT_REPORT.md` — findings absorbed into v4.0
- `Archived/eds_ftb_cohesion_audit.md` — resolutions absorbed into v4.0
- `Archived/thread_backfill_readiness.md` — findings absorbed into v4.0
- `Archived/V3_UPGRADE_HANDOFF.md` — v2→v3.1 upgrade record
- `Archived/V3_REVIEW_HANDOFF.md` — v3.1 review session handoff
- `Archived/V3.1_REVIEW_REPORT.md` — v3.1 automated review (findings absorbed into v4.0)
- `Archived/V3.1_REVIEW_HANDOFF_SESSION2.md` — v3.1 review session 2
- `Archived/V3.1_REVIEW_HANDOFF_SESSION3.md` — v3.1 review session 3

**Session plans (`docs/Historical/`):** Evidence trails. Not authority. All decisions
pulled into v4.0 before archival.

---

## PHASE READING MAP

All content is in `FromTheBridge_design_v4.0.md`. Section references below guide
which parts to load for each phase.

| Phase | Required sections in v4.0 |
|---|---|
| Phase 0 — Schema Foundation | Three Hard Rules · §Thread 4 (all catalog DDL, ClickHouse schema, PIT model) · §Cold-Start Sequence · §Phase 0 gate |
| Phase 1 — Data Collection | §Thread 5 (adapter contract, per-source specs, GE, BLC-01, migration plan) · §Thread 4 (source_catalog, metric_catalog, collection_events) · §Silver → Gold Export · §Solo Operator Operations · §Instrument Admission Framework · §ML Training Windows · §Phase 1 gate · §Disaster Recovery Objectives |
| Phase 2 — Feature Engineering | §Thread 3 (PIT, null states, computation order, breadth scores, feature catalog) · §Thread 4 (instrument_metric_coverage) · ADR-004 (DuckDB), ADR-006 (dbt + forge_compute) · §Cross-source metric candidates · §Phase 2 gate |
| Phase 3 — EDSx Signal | §Thread 2 (Regime Engine, Five-Pillar Framework, EDSx v2.2, Synthesis) · §Thread 3 (per-pillar feature requirements) · §Phase 3 gate · §Decision Gate DG-R1 |
| Phase 4 — ML Track (Shadow) | §Thread 2 (ML Track, Graduation Criteria, Synthesis) · §Thread 3 (ML feature requirements) · §ML Training Windows · §48h Preview Implementation Spec · §Phase 4 gate |
| Phase 5 — Signal Synthesis and Serving | §Thread 7 (API Surface, Redistribution, Performance History B3, Signal Snapshot Cache C3, SLA Definitions) · §Customer Identity Model · §DuckDB Concurrency Model · §Credential Inventory · §Phase 5 gate |
| Phase 6 — Productization | §Thread 7 (Methodology Documentation, First Customer Onboarding) · §Disaster Recovery (restore drill verification) · §Phase 6 gate |

---

*design_index.md — navigation only. Not authoritative on any decision.
Full specification in `FromTheBridge_design_v4.0.md`.*
