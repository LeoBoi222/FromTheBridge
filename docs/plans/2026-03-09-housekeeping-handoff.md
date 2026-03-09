# FTB Housekeeping Session Handoff

**Date:** 2026-03-09
**Status:** Tasks 1-4 COMPLETE, Tasks 5-8 REMAINING

## What Was Done (This Session)

Design doc (v4.0) — all fixes applied:

1. **Metric catalog seed** — 4 renames (net_position→net_flow, volume_usd→volume_usd_24h, fed_funds→fed_funds_effective, circulating_usd→per_asset_usd), perp_basis fixed (derived, not Coinalyze), 5 ETF rows consolidated to 2 (generic per_instrument), 3 rows added (delta_skew_25, aggregate.tvl_usd, supply.total_usd)
2. **DDL defects** — bronze_archive_log UUID→TEXT, backfill_depth_days added to metric_catalog, CFTC instrument_id fixed to BTC-USD/ETH-USD
3. **Text errors** — flows.onchain→chain.activity (all occurrences), spot.price→price.spot (all), Dagster SQLite→PostgreSQL, footer v3.1→v4.0
4. **ADR-002** — PyIceberg→DuckDB writes as primary Gold engine, Gold Iceberg schema added, FTB-24 updated
5. **SoSoValue** — field mapping table added, adapter completeness table added

## What Remains (Next Session)

Full plan: `docs/plans/2026-03-09-ftb-housekeeping.md` (Tasks 5-8)

### Task 5: CLAUDE.md Fixes (12 items)
- Fix Layer 8 "Phase 6"→"Phase 5"
- Fix "As of" date to 2026-03-09
- Fix migration file path (root→postgres/)
- Fix v3.1 citation on line 70
- Fix instrument-admission path (plans/→Historical/)
- Add MinIO to Known Gaps
- Remove Redis from storage table
- Fix PG migration command (crypto_user→forge_user)
- Fix 2 thread references (add Archived/ or v4.0 section refs)
- Fix eds_ftb_cohesion_audit path (add Archived/)
- Scope hardcoded-IP rule to application code
- Update Python tooling from TBD to uv/pytest/ruff

### Task 6: Python Project Scaffold
- Create pyproject.toml (uv, pytest, ruff config)
- Create src/ftb/__init__.py, py.typed
- Create .python-version (3.12)
- Create tests/__init__.py
- Verify with `uv sync && uv run pytest --co -q`

### Task 7: Structural Cleanup
- Delete .claude/phase0-open (re-enable DDL guard)
- Fix secrets/ permissions to 600
- Update decision-outcomes.md (D-02 through D-08)
- Update MEMORY.md

### Task 8: Update Corrective Migration
- Update 0004_phase0_corrective.sql to reflect seed changes
- Add backfill_depth_days column
- DO NOT DEPLOY — passwords still placeholders

## Decisions Made This Session
- Python tooling: uv + pytest + ruff
- Gold write engine: DuckDB Iceberg writes (ADR-002 updated)
- Metric names: adapter specs win (4 renames applied)
- perp_basis: derived metric, not Coinalyze-sourced
- ETF flows: generic per_instrument (etf.flows.net_flow_usd), per-asset rows deleted
- forge.event_calendar exception: FTB-side already has calendar_writer role in v4.0 §Solo Ops — EDS CLAUDE.md rule is correct, EDS-31 assesses what to write, not whether

## To Resume
```
Open Claude Code in ~/Projects/FromTheBridge
Say: "Continue housekeeping from docs/plans/2026-03-09-housekeeping-handoff.md — pick up at Task 5"
```
