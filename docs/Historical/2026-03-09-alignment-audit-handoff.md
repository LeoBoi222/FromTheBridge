# Design-Execution Alignment Audit — Session Handoff

**Date:** 2026-03-09
**Pipeline item:** LH-69 (critical, ftb_p0, blocks Phase 1)

---

## Problem

Design docs hardcode counts that drift from reality. "121 instruments" propagated to 8+ locations and contradicted the tier system in the same document. Source counts, metric counts, DDL column names — same pattern. Each prior review found issues but none checked internal numeric consistency.

## Root Cause

Counts were stated as facts instead of derived from sources of truth. The fix is simple: replace hardcoded counts with language that references criteria, queries, or processes.

## Task

1. Grep v3.1 and CLAUDE.md for remaining hardcoded counts that will break when reality changes
2. Replace with language referencing the source of truth (a query, a criteria, a process)
3. Check deployed schema against v3.1 DDL specs for column name/type drift
4. Flag stale pipeline items carrying pre-design assumptions
5. Present findings before editing — Stephen reviews first

**Already fixed this session:** 121 instruments (8 locations), source catalog count (15→11), metric count (82→85), Phase 1 gate criterion (≥20→≥12), ML-FS-01 flagged stale.

## Going-Forward Rule

**State rules and criteria in design docs, not counts.** Counts are outputs, not inputs. If a count appears, it must reference its source (a table, a query, a constraint). If it can change, don't hardcode it.

## DB Access

- Forge catalog: `ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"` — schema `forge`
- Pipeline items: same, schema `bridge`, table `pipeline_items`
- ClickHouse: `ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client"`

## Key Files

- `docs/design/FromTheBridge_design_v3.1.md` — primary target
- `CLAUDE.md` — secondary target
- `docs/design/thread_backfill_readiness.md` — tertiary
- `docs/plans/2026-03-09-instrument-admission-design.md` — reference for how fixes should look
