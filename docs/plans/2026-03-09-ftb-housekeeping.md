# FTB Pre-Phase 1 Housekeeping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all audit findings (design doc defects, CLAUDE.md staleness, structural gaps) so FTB is ready for Phase 1 coding.

**Architecture:** No architecture changes. All fixes are documentation corrections, metric name canonicalization, path fixes, and project scaffold creation. The corrective migration file (0004) is updated but NOT deployed — deployment is a separate step.

**Tech Stack:** uv (Python packaging), pytest (testing), ruff (linting)

**Decisions made this session:**
- Python tooling: uv + pytest + ruff
- Gold write engine: DuckDB Iceberg writes (replaces PyIceberg)
- Metric name conflicts: adapter specs win
- perp_basis: derived metric (remove from Coinalyze sources, mark derived)
- ETF flows: add generic `etf.flows.net_flow_usd` (per_instrument), delete per-asset btc/eth/sol rows

---

## Task 1: Design Doc — Fix Metric Catalog Seed Table

**Files:**
- Modify: `docs/design/FromTheBridge_design_v4.0.md:2090-2169`

**Step 1: Apply 4 metric renames in seed table**

| Line | Old metric_id | New metric_id |
|------|--------------|---------------|
| 2103 | `flows.exchange.net_position_usd` | `flows.exchange.net_flow_usd` |
| 2125 | `defi.dex.volume_usd` | `defi.dex.volume_usd_24h` |
| 2135 | `macro.rates.fed_funds` | `macro.rates.fed_funds_effective` |
| 2114 | `stablecoin.supply.circulating_usd` | `stablecoin.supply.per_asset_usd` |

Also update descriptions where needed:
- Line 2103: "Exchange Net Position" → "Exchange Net Flow"
- Line 2135: "Fed Funds Rate" → "Fed Funds Effective Rate"
- Line 2114: "Stablecoin Circulating Supply" → "Stablecoin Per-Asset Supply"

**Step 2: Fix perp_basis — change sources from `{coinalyze}` to `{}`, add computation**

Line 2097: Change sources column from `{coinalyze}` to `{}`. Add computation: `(perp_price - spot_price) / spot_price`. This is a Marts-layer derived metric, not collected from any source.

**Step 3: Delete 3 per-asset ETF rows, add 1 generic row**

Delete lines 2109-2111 (`etf.flows.btc_net_flow_usd`, `etf.flows.eth_net_flow_usd`, `etf.flows.sol_net_flow_usd`).

Also delete lines 2112-2113 (`etf.flows.btc_cumulative_flow_usd`, `etf.flows.eth_cumulative_flow_usd`) — cumulative is derived per-instrument, not per-asset-prefixed.

Replace all 5 with 2 rows:

```
| etf.flows.net_flow_usd | etf | flows | ETF Net Flow (USD) | usd | numeric | per_instrument | 1 day | 2 days | false | | {sosovalue} | active |
| etf.flows.cumulative_flow_usd | etf | flows | ETF Cumulative Flow (USD, derived) | usd | numeric | per_instrument | 1 day | 2 days | false | SUM(net_flow_usd) OVER (PARTITION BY instrument_id ORDER BY observed_at) | {sosovalue} | active |
```

**Step 4: Add 4 missing seed rows**

Add after the last defi row (after line 2128):

```
| defi.aggregate.tvl_usd | defi | aggregate | Aggregate DeFi TVL (USD) | usd | numeric | market_level | 12 hours | 1 day | false | SUM(protocol.tvl_usd) | {defillama} | active |
| stablecoin.supply.total_usd | stablecoin | supply | Total Stablecoin Supply (USD) | usd | numeric | market_level | 12 hours | 1 day | false | SUM(per_asset_usd) | {defillama} | active |
```

Add after the last derivatives row (after line 2100):

```
| derivatives.options.delta_skew_25 | derivatives | options | 25-Delta Skew | pct | numeric | per_instrument | 8 hours | 16 hours | true | | {} | planned |
```

**Step 5: Update metric count in header/summary**

The seed table changes from 74 to 72 rows (74 - 5 deleted ETF + 2 new ETF + 3 new = 74 net, but actually: 74 - 5 + 2 + 3 = 74). Count stays at 74. Verify after edits.

Update the "83 after Phase 1" count: was 74 + 9 = 83. Now 74 + 9 = 83 (seed count unchanged). No change needed.

**Step 6: Commit**

