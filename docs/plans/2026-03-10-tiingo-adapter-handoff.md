# Tiingo Adapter — Session Handoff

**Date:** 2026-03-10
**Sessions:** 2 (design + planning + Task 1, then Tasks 2-10)

## What Was Done

### Session 1 (design + plan)
1. **Design:** Brainstormed adapter architecture, chose composition pattern (Option C), approved design
2. **Design doc:** `docs/plans/2026-03-10-tiingo-adapter-design.md` — committed `9a2b48e`
3. **Implementation plan:** `docs/plans/2026-03-10-tiingo-adapter-plan.md` — 12 tasks, full TDD, exact code — committed `31730dc`
4. **Task 1 complete:** Dependencies added — committed `74a2393`

### Session 2 (implementation)
5. **Task 2:** instrument_source_map migration — `48f2ac4`
6. **Task 3:** Validation module (Observation, ValidationResult, validate_observation) — 6 tests — `70a6fca`
7. **Tasks 4-6:** Shared writers (Silver/Bronze/collection) — 11 tests — `287e76b`
8. **Tasks 7-8:** Dagster resources + Tiingo adapter — 8 tests — `0e20cf6`
9. **Tasks 9-10:** Dagster definitions wiring + Docker secret mount — `c61e854`

**Total: 25 tests, all passing.**

## What Remains

### Task 11: Deploy and Smoke Test
```
1. Create Tiingo secret on proxmox:
   ssh root@192.168.68.11 "mkdir -p /opt/empire/FromTheBridge/secrets/external_apis && grep TIINGO_API_KEY /opt/empire/.env | cut -d= -f2- | tr -d '\n' > /opt/empire/FromTheBridge/secrets/external_apis/tiingo.txt && chmod 600 /opt/empire/FromTheBridge/secrets/external_apis/tiingo.txt"

2. Rsync to proxmox:
   rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' /var/home/stephen/Projects/FromTheBridge/ root@192.168.68.11:/opt/empire/FromTheBridge/

3. Deploy migration:
   cat db/migrations/postgres/0005_instrument_source_map.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"

4. Rebuild + restart Dagster:
   ssh root@192.168.68.11 "cd /opt/empire/FromTheBridge && docker compose build empire_dagster_code && docker compose up -d"

5. Verify code server loads asset:
   ssh root@192.168.68.11 "docker logs empire_dagster_code 2>&1 | tail -20"
   Expected: gRPC server listening on 4266, no import errors

6. Verify via webserver (http://192.168.68.11:3010):
   collect_tiingo_price asset visible in asset graph

7. Trigger single partition (2024-01-15) from Dagster UI

8. Verify Silver:
   ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader.txt) --query \"SELECT metric_id, instrument_id, observed_at, value FROM forge.observations WHERE source_id = 'tiingo' LIMIT 10\""

9. Verify Bronze:
   ssh root@192.168.68.11 "docker exec -i empire_minio mc ls local/bronze-hot/tiingo/2024-01-15/price/"

10. Verify collection event:
    ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_reader -d crypto_structured -c \"SELECT source_id, status, observations_written FROM forge.collection_events WHERE source_id = 'tiingo' ORDER BY started_at DESC LIMIT 1;\""
```

### Minor: ruff not in dependencies
`ruff` is configured in pyproject.toml but not in dependencies. Add as dev dependency when convenient.

## Key Decisions (reference)

- **Composition over inheritance** — shared writers in `src/ftb/writers/`, adapter orchestrates
- **instrument_source_map** — new PG table for cross-source symbol resolution
- **OHLCV composite** — NOT written to Silver, scalar metrics only (close_usd, volume_usd_24h), Bronze preserves raw
- **Single fetch, triple extract** — one API call, 2 Silver observations per instrument per timestamp
- **Dagster daily partitions** — DailyPartitionsDefinition(start_date="2014-01-01") for backfill + live
- **forge_writer role** — actual deployed role name (CLAUDE.md says forge_user, but migrations use forge_writer)

## Resume Instructions

```
Next session: Deploy (Task 11 above), then smoke test.
Base SHA: c61e854
All code complete. 25/25 tests pass.
```

## Commits

| SHA | Description |
|-----|-------------|
| `60f903d` | feat: deploy Phase 1 infrastructure — MinIO + Dagster |
| `9a2b48e` | docs: Tiingo adapter design |
| `31730dc` | docs: Tiingo adapter implementation plan |
| `74a2393` | chore: add adapter dependencies |
| `48f2ac4` | feat: add instrument_source_map table with Tiingo seed data |
| `70a6fca` | feat: add observation validation module with tests |
| `287e76b` | feat: add shared writers — Silver, Bronze, collection |
| `0e20cf6` | feat: add Tiingo adapter with Dagster resources and tests |
| `c61e854` | feat: wire collect_tiingo_price Dagster asset with Tiingo API key secret |

## Files Created/Modified

### New files
- `db/migrations/postgres/0005_instrument_source_map.sql`
- `src/ftb/validation/__init__.py`, `src/ftb/validation/core.py`
- `src/ftb/writers/__init__.py`, `src/ftb/writers/silver.py`, `src/ftb/writers/bronze.py`, `src/ftb/writers/collection.py`
- `src/ftb/adapters/__init__.py`, `src/ftb/adapters/tiingo.py`, `src/ftb/adapters/tiingo_asset.py`
- `src/ftb/resources.py`
- `tests/validation/__init__.py`, `tests/validation/test_core.py`
- `tests/writers/__init__.py`, `tests/writers/test_silver.py`, `tests/writers/test_bronze.py`, `tests/writers/test_collection.py`
- `tests/adapters/__init__.py`, `tests/adapters/test_tiingo.py`

### Modified files
- `src/ftb/definitions.py` — registered asset + resources
- `docker-compose.yml` — added tiingo_api_key secret
