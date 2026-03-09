# T5 Patch Handoff — Phase 1 Prompt + Synthesis Updates
**Date:** 2026-03-07
**Context:** T5 synthesis complete. All 8 open questions resolved. This handoff specifies exact edits to apply.

---

## Files to Edit

1. `docs/plans/phase-prompts/phase1-data-collection.md` — Phase 1 prompt
2. `docs/design/thread_backfill_readiness.md` — T5 synthesis document
3. `~/.claude/projects/-var-home-stephen-Projects-FromTheBridge/memory/MEMORY.md` — Persistent memory

---

## Closed Decisions (All 8)

| Q | Decision | Impact on Phase 1 Prompt |
|---|----------|--------------------------|
| Q1 | Accept Phase 0 as-is, HY OAS → Phase 1 | Already in prompt (line 399). No change. |
| Q2 | Option C — `macro.nvt_txcount_proxy` = market_cap / tx_count. CoinMetrics community CSV `transaction_count` + market cap. Evidence-gated revisit: if ML F1 drop > 3% without NVT, get CoinMetrics paid quote. | Add metric to Phase 1 additions. Add to metric catalog. |
| Q3 | CoinMetrics `CapMrktCurUSD` for BTC/ETH historical market cap. Forward: Tiingo price × CoinPaprika current circulating supply. CoinGecko rejected (free tier = non-commercial ToS). NVT proxy BTC/ETH-deep, altcoin-shallow. | Add market cap sourcing note to CoinMetrics + CoinPaprika adapter specs. |
| Q4 | Add BREAKEVEN_INFLATION_10Y + REAL_YIELD_10Y to catalog. Skip MFG_EMPLOYMENT. | Add 2 series to FRED expansion list. Update metric counts (74 → 76 base, 84 total at Phase 1 end). |
| Q5 | Deferred. Free tier backfill first, Pro only if operationally painful. Exchange flows are Priority 2 (not on critical path). | No change to prompt. |
| Q6 | Approved. Parallel Phase 3/4 tracks. EDSx and ML independent after Phase 2 gate. | No change to Phase 1 prompt (affects Phase 3/4 planning only). |
| Q7 | Token unlock data deferred from Phase 3. | No change to Phase 1 prompt. |
| Q8 | Polygon.io — separate blocker, not resolved by T5. Still listed in MEMORY.md. | No change. |

---

## Edit 1: Phase 1 Prompt — FRED Adapter Expansion

**File:** `docs/plans/phase-prompts/phase1-data-collection.md`

### 1a. Add Gold + MOVE to FRED expansion list (line ~227)

Current line 227:
```
**Phase 1 expansion (18 additional):** `macro.rates.yield_30y`, ...
```

Change to **20 additional** and append these 2 series to the list:
- `macro.commodity.gold` (FRED series: GOLDAMGBD228NLBM)
- `macro.rates.move_index` (FRED series: MOVE)

### 1b. Add BREAKEVEN_INFLATION_10Y + REAL_YIELD_10Y (same line)

These are already in Forge (140k rows) under their FRED names. Add to the expansion list:
- `macro.inflation.breakeven_10y` (FRED series: T10YIE — note: Forge uses BREAKEVEN_INFLATION_10Y as label but FRED series_id is T10YIE)
- `macro.rates.real_yield_10y` (FRED series: DFII10)

Update count from "18 additional" to **22 additional**.

### 1c. Update FRED series count in spec header (line ~211)

Change "23 macro series (5 Phase 0 seed + 18 Phase 1 expansion)" to "27 macro series (5 Phase 0 seed + 22 Phase 1 expansion)"

### 1d. Add Gold + MOVE to gate criteria

Add gate criterion after existing `macro.credit.hy_oas` gate (line ~399):
```
| `macro.commodity.gold` in FRED | `GOLDAMGBD228NLBM` collecting, Silver rows present |
| `macro.rates.move_index` in FRED | `MOVE` collecting, Silver rows present |
```

---

## Edit 2: Phase 1 Prompt — Binance Bulk Migration

**File:** `docs/plans/phase-prompts/phase1-data-collection.md`