```bash
git add docs/design/FromTheBridge_design_v4.0.md
git commit -m "fix: canonicalize metric catalog seed — 4 renames, 3 additions, 5 ETF consolidation"
```

---

## Task 2: Design Doc — Fix DDL Defects and Spec Errors

**Files:**
- Modify: `docs/design/FromTheBridge_design_v4.0.md`

**Step 1: Fix `bronze_archive_log` UUID→TEXT (lines 2296-2297)**

Change:
```sql
source_id         UUID        NOT NULL REFERENCES forge.source_catalog(source_id),
metric_id         UUID        NOT NULL REFERENCES forge.metric_catalog(metric_id),
```
To:
```sql
source_id         TEXT        NOT NULL REFERENCES forge.source_catalog(source_id),
metric_id         TEXT        NOT NULL REFERENCES forge.metric_catalog(metric_id),
```

**Step 2: Add `backfill_depth_days` to metric_catalog DDL (after line 1809)**

Add column after `deprecated_at`:
```sql
backfill_depth_days INTEGER,              -- Phase 1: target historical depth per metric
```

Also add to the "Columns not shown" note at line 2167-2169 — remove `backfill_depth_days` from the hidden list since it's now in DDL (it was never listed there, so just ensure the seed table note mentions it as NULL for Phase 0 seeds).

**Step 3: Fix CFTC COT instrument_id (line 2673-2674)**

Change:
```
`instrument_id` = `BTC` or `ETH`.
```
To:
```
`instrument_id` = `BTC-USD` or `ETH-USD` (matching canonical instruments table).
```

**Step 4: Fix feature engineering text errors**

Lines 1501-1503: Change `flows.onchain.transfer_volume_usd` → `chain.activity.transfer_volume_usd` (3 occurrences in that block).

Line 2712: Change `spot.price.close_usd` → `price.spot.close_usd`.

Line 2770 (source gap analysis): Change `flows.onchain.transfer_volume_usd` → `chain.activity.transfer_volume_usd`.

**Step 5: Fix Dagster SQLite reference (line 5046)**

Change:
```
Metadata DB corrupted: delete SQLite file and restart
```
To:
```
Metadata DB corrupted: reset PostgreSQL Dagster metadata schema and restart
```

**Step 6: Fix footer v3.1→v4.0 (line 5508)**

Change:
```
this v3.1 document is now authoritative
```
To:
```
this v4.0 document is now authoritative
```

**Step 7: Commit**

```bash
git add docs/design/FromTheBridge_design_v4.0.md
git commit -m "fix: DDL defects — bronze_archive UUID→TEXT, backfill_depth_days, CFTC instrument_id, text errors, footer"
```

---

## Task 3: Design Doc — Update ADR-002 and Gold Schema

**Files:**
- Modify: `docs/design/FromTheBridge_design_v4.0.md`

**Step 1: Update ADR-002 (lines 4770-4775)**

Replace the current ADR-002 decision text:
```
**Decision:** Apache Iceberg tables on MinIO for both Bronze and Gold. PyIceberg for
writes. DuckDB with `iceberg` extension for reads.

**Alternative write path (watch):** DuckDB v1.4.2+ supports full DML on Iceberg
(INSERT, UPDATE, DELETE). Could eliminate PyIceberg write dependency and unify
read/write engines. Re-evaluate when building Gold layer in Phase 1.
```

With:
```
**Decision:** Apache Iceberg tables on MinIO for both Bronze and Gold. DuckDB with
`iceberg` extension for both reads and writes (DML support in v1.4.2+). Single engine
for analytical read/write path. PyIceberg available as fallback if DuckDB Iceberg write
support proves insufficient during Phase 1.
```

**Step 2: Update Gold export section references to PyIceberg**

Line 2496 — change:
```
2. Read existing Gold partition via PyIceberg.
```
To:
```
2. Read existing Gold partition via DuckDB Iceberg extension.
```

Scan for any other PyIceberg references in the Gold write path and update to DuckDB.

**Step 3: Add minimal Gold schema to resolve FTB-24 for Phase 1**

After the Silver → Gold export section (~line 2510), add:

```markdown
**Gold Iceberg table schema (Phase 1 — observations export):**

| Column | Type | Description |
|--------|------|-------------|
| metric_id | STRING | FK to metric_catalog |
| instrument_id | STRING | FK to instruments (nullable for market_level) |
| observed_at | TIMESTAMP | Observation time (UTC) |
| value | DOUBLE | Metric value |
| data_version | INT | ReplacingMergeTree version for dedup |
| ingested_at | TIMESTAMP | Silver ingestion time (PIT anchor) |
| metric_domain | STRING | Partition key: derivatives, macro, flows, defi, onchain |
| year_month | STRING | Partition key: YYYY-MM format |

Partitioned by `(year_month, metric_domain)`. Written by DuckDB Iceberg DML.
```

