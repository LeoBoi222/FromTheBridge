# Phase Status

**As of:** 2026-03-13

| Phase | Status | Detail |
|-------|--------|--------|
| 0 — Schema | Complete | 76 metrics, 11 sources, 4 instruments, CH Silver, MinIO buckets |
| 1 — Collection | In progress | All FTB-buildable work complete. Blocked on EDS adapter delivery. |
| 2–6 | Not started | Gated on Phase 1 |

## Deployed on proxmox

- PostgreSQL `forge` schema: 12 catalog tables, seed data above
- ClickHouse `forge`: 3 tables (observations, dead_letter, current_values MV), ~16k rows
- MinIO: bronze-hot (90d lifecycle), bronze-archive, gold buckets
- Dagster: 4 containers (webserver :3010, daemon, code_ftb, code_eds)
- empire_to_forge_sync: 6h schedule, FRED + Tiingo flowing
- Ops health: 3 assets on 30m schedule. Bronze archive: daily 02:00 UTC. Gold export: hourly :15.

## Blocked

- **EDS:** All 11 sources need Silver rows via `empire_to_forge_sync`.
- **Infra:** Server2 OS upgrade (blocks EDS-0, not FTB).
- **Cleared:** ai-srv-01 operational (2026-03-13). Unblocks Phase 4 ML.

## Gate Criteria

Detailed ✅/❌ markers: v4.0 §Phase Gates.