### 2a. Add Binance bulk to migration table (after line ~328)

Add row to migration table:
```
| Binance bulk OI/LS | 96,771 | GREEN | Migrate from Forge DB — OI + L/S ratio, 66 instruments, Dec 2021–Mar 2026 |
```

### 2b. Add to build steps (after line ~363, migration step)

Add step after step 1 or integrate into step 1:
"Migrate Binance bulk data (96,771 rows) from `empire_forge_db.forge.derivatives` to ClickHouse Silver"

---

## Edit 3: Phase 1 Prompt — NVT Proxy Metric

**File:** `docs/plans/phase-prompts/phase1-data-collection.md`

### 3a. Add to Phase 1 Metric Additions section (line ~344)

Add:
- `macro.nvt_txcount_proxy` (derived — market_cap / transaction_count, composite source: CoinMetrics tx_count + CoinMetrics CapMrktCurUSD for BTC/ETH historical, CoinPaprika×Tiingo for forward)

### 3b. Update total metric count

Change "Total at Phase 1 completion: 82 metrics" to **87 metrics** (76 base + 8 original additions + 1 NVT proxy + 2 FRED series already counted in expansion).

Wait — let me recount:
- Phase 0 base: 74
- Q4 adds: +2 (breakeven, real yield) = 76
- Phase 1 additions (original 8): +8 = 84
- NVT proxy: +1 = 85
- Gold, MOVE already in the 22 FRED expansion but may need catalog entries: check if they're in the original 74

Actually: Gold (`macro.commodity.gold`) and MOVE (`macro.rates.move_index`) are already in the Phase 0 74-metric seed (per T1 audit: "3 macro metrics in catalog missing from data"). They have catalog rows, just no data. So no new catalog entries needed for those — just FRED adapter fetch.

Revised count: 74 (Phase 0) + 2 (Q4 FRED series) + 8 (original Phase 1 additions) + 1 (NVT proxy) = **85 metrics at Phase 1 completion**.

### 3c. Add to CoinMetrics adapter spec (line ~279-283)

Add note: "CoinMetrics community CSV also provides `CapMrktCurUSD` (market cap) for BTC and ETH. Extract and write to Silver as `spot.market_cap.usd` for these two instruments. This provides historical market cap for the NVT proxy metric."

### 3d. Add to source gap analysis or CoinPaprika spec

Add note: "CoinPaprika provides current `circulating_supply` on free tier. Combined with Tiingo spot price, this enables forward market cap computation for instruments beyond BTC/ETH."

---

## Edit 4: Phase 1 Prompt — New Gate Criteria from T5

**File:** `docs/plans/phase-prompts/phase1-data-collection.md`

Add to Hard Gate Criteria table (line ~376):

```
| Backfill depth documented | Every active metric in `forge.metric_catalog` has non-NULL `backfill_depth_days` |
| Training window viability | Architect-signed JSON report confirming per-model floor dates and row counts |
| BLC-01 rsync operational | ≥ 7 `.complete` files in proxmox landing directory |
| Dead letter triage | No unresolved dead letters older than 7 days |
| Priority-1 backfill verified | Coinalyze, DeFiLlama, BGeometrics, Tiingo, Binance bulk all have ≥ 2yr Silver history |
```

---

## Edit 5: T5 Synthesis — Close Open Questions

**File:** `docs/design/thread_backfill_readiness.md`

Replace the content of section `## 9. Open Questions for Architect Review` with:

