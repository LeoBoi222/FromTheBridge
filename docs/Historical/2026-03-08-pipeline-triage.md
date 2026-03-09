# Pipeline Triage — 2026-03-08

## What Changed

Added two columns to `bridge.pipeline_items`:
- **`workstream`**: `ftb` | `eds` | `ops` | `product` | `bridge`
- **`phase_gate`**: Which phase gate the item blocks (nullable — null = ungated backlog)

Reclassified all `FRG-*` items into `LH-*` (Lakehouse) IDs under the `ftb` workstream. Old FRG items marked `cancelled` with `decision_notes` pointing to the new ID.

Migration file: `db/migrations/postgres/0003_pipeline_triage.sql`

## Summary

| Workstream | Gated | Ungated Backlog | Total Active |
|------------|-------|-----------------|--------------|
| **ftb** | 30 | 0 | 30 |
| **eds** | 10 | 21 | 31 |
| **product** | 15 | 0 | 15 |
| **bridge** | 0 | 16 | 16 |
| **ops** | 0 | 1 | 1 |
| **Total** | **55** | **38** | **93** |

## Cancelled (6 items)
- `ACT-01` — CoinGlass (parked, not in budget)
- `ACT-02` — CryptoETFs (no current need)
- `ACT-03` — CryptoQuant (parked, not in budget)
- `FRG-06` — forge_agent_cryptoquant (source parked)
- `VER-02` — FRG-03 verification (superseded by FTB adapters)
- `F19-D10` — dex_cex_fee_ratio (deferred, evidence-gated)

## FRG → LH Reclassification Map

| Old ID | New ID | Phase Gate | Title |
|--------|--------|------------|-------|
| FRG-22-BF | LH-01 | ftb_p1 | Backfill Execution |
| FRG-23 | LH-02 | ftb_p1 | Liquidation WebSocket Collector |
| FRG-23-INGEST | LH-03 | ftb_p1 | Liquidation Ingestion Agent |
| FRG-24 | LH-04 | ftb_p1 | forge_agent_coinmetrics |
| FRG-25 | LH-05 | ftb_p1 | forge_agent_binance_metrics |
| FRG-45 | LH-06 | ftb_p1 | Tiingo ToS verification |
| FRG-40 | LH-07 | ftb_p1 | Coinalyze ToS audit |
| FRG-41 | LH-08 | ftb_p1 | BGeometrics ToS audit |
| FRG-42 | LH-09 | ftb_p1 | Etherscan commercial audit |
| FRG-43 | LH-10 | ftb_p1 | CoinPaprika commercial audit |
| FRG-44 | LH-11 | ftb_p1 | Binance WebSocket ToS audit |
| FRG-46 | LH-12 | ftb_p1 | Formal ToS sign-off (all 11) |
| FRG-15 | LH-13 | ftb_p1 | OHLCV migration decision |
| FRG-22 | LH-14 | ftb_p1 | Dune API Evaluation |
| FRG-10-CB | LH-15 | ftb_p1 | BOJ/PBOC non-FRED source |
| FRG-10-GOLD | LH-16 | ftb_p1 | Gold price non-FRED source |
| FRG-11 | LH-17 | ftb_p1 | CoinPaprika migration |
| FRG-12 | LH-18 | ftb_p1 | GitHub collector migration |
| FRG-13 | LH-19 | ftb_p1 | DeFiLlama migration |
| FRG-02 | LH-20 | ftb_p5 | Forge API (Serving layer) |
| FRG-17 | LH-21 | ftb_p2 | Tier 3 composite metrics |
| FRG-18 | LH-22 | ftb_p5 | API security design |
| FRG-14a | LH-23 | ftb_p2 | GPU compute — approximate |
| FRG-14b | LH-24 | ftb_p2 | GPU compute — true UTXO |

## Execution Sequence

### Now (parallel)
1. **EDS cohesion** (5 items) — CC in Nexus-Council is already doing this
2. **FTB Phase 1 prep** — ToS audits (LH-06 through LH-12), Polygon.io design session

### Next (after cohesion + Phase 1 prep)
3. **FTB Phase 1 build** (19 items) — adapters, Dagster, backfill, GE
4. **EDS pillar rebuild** (5 items, REM-19→24 chain) — can run parallel to FTB P1

### Later (sequenced by phase gates)
5. FTB Phase 2 (3 items) → Phase 3 (EDSx, items TBD) → Phase 4 (6 ML items)
6. Product v1 (15 items) — starts when FTB Phase 5 delivers API

### True backlog (38 items, no phase gate)
- 21 EDS enhancements (W-series)
- 16 Bridge features (B-series)
- 1 ops verification

## Useful Queries

```sql
-- What blocks FTB Phase 1?
SELECT id, title, status, tier FROM bridge.pipeline_items
WHERE workstream = 'ftb' AND phase_gate = 'ftb_p1' AND status NOT IN ('complete','cancelled')
ORDER BY tier, id;

-- What's the current EDS cohesion work?
SELECT id, title, status FROM bridge.pipeline_items
WHERE workstream = 'eds' AND phase_gate = 'eds_cohesion' AND status NOT IN ('complete','cancelled');

-- Active items by workstream
SELECT workstream, phase_gate, count(*) FROM bridge.pipeline_items
WHERE status NOT IN ('complete','cancelled')
GROUP BY workstream, phase_gate ORDER BY workstream, phase_gate NULLS LAST;

-- All ungated backlog (noise to ignore for now)
SELECT workstream, id, title FROM bridge.pipeline_items
WHERE status NOT IN ('complete','cancelled') AND phase_gate IS NULL
ORDER BY workstream, id;
```