**Step 4: Update FTB-24 known gap (line 5335)**

Change:
```
| Gold/Marts Iceberg schema (FTB-24) | Bronze and Silver DDL specified. Gold and Marts table schemas not defined. | Before Phase 2 build prompt |
```
To:
```
| Gold/Marts Iceberg schema (FTB-24) | Gold observations schema defined (Phase 1). Marts table schemas not defined. | Marts schema: before Phase 2 build prompt |
```

**Step 5: Commit**

```bash
git add docs/design/FromTheBridge_design_v4.0.md
git commit -m "fix: ADR-002 PyIceberg→DuckDB writes, add Gold schema, resolve FTB-24 for Phase 1"
```

---

## Task 4: Design Doc — Add SoSoValue Field Mapping Table

**Files:**
- Modify: `docs/design/FromTheBridge_design_v4.0.md:2688-2694`

**Step 1: Add field mapping table after SoSoValue section header**

After line 2694 ("Cadence: Daily..."), add:

```markdown

**Field mappings:**

| Source field | Canonical metric_id | instrument_id | Notes |
|---|---|---|---|
| BTC ETF net flow (USD) | `etf.flows.net_flow_usd` | `BTC-USD` | Daily net inflow/outflow |
| ETH ETF net flow (USD) | `etf.flows.net_flow_usd` | `ETH-USD` | Daily net inflow/outflow |
| SOL ETF net flow (USD) | `etf.flows.net_flow_usd` | `SOL-USD` | Daily net inflow/outflow |

**Derived by adapter:**
- `etf.flows.cumulative_flow_usd` — running sum of net_flow_usd per instrument_id, computed at write time.
```

**Step 2: Flag 5 other adapters as needing field mapping tables**

Add a note after the adapter specs section (after ~line 2770):

```markdown
**Adapter spec completeness (Phase 1 pre-build audit):**

| Adapter | Field mapping table | Status |
|---|---|---|
| Coinalyze | Yes (6 fields) | Complete |
| DeFiLlama | Yes (5 fields) | Complete |
| FRED | Yes (5 Phase 0 + 18 Phase 1 implicit) | Needs explicit Phase 1 mappings |
| CFTC COT | Yes (4 fields) | Complete |
| SoSoValue | Yes (3 fields) | Complete (added this session) |
| Tiingo | No | Build during adapter implementation |
| CoinPaprika | No | Build during adapter implementation |
| BGeometrics | No | Build during adapter implementation |
| CoinMetrics | No | Build during adapter implementation |
| Etherscan/Explorer | No | Build during adapter implementation |
| Binance BLC-01 | No | Build during adapter implementation |
```

**Step 3: Commit**

```bash
git add docs/design/FromTheBridge_design_v4.0.md
git commit -m "fix: add SoSoValue field mapping table, flag incomplete adapter specs"
```

---

## Task 5: CLAUDE.md — Fix All Stale References and Errors

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Fix Layer 8 phase annotation (line 24)**

Change `Phase 6.` to `Phase 5.` in the Serving layer row.

**Step 2: Fix "As of" date (line 54)**

Change `2026-03-06` to `2026-03-09`.

**Step 3: Fix migration file path (line 68)**

Change:
```
- Forge DB schema deployed (`db/migrations/0001_phase0_schema.sql`)
```
To:
```
- Forge DB schema deployed (`db/migrations/postgres/0001_catalog_schema.sql`)
```

**Step 4: Fix v3.1 citation (line 70)**

Change:
```
reference sources not cataloged per v3.1
```
To:
```
reference sources not cataloged per v4.0 §Sources Catalog
```

**Step 5: Fix instrument-admission path (line 71)**

Change:
```
docs/plans/2026-03-09-instrument-admission-design.md
```
To:
```
docs/Historical/2026-03-09-instrument-admission-design.md
```

**Step 6: Add MinIO to Known Gaps (after line 82)**

Add:
```
- MinIO service definition not yet in docker-compose.yml (Phase 1)
```

**Step 7: Fix Redis reference (line 103)**

Change:
```
PostgreSQL, ClickHouse, Dagster metadata, Redis
```
To:
```
PostgreSQL, ClickHouse, Dagster metadata
```