```markdown
## 9. Architect Decisions (All Resolved 2026-03-07)

| Q | Question | Decision |
|---|----------|----------|
| Q1 | Phase 0 gate — HY OAS never loaded | Accept Phase 0 as-is. HY OAS added to Phase 1 FRED adapter. |
| Q2 | NVT Proxy source | Option C: `macro.nvt_txcount_proxy` = market_cap / transaction_count. CoinMetrics community CSV for tx_count. Evidence-gated: if ML F1 drop > 3% without true NVT, get CoinMetrics paid quote. |
| Q3 | Market cap source for NVT proxy | CoinMetrics `CapMrktCurUSD` for BTC/ETH historical. Forward: Tiingo price × CoinPaprika circulating supply. CoinGecko rejected (free tier non-commercial ToS). NVT proxy is BTC/ETH-deep, altcoin-shallow. |
| Q4 | Extra FRED series | Add BREAKEVEN_INFLATION_10Y + REAL_YIELD_10Y to catalog (76 base metrics). Skip MFG_EMPLOYMENT. |
| Q5 | Etherscan Pro budget | Deferred. Free tier backfill first. Exchange flows Priority 2, not on critical path. Pro only if operationally painful. |
| Q6 | Parallel Phase 3/4 | Approved. EDSx and ML tracks independent after Phase 2 gate. Saves ~1-2 weeks. |
| Q7 | Token unlock data | Deferred from Phase 3. Accept null redistribution for this feature in v1. |
| Q8 | Polygon.io integration | Separate blocker. Design session required before Phase 1. Not resolved by T5. |
```

Also update the Pipeline Items SQL at the end — change FRG-34 to reflect the closed decision:
```sql
-- FRG-34 resolved: NVT proxy uses CoinMetrics tx_count + market cap
UPDATE bridge.pipeline_items SET status = 'complete', completed_at = NOW(),
  decision_notes = 'Q2+Q3 closed: macro.nvt_txcount_proxy = market_cap / tx_count. CoinMetrics CapMrktCurUSD for BTC/ETH historical, Tiingo×CoinPaprika forward. Evidence-gated revisit if ML F1 > 3% drop.'
  WHERE id = 'FRG-34';
```

---

## Edit 6: MEMORY.md Updates

**File:** `~/.claude/projects/-var-home-stephen-Projects-FromTheBridge/memory/MEMORY.md`

Update `## Project State` section:
```
- Phase 0 complete, Phase 1 not started
- Blocking: Polygon.io integration design session before Phase 1
- **T5 synthesis complete:** All 8 open questions resolved. Phase 1 prompt patch pending (this handoff).
- **T3b audit complete**
```

Remove the line:
```
- **Pre-Phase-1 audit in progress:** T3 (depth) complete, T3b (quality) prompt written but not yet executed
```

Add to `## Patterns & Decisions`:
```
- NVT proxy: macro.nvt_txcount_proxy = market_cap / tx_count (not on-chain volume). Evidence-gated: revisit if ML F1 > 3% drop.
- CoinGecko free tier rejected on ToS grounds (non-commercial). Do not use for any data sourcing.
- Phase 3/4 run in parallel after Phase 2 gate. EDSx and ML share no state.
- Etherscan Pro deferred — free tier backfill first, exchange flows are Priority 2.
- 450-day ML wait assumption eliminated — all models have sufficient historical depth.
```

---

## Verification After Edits

1. Grep Phase 1 prompt for "18 additional" — should be zero (changed to 22)
2. Grep for "82 metrics" — should be zero (changed to 85)
3. Grep T5 doc for "Open Questions" — should show "Architect Decisions (All Resolved)"
4. Read MEMORY.md — should reflect T5 complete, no pre-Phase-1 audit in progress

---

## Important: Instrument Admission Framework (2026-03-09)

The Phase 1 prompt, when written, must incorporate the Instrument Admission Framework (`docs/plans/2026-03-09-instrument-admission-design.md`). Key impacts:

- **Do not reference "121 instruments"** — the instrument universe is dynamic, determined by adapter discovery and admission criteria
- **Phase 1 gate criterion changed:** "≥ 20 instruments at signal_eligible" → "≥ 12 instruments at signal_eligible, all passing admission criteria"
- **Phase 0 seed is minimal:** BTC, ETH, SOL spot + `__market__` only. No Coinalyze list dump.
- **Phase 1 step 10 ("First instrument tier promotion run")** is the mechanism that populates the universe based on data quality evidence
- **Metric count at Phase 1 completion:** 85 (not 82)
- **Source catalog:** 11 v1 sources (not 15 — reference sources are not cataloged per v3.1)

---

*This handoff is mechanical. All decisions are made. No judgment calls remain. Apply edits, verify, done.*
