# FTB Technology Intelligence Handoff

**From:** EDS session 2026-03-09
**For:** Next FTB session

## Actions Required in FTB

### 1. CLAUDE.md — Add Dagster constraint

Under the Dagster section, add:
```
- Use `AutomationCondition` for sensor/trigger logic. Do NOT use `@multi_asset_sensor` (deprecated, removed in Dagster 2.0).
```

FTB design explicitly uses `multi_asset_sensor` for Silver-to-Gold export trigger. The primitive still works but is deprecated. Since FTB hasn't built Phase 1 yet, switch to `AutomationCondition` before writing any sensor code.

### 2. Design doc — Note DuckDB Iceberg write support

DuckDB v1.4.2+ has full DML on Iceberg (INSERT, UPDATE, DELETE). FTB Gold layer design assumed PyIceberg for writes. DuckDB Iceberg writes could eliminate that dependency and simplify the Gold layer write path.

This is a design doc amendment, not a code change. Add to the Gold layer section as an alternative to PyIceberg.

### 3. FTB decision-outcomes.md — Add entries

```
| D-xx | AutomationCondition over @multi_asset_sensor | 2026-03-09 | pending | multi_asset_sensor deprecated, removed in Dagster 2.0 |
| W-xx | WATCH: DuckDB Iceberg writes (v1.4.2+) — full DML, potential PyIceberg replacement | 2026-03-09 | watch | Re-evaluate when building Gold layer in Phase 1 |
| W-xx | WATCH: DuckLake — DuckDB lakehouse format, PG as catalog | 2026-03-09 | watch | Re-evaluate at Phase 6+. Potential Gold layer simplification. Needs community maturity. |
| W-xx | WATCH: Iceberg V3 — deletion vectors, row lineage, nanosecond timestamps | 2026-03-09 | watch | Target V3 tables when building Bronze/Gold in Phase 1 if library support available |
```

### 4. uv — Not applicable to FTB

FTB already has its Python tooling established. uv decision is EDS-only.

## Context

These items came from a technology scan on 2026-03-09. Tier 1 items (uv, AutomationCondition) were applied to EDS immediately. FTB items require a FTB session for proper context loading and review.
