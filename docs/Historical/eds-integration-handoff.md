# FromTheBridge — EDS Integration & Solo Operator Handoff

**Date:** 2026-03-08
**Source session:** EDS design review synthesis (Session 3)
**Author:** Claude (EDS session), for pickup by Claude (FTB session)
**Priority:** Two independent work items, both high value

---

## CONTEXT

EDS v1.1 design is complete and build-ready. During review synthesis, two gaps were
identified that require work in the FromTheBridge repo:

1. **FTB Calendar Integration** — EDS needs to write operational events to FTB's
   existing calendar. The calendar exists but is unused with stale data.
2. **FTB Solo Operator Evaluation** — The same operational sustainability problem
   solved for EDS (observability, risk board, maintenance calendar, runbooks, alert
   routing) applies to FTB. Stephen explicitly requested this evaluation.

These are separate conversations. Item 1 is a focused integration task. Item 2 is
a design-level evaluation comparable to what produced EDS's Solo Operator Operations
section.

---

## ITEM 1: FTB CALENDAR INTEGRATION FOR EDS

### What EDS Needs

EDS's Solo Operator Operations section (EDS design v1.1, §Solo Operator Operations
— Maintenance Calendar) specifies that Dagster sensors auto-populate the FTB
calendar with operational events:

| Event Source | Example |
|-------------|---------|
| Capacity projection assets | "Order 4th NVMe by April 3 — Polygon procurement window opens" |
| Hardware health assets | "ai-srv-01 NVMe-0: 80% TBW consumed — plan replacement" |
| Node health assets | "Erigon v2.y.y available — schedule upgrade window" |
| Dagster schedule metadata | "Weekly: SMART health verification" |
| Pipeline items with dates | "EDS-1 gate target: May 15" |

### What Needs Assessment

The FTB calendar implementation needs to be located and evaluated:

1. **Where is the calendar?** The FTB design mentions a PostgreSQL-backed event
   calendar in Layer 7. Find the schema, tables, and any API/service layer.

2. **What is its current state?** Stephen says it has stale data. What data exists?
   What was it designed to hold? What populates it today (if anything)?

3. **Does it accept programmatic event creation?** If there's an API or direct
   table INSERT, EDS Dagster sensors can write events directly. If it's UI-only,
   an adapter is needed.

4. **What is the event data model?** Fields, types, categories. EDS needs to write
   events with: title, description, severity, source_system ('eds'), event_type
   (maintenance/hardware/milestone/recurring), scheduled_date, and optional
   link to pipeline item or runbook.

5. **Is the calendar shared or FTB-only?** If shared, EDS events coexist with FTB
   events via a `system_id` or `source` column. If FTB-only, it may need a minor
   schema extension.

### Deliverable

A short assessment document answering the 5 questions above, plus one of:
- **If integration is straightforward:** Write the Dagster sensor spec for EDS
  calendar event creation.
- **If schema changes are needed:** Propose minimal schema additions and get
  architect approval before implementing.
- **If the calendar is not viable:** Propose an alternative (lightweight calendar
  service, or extend the existing implementation).

### Pipeline Item

EDS-31: "FTB calendar API assessment and integration"

### Files to Read First

- `FromTheBridge/docs/design/FromTheBridge_design_v3.1.md` — search for "calendar",
  "event", "schedule" sections
- Any migration files or schema definitions in the FTB codebase related to calendar
- The PostgreSQL `forge` schema on proxmox — look for calendar/event tables
- `FromTheBridge/CLAUDE.md` — current state, infrastructure, database rules

---

## ITEM 2: FTB SOLO OPERATOR EVALUATION

### Background

During EDS review synthesis, the following was identified as a critical gap: EDS
(and by extension FTB) is designed, built, and operated by one person. Without
operational tooling designed for solo operation, the system becomes unmanageable
as complexity grows.

For EDS, this was resolved by adding a Solo Operator Operations section to the
design document specifying:

- **Dagster as single observability pane** (no separate monitoring stack)
- **Health assets** for every operational component (nodes, adapters, storage,
  hardware, pipeline)
- **Capacity projection** with exhaustion dates and procurement windows
- **Risk assessment board** with automated severity/trend/escalation scoring
- **Maintenance calendar** (the FTB calendar, Item 1 above)
- **12 runbooks** written during build phases, tested before gates
- **Alert routing** (red→push notification, yellow→daily digest, green→silent)

### What FTB Needs

The same evaluation, but scoped to FTB's specific operational concerns:

**FTB has different operational surfaces than EDS:**

| Concern | FTB-Specific | EDS Equivalent |
|---------|-------------|----------------|
| 9-layer stack health | Is data flowing through all 9 layers? Bronze→Silver→Gold→Marts→Serving | Is data flowing from nodes→empire.observations→sync? |
| Adapter freshness | 11 sources with different cadences (tick, hourly, daily, weekly) | 7 nodes + 11 adapters |
| Export pipeline | Silver→Gold export SDA health (the only ClickHouse reader) | empire_to_forge_sync |
| Serving layer | FastAPI + DuckDB + Arrow Flight health | EDS API (not yet built) |
| Source health | External API availability (Coinalyze, DeFiLlama, FRED, etc.) | Exchange API + public feed availability |
| Data quality | Bronze→Silver transformation correctness, dead letter rates | Derivation correctness, dead letter rates |
| Catalog integrity | 74 metrics, 15 sources in PostgreSQL catalog | Metric registration, sync catalog verification |
| ML model health | 5 LightGBM models in shadow mode (Phase 4) | N/A |
| Cloudflare tunnel | fromthebridge.net → proxmox routing | N/A |

