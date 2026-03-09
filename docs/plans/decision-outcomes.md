# Decision Outcomes & Technology Watch

Tracks decisions and technology watches that aren't ADRs (ADRs live in v4.0 design doc).

| ID | Decision / Watch | Date | Status | Notes |
|----|-----------------|------|--------|-------|
| D-01 | AutomationCondition over @multi_asset_sensor | 2026-03-09 | pending | multi_asset_sensor deprecated, removed in Dagster 2.0. Design doc updated. Apply when building Phase 1 Dagster assets. |
| D-02 | uv as Python package manager | 2026-03-09 | decided | Matches EDS. pyproject.toml + uv.lock. |
| D-03 | pytest as test framework | 2026-03-09 | decided | Standard, well-supported. |
| D-04 | ruff as linter | 2026-03-09 | decided | Fast, replaces black+isort+flake8. |
| D-05 | DuckDB Iceberg writes for Gold layer | 2026-03-09 | decided | Replaces PyIceberg. ADR-002 updated. Single engine for read+write. |
| D-06 | Adapter specs win for metric name conflicts | 2026-03-09 | decided | 4 seed renames applied. Adapter builders wrote the correct granular names. |
| D-07 | perp_basis is derived, not collected | 2026-03-09 | decided | Removed from Coinalyze sources. Computed in Marts: (perp_price - spot) / spot. |
| D-08 | ETF flows: generic per_instrument, not per-asset prefixed | 2026-03-09 | decided | etf.flows.net_flow_usd with instrument_id, replaces btc/eth/sol-prefixed rows. |
| W-01 | WATCH: DuckDB Iceberg writes (v1.4.2+) — full DML, potential PyIceberg replacement | 2026-03-09 | watch | Re-evaluate when building Gold layer in Phase 1 |
| W-02 | WATCH: DuckLake — DuckDB lakehouse format, PG as catalog | 2026-03-09 | watch | Re-evaluate at Phase 6+. Potential Gold layer simplification. Needs community maturity. |
| W-03 | WATCH: Iceberg V3 — deletion vectors, row lineage, nanosecond timestamps | 2026-03-09 | watch | Target V3 tables when building Bronze/Gold in Phase 1 if library support available |