(Redis is an Empire service, not FTB. Remove from FTB storage table.)

**Step 8: Fix PG migration command (line 140)**

Change:
```
**DB migration (PG):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"`
```
To:
```
**DB migration (PG — forge schema):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_user -d crypto_structured"`
```

**Step 9: Fix thread references (lines 158, 265)**

Line 158 — change:
```
Triggers defined in `thread_infrastructure.md`.
```
To:
```
Triggers defined in v4.0 §Infrastructure Migration Triggers.
```

Line 265 — change:
```
Full gate criteria in `thread_6_build_plan.md`.
```
To:
```
Full gate criteria in v4.0 §Phase Gates.
```

**Step 10: Fix eds_ftb_cohesion_audit path (line 293)**

Change:
```
docs/design/eds_ftb_cohesion_audit.md
```
To:
```
docs/design/Archived/eds_ftb_cohesion_audit.md
```

**Step 11: Scope hardcoded-IP rule (line 312)**

Change:
```
- Hardcode IPs — use environment variables
```
To:
```
- Hardcode IPs in application code — use environment variables (ops documentation IPs are exempt)
```

**Step 12: Fix Python tooling (lines 257-259)**

Change:
```
**Python-specific:**
- Package management: `pyproject.toml` (tooling TBD — not yet chosen)
- Testing framework: TBD
- Linting: TBD
```
To:
```
**Python-specific:**
- Package management: `uv` with `pyproject.toml` + `uv.lock`
- Testing framework: `pytest`
- Linting: `ruff`
```

**Step 13: Commit**

```bash
git add CLAUDE.md
git commit -m "fix: CLAUDE.md — 12 stale refs, path fixes, Python tooling, phase annotation"
```

---

## Task 6: Python Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/ftb/__init__.py`
- Create: `src/ftb/py.typed`
- Create: `.python-version`
- Create: `tests/__init__.py`

**Step 1: Create `.python-version`**

```
3.12
```

**Step 2: Create `pyproject.toml`**