**FTB also shares infrastructure concerns with EDS:**
- proxmox disk usage (ClickHouse, PostgreSQL, MinIO, Dagster all on one machine)
- proxmox RAM pressure (56GB free before EDS adds Dagster + adapters)
- NAS backup verification
- Database credential rotation
- Container health across 19+ Docker containers

### Approach

This is NOT about copying EDS's operations section into FTB. FTB has a different
architecture (9-layer stack vs 3-track), different failure modes (layer propagation
vs track independence), and different operational cadence.

The evaluation should:

1. **Audit FTB's current operational visibility.** What can Stephen see today?
   What's invisible? Where are the blind spots?

2. **Identify FTB-specific failure modes.** What breaks silently? What has cascading
   effects? What has no recovery procedure?

3. **Design FTB's observability using the same principles:**
   - Dagster as single pane (shared with EDS — same Dagster instance)
   - Health assets per layer
   - Risk board (shared table pattern, `system_id` differentiates EDS vs FTB)
   - Runbooks written during build
   - Alert routing (shared with EDS — same notification channel)

4. **Identify shared operational infrastructure.** The risk board, calendar, and
   alert routing should be shared between EDS and FTB. Design once, use from both.

5. **Write the section.** A new section in `FromTheBridge_design_v3.1.md` (or v3.2)
   comparable to EDS's Solo Operator Operations section, scoped to FTB.

### What to Read First

- `FromTheBridge/docs/design/FromTheBridge_design_v3.1.md` — full design, all layers
- `FromTheBridge/CLAUDE.md` — current state, phase gates, infrastructure
- `EmpireDataServices/docs/design/EDS_design_v1.0.md` §Solo Operator Operations —
  the pattern to evaluate against (not copy)
- `EmpireDataServices/docs/plans/post-review-handoff.md` — session 3 summary

### Deliverable

A Solo Operator Operations section for the FTB design document, with:
- FTB-specific health assets (per-layer, per-source, per-pipeline)
- Shared infrastructure with EDS (risk board, calendar, alert routing)
- FTB-specific runbook index
- Updated FTB phase gates incorporating operational criteria
- Pipeline items for implementation

### Constraints

- **Do not break FTB's existing rules.** Rule 1 (downward flow), Rule 2 (CH
  write-only), Rule 3 (no time series in PG). Operational health data goes to
  ClickHouse (`forge.ops_*` or a dedicated `forge_ops` database — architect
  decides).
- **Do not duplicate EDS infrastructure.** Shared Dagster, shared calendar, shared
  alert routing, shared risk board table (with `system_id` column). Two systems,
  one operational surface.
- **Get architect approval** before writing new sections into the FTB design doc.
  Present the evaluation first, write after approval.

---

## CROSS-REFERENCE

| Document | Location | Relevance |
|----------|----------|-----------|
| EDS design v1.1 | `EmpireDataServices/docs/design/EDS_design_v1.0.md` | §Solo Operator Operations — the pattern |
| EDS architecture | `EmpireDataServices/docs/design/empire_data_services_architecture.md` | Infrastructure context |
| EDS post-review handoff | `EmpireDataServices/docs/plans/post-review-handoff.md` | What was done in session 3 |
| EDS review synthesis | `~/Downloads/EDS_Design_Synthesis_v1_0.md` | The review that motivated this work |
| FTB design v3.1 | `FromTheBridge/docs/design/FromTheBridge_design_v3.1.md` | FTB canonical design |
| FTB CLAUDE.md | `FromTheBridge/CLAUDE.md` | FTB rules and current state |
| Shared infra doc | `FromTheBridge/docs/design/thread_infrastructure.md` | Shared infrastructure between EDS and FTB |

---

## SESSION INSTRUCTIONS

When picking up this handoff:

1. Read this document first
2. Read FTB CLAUDE.md and design v3.1 in full
3. Read EDS §Solo Operator Operations for the pattern (don't copy — adapt)
4. **Item 1 (calendar):** Can be done as a focused task. Assess, propose, implement.
5. **Item 2 (solo operator eval):** Present the evaluation to Stephen before writing.
   This is a design-level change that requires architect approval.
6. Both items are independent — can be done in either order or parallel sessions.

**Stephen's words:** "The same evaluation needs to take place against FTB but in a
different session." And: "Having decisive visibility into what processes are running,
the health of those items, the timeline and event horizon of hardware exhaustion and
disk capacity, a visually meaningful maintenance and risk assessment board. A
maintenance calendar. Mechanisms and tools that will make my life as a single
operator more agile and in control without burdening an already complex process."

That's the mandate. Build for a solo operator who needs to see everything, act on
anything, and never be surprised.

---

*Handoff prepared 2026-03-08 from EDS Session 3.*