```toml
[project]
name = "ftb"
version = "0.1.0"
description = "FromTheBridge — Crypto market intelligence lakehouse"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ftb"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
target-version = "py312"
line-length = 120
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

**Step 3: Create package directories**

```bash
mkdir -p src/ftb tests
```

`src/ftb/__init__.py`:
```python
"""FromTheBridge — Crypto market intelligence lakehouse."""
```

`src/ftb/py.typed`:
```
```
(empty marker file for PEP 561)

`tests/__init__.py`:
```python
```
(empty)

**Step 4: Delete old empty files**

```bash
rm -f pyproject.toml requirements.txt Makefile  # all currently empty
```

(Then create the real pyproject.toml above.)

**Step 5: Verify**

```bash
cd /var/home/stephen/Projects/FromTheBridge
uv sync
uv run pytest --co -q  # should show "no tests ran" with 0 errors
```

**Step 6: Commit**

```bash
git add pyproject.toml .python-version src/ tests/
git commit -m "feat: Python project scaffold — uv, pytest, ruff"
```

---

## Task 7: Structural Cleanup

**Files:**
- Delete: `.claude/phase0-open`
- Modify: `secrets/` permissions
- Modify: `docs/plans/decision-outcomes.md`
- Modify: memory file `MEMORY.md`

**Step 1: Delete phase0-open flag**

```bash
rm .claude/phase0-open
```

This re-enables the `guard-ddl.sh` schema immutability hook. After this, any DDL command will be blocked unless the flag is recreated (which should only happen if Phase 0 needs to be reopened by architect decision).

**Step 2: Fix secrets permissions**

```bash
chmod 600 secrets/*.txt
```

**Step 3: Update decision-outcomes.md**

Add new decisions from this session:

```markdown
| D-02 | uv as Python package manager | 2026-03-09 | decided | Matches EDS. pyproject.toml + uv.lock. |
| D-03 | pytest as test framework | 2026-03-09 | decided | Standard, well-supported. |
| D-04 | ruff as linter | 2026-03-09 | decided | Fast, replaces black+isort+flake8. |
| D-05 | DuckDB Iceberg writes for Gold layer | 2026-03-09 | decided | Replaces PyIceberg. ADR-002 updated. Single engine for read+write. |
| D-06 | Adapter specs win for metric name conflicts | 2026-03-09 | decided | 4 seed renames applied. Adapter builders wrote the correct granular names. |
| D-07 | perp_basis is derived, not collected | 2026-03-09 | decided | Removed from Coinalyze sources. Computed in Marts: (perp_price - spot) / spot. |
| D-08 | ETF flows: generic per_instrument, not per-asset prefixed | 2026-03-09 | decided | etf.flows.net_flow_usd with instrument_id, replaces btc/eth/sol-prefixed rows. |
```

**Step 4: Update MEMORY.md**

Update the "Project State" section:
- Add: "Python tooling decided: uv + pytest + ruff"
- Add: "Gold write engine decided: DuckDB Iceberg writes (ADR-002 updated)"
- Add: "Metric catalog: 4 renames, 3 additions, 5 ETF consolidation applied to v4.0"
- Fix: "Session plans archived to `docs/design/Archived/`" (was `docs/Historical/`)
- Add: "Pre-Phase 1 housekeeping: COMPLETE (2026-03-09)"

**Step 5: Commit**

```bash
git add .claude/phase0-open docs/plans/decision-outcomes.md
git commit -m "chore: structural cleanup — remove phase0-open, update decisions, fix memory"
```

Note: `secrets/` permission changes won't show in git (permissions aren't tracked by default). The `.claude/phase0-open` deletion will show as a file removal.

---

## Task 8: Update Corrective Migration

**Files:**
- Modify: `db/migrations/postgres/0004_phase0_corrective.sql`

**Step 1: Read current corrective migration**

Read the file to understand what's already there.

**Step 2: Add/update metric seed changes**

The corrective migration already exists. Ensure it includes:
- The 4 metric renames (UPDATE statements or DROP+INSERT)
- The 3 new metric rows (INSERT)
- The 5 ETF row deletions + 2 new ETF rows
- The `backfill_depth_days` column addition (ALTER TABLE)
- The perp_basis sources fix

If the existing migration uses DROP TABLE + CREATE TABLE (which MEMORY.md suggests), update the CREATE TABLE and INSERT statements to reflect the canonical seed from Task 1.

**Step 3: Verify SQL is valid**

```bash
# Syntax check only — do NOT execute against live DB
cat db/migrations/postgres/0004_phase0_corrective.sql | head -20
```

**Step 4: Commit**

```bash
git add db/migrations/postgres/0004_phase0_corrective.sql
git commit -m "fix: corrective migration — metric renames, additions, backfill_depth_days column"
```

**DO NOT DEPLOY.** Deployment to proxmox is a separate step requiring architect sign-off and the ClickHouse corrective migration passwords to be set.

---

## Verification Checklist

After all tasks complete, verify:

1. `grep -c 'net_position_usd' docs/design/FromTheBridge_design_v4.0.md` → 0
2. `grep -c 'defi.dex.volume_usd |' docs/design/FromTheBridge_design_v4.0.md` → 0 (note trailing pipe to avoid matching volume_usd_24h)
3. `grep -c 'macro.rates.fed_funds |' docs/design/FromTheBridge_design_v4.0.md` → 0
4. `grep -c 'flows.onchain.transfer_volume_usd' docs/design/FromTheBridge_design_v4.0.md` → 0
5. `grep -c 'spot.price.close_usd' docs/design/FromTheBridge_design_v4.0.md` → 0
6. `grep -c 'UUID.*REFERENCES.*source_catalog' docs/design/FromTheBridge_design_v4.0.md` → 0
7. `grep -c 'v3.1 document is now authoritative' docs/design/FromTheBridge_design_v4.0.md` → 0
8. `grep -c 'TBD' CLAUDE.md` → 0
9. `grep -c 'Phase 6\.' CLAUDE.md` → 1 (only in the Productization phase, not Serving)
10. `test -f .claude/phase0-open` → should fail (file deleted)
11. `uv run pytest --co -q` → exits 0
12. `stat -c '%a' secrets/*.txt` → all 600

---

## Not In Scope (deferred)

- Deploy corrective migrations to proxmox (blocked on CH passwords)
- T5_training_windows.json report (produced during Phase 1)
- Runbooks FTB-01 through FTB-08 (written during Phase 1 alongside adapter builds)
- BLC-01 aggregation formula, FRED 18 series mappings, DeFiLlama exotic tokens (per-adapter during Phase 1)
- Tiingo/CoinPaprika/BGeometrics/CoinMetrics/Etherscan field mapping tables (per-adapter during Phase 1)
- EDS-47/EDS-48 resolution (EDS-side dependency)
- scripts/proxmox_shutdown.sh creation (ops script, not code — create when needed)
