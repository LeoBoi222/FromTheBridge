# RESULT_ID_E3 — DeFiLlama Yields API Extension
## FromTheBridge — Empire Architecture v2.0

**Result ID:** E3
**Document type:** Design Result — ready for design index update and cc execution prompt generation
**Date:** 2026-03-06
**Status:** Confirmed. All assumptions resolved by architect review (2026-03-06).
**Companion result:** RESULT_ID_E4_CFTC_COT (separate document)
**Supersedes:** Prior proxy methodology for `defi.lending.utilization_rate`
**Phase gate:** Phase 1 pre-condition — adapter extension must be built before Phase 1 gate can close

---

## 1. Purpose and Scope

This document is the complete, execution-ready design record for the DeFiLlama
Yields API extension to the FromTheBridge lakehouse. It covers:

- The DeFiLlama `/yields` API surface (endpoint, schema, cadence, pagination,
  filtering strategy)
- All catalog changes: 3 new metric_catalog rows, 1 metric_catalog UPDATE,
  4 new metric_lineage rows
- The adapter extension specification for the existing DeFiLlama adapter
- Dagster asset registration (4 assets, 1 op pattern)
- Backfill strategy
- Validation pre-flight checks required before Phase 1 build begins
- Signal relevance and Phase 2 feature engineering inputs
- Known gap closure record

This document does NOT cover CFTC COT (see RESULT_ID_E4). It does NOT cover
DeFiLlama endpoints already in production (/protocols, /charts, /stablecoins).
Those remain unchanged.

---

## 2. Pre-Build Validation Required

Before the Phase 1 execution prompt for this extension is issued, one API
check must be run and its result recorded here. This is a pre-condition,
not a Phase 1 task.

### 2.1 Validation: `utilization` field unit

**Risk:** The adapter spec assumes `utilization` is already in decimal form
(0.0–1.0). If DeFiLlama returns it as a percent (e.g., 74.0), the adapter
must apply ÷100 — same as the APY fields.

**Command:**
```bash
curl -s "https://yields.llama.fi/pools" | python3 -c "
import json, sys
pools = json.load(sys.stdin)['data']
aave = [p for p in pools
        if p.get('project') == 'aave-v3'
        and p.get('chain') == 'Ethereum'
        and p.get('symbol') == 'USDC']
if aave:
    p = aave[0]
    print('apyBase:    ', p.get('apyBase'))
    print('apyBorrow:  ', p.get('apyBorrow'))
    print('utilization:', p.get('utilization'))
    print('apyReward:  ', p.get('apyReward'))
else:
    print('Pool not found — check project/chain/symbol filter')
"
```

**Expected result:** `utilization` in range 0.0–1.0 (e.g., 0.74). `apyBase`
and `apyBorrow` in percent range (e.g., 3.21, 4.12) — confirming ÷100 is
required for APY fields and NOT required for utilization.

**Decision gate:** Record actual output here before Phase 1 build prompt.
If `utilization` > 1.0 is observed, update Section 5.3 (unit normalization)
to apply ÷100 to utilization as well, and update the metric_catalog
`expected_range_high` to 100.0 (reverting to percent storage), OR apply ÷100
and keep decimal storage. Architect decision required if mismatch found.

**Result (to be filled):** `________________`

---

## 3. Decisions Locked by Architect Review (2026-03-06)

All assumptions from the E3 spec are resolved. The following are locked
decisions, not open questions. No Phase 1 execution prompt may re-open these.

| ID | Decision | Locked Value |
|---|---|---|
| E3-A1 | `instrument_id` for pool metrics | Underlying asset canonical symbol (`USDC`, `WETH`, etc.) for assets in v1 instruments catalog; `__market__` sentinel for exotic underlying tokens |
| E3-A2 | `defi.lending.borrow_apy` nullability | `is_nullable = true`. Supply-only pools structurally lack a borrow side; null is correct, not an error. Avoids dead_letter spam for structurally incomplete pools. |
| E3-A3 | `observed_at` assignment | Request time, truncated to the 12h cadence boundary. Single HTTP request; no per-pool historical fetch. Idempotent via ReplacingMergeTree deduplication on same `observed_at`. |
| — | Dagster asset fan-out | Four metrics collected in one HTTP request to `/pools`. Single op, four asset materializations. Eliminates four redundant requests per run. |
| — | `utilization_rate` handling | Existing catalog row updated (methodology field only). No new row. No rename. Canonical name `defi.lending.utilization_rate` preserved per locked schema immutability decision. |
| — | `metric_lineage` for utilization | New lineage INSERT linking `defi.lending.utilization_rate` to `defillama` source. Previously this metric had no lineage row (proxy computed in Marts). Direct source lineage is now required. |

---

## 4. API Surface Reference

### 4.1 Primary Endpoint

```
GET https://yields.llama.fi/pools
```

- Returns all lending pools across all protocols and chains in a single response
- No authentication required
- No API key required
- No pagination — single JSON response
- Response envelope: `{ "status": "ok", "data": [ ... ] }`
- Approximate response size: 5,000–7,000 pool objects as of early 2026
- Rate limit: Not formally published. Public API. One request per adapter
  run (12h cadence) produces negligible load.

### 4.2 Historical Endpoint (backfill only)

```
GET https://yields.llama.fi/chart/{pool_id}
```

- `pool_id` is the `pool` UUID field from the `/pools` response
- Returns time-series array: `[ { "timestamp": "...", "apy": ..., "tvlUsd": ... } ]`
- `apyBase`, `apyBorrow`, `utilization` may not be present in all historical
  records — apply null handling
- Historical depth: approximately 2–3 years for major Aave/Compound pools
- Used for initial backfill only. Incremental collection uses `/pools`.

### 4.3 Response Schema (per pool object)

| Field | Type | Unit | Adapter handling |
|---|---|---|---|
| `pool` | string (UUID) | — | Pool identifier. Used for backfill historical fetch. Not stored in Silver. |
| `chain` | string | — | Filter: `IN ('Ethereum', 'Arbitrum')`. Not stored. |
| `project` | string | — | Filter: see Section 4.4. Not stored directly; used for `instrument_id` resolution and logging. |
| `symbol` | string | — | Underlying asset symbol. Used for `instrument_id` resolution. |
| `apyBase` | float | percent (e.g., 3.21) | ÷100 → `defi.lending.supply_apy` (decimal 0.0321) |
| `apyBorrow` | float or null | percent | ÷100 → `defi.lending.borrow_apy`. Null for supply-only pools. Write null; `is_nullable = true`. |
| `apyReward` | float or null | percent | ÷100 → `defi.lending.reward_apy`. Null when no active reward program. |
| `utilization` | float or null | decimal 0.0–1.0 (verify — see Section 2.1) | → `defi.lending.utilization_rate`. No unit conversion if decimal confirmed. |
| `tvlUsd` | float | USD | Not a new metric. DeFiLlama /protocols already provides protocol-level TVL. |
| `apy` | float | percent | Total APY = apyBase + apyReward. Not collected separately — derivable in Marts. |
| `stablecoin` | boolean | — | Not stored. Used for pool filtering heuristics if needed. |
| `ilRisk` | string | — | Not collected. Not signal-relevant at v1. |
| `exposure` | string | — | Not collected. |
| `underlyingTokens` | array | — | Not stored. Used only for instrument_id resolution edge cases. |
| `rewardTokens` | array | — | Not stored. |

### 4.4 Pool Filter Scope (v1)

The adapter filters client-side after fetching all pools. Only records matching
ALL of the following criteria produce observations:

**Protocol filter:**
```python
V1_PROJECTS = {
    'aave-v3',
    'aave-v2',
    'compound-v3',
    'compound-v2',
    'curve',
}
```

**Chain filter:**
```python
V1_CHAINS = {'Ethereum', 'Arbitrum'}
```

**Asset filter (for `instrument_id` resolution):**
```python
V1_ASSET_SYMBOLS = {
    'USDC', 'USDT', 'DAI', 'WETH', 'WBTC', 'ETH', 'BTC',
}
```

Pools where `symbol` is not in `V1_ASSET_SYMBOLS` will have
`instrument_id = '__market__'` and are still collected (the protocol-level
APY signal is valid regardless of underlying) but cannot be joined to
instrument-specific features in Marts without additional resolution logic.

**Approximate pool count after filtering:** 20–40 records per run.
DeFiLlama pool IDs are stable — the same pool UUID appears in every response
until the pool is deprecated.

### 4.5 Protocol Deprecation Handling

If a protocol disappears from the `/pools` response (e.g., Compound v2 winding
down), the adapter must NOT error. Affected metrics transition to
`METRIC_UNAVAILABLE` null state in the feature layer. The adapter continues
running; absence of new observations for a previously active pool is handled
by staleness propagation in forge_compute. No dead_letter entry is written
for a structurally absent pool — that is `SOURCE_STALE`, not a validation
failure.

---

## 5. Adapter Extension Specification

### 5.1 Integration Point

The existing DeFiLlama adapter (`adapters/defillama/adapter.py` or equivalent)
is extended by adding one new method: `collect_yields()`. The existing methods
(`collect_protocols()`, `collect_tvl()`, `collect_stablecoins()`) are not
modified. The new method is registered as a new collection step in the
adapter's run sequence.

The adapter class already implements auth (none required for DeFiLlama),
rate limiting, and observability infrastructure. The `collect_yields()` method
inherits all of these — it does not re-implement them.

### 5.2 Method Signature

```python
def collect_yields(self) -> CollectionResult:
    """
    Fetches https://yields.llama.fi/pools and writes observations for
    v1-scoped protocol/chain/asset combinations.

    Returns CollectionResult with:
      - observations_written: int
      - observations_rejected: int
      - pools_fetched: int
      - pools_in_scope: int
      - errors: List[str]
    """
```

### 5.3 Responsibility-by-Responsibility Implementation Contract

**Responsibility 1 — Auth:**
No authentication required for `yields.llama.fi`. No API key injection.
No `Authorization` header. The adapter should not include any auth headers
for this endpoint even if the base DeFiLlama adapter uses them for other
endpoints.

**Responsibility 2 — Rate limiting:**
Single HTTP GET request per adapter run. 12h cadence. No rate limiting
logic required beyond the standard adapter run guard (prevent concurrent
runs via Dagster's asset materialization lock).

**Responsibility 3 — Pagination:**
None. `/pools` returns all data in one response. Client-side filtering
is applied after the single fetch.

**Responsibility 4 — Schema normalization:**

```python
METRIC_FIELD_MAP = {
    'defi.lending.supply_apy':       ('apyBase',    lambda v: v / 100.0),
    'defi.lending.borrow_apy':       ('apyBorrow',  lambda v: v / 100.0 if v is not None else None),
    'defi.lending.reward_apy':       ('apyReward',  lambda v: v / 100.0 if v is not None else None),
    'defi.lending.utilization_rate': ('utilization', lambda v: float(v)),
    # NOTE: if validation check (Section 2.1) reveals utilization is in percent,
    # update lambda to: lambda v: v / 100.0
}
```

**Responsibility 5 — Timestamp normalization:**

```python
import datetime

def get_observed_at(cadence_hours: int = 12) -> datetime.datetime:
    """
    Returns current UTC time truncated to the cadence boundary.
    For cadence_hours=12: truncates to midnight or noon UTC.
    All runs within the same 12h window produce the same observed_at.
    ReplacingMergeTree handles idempotent re-writes.
    """
    now = datetime.datetime.utcnow()
    boundary_seconds = cadence_hours * 3600
    truncated = (int(now.timestamp()) // boundary_seconds) * boundary_seconds
    return datetime.datetime.utcfromtimestamp(truncated)
```

**Responsibility 6 — Unit normalization:**

| Source field | Raw unit | Stored unit | Conversion |
|---|---|---|---|
| `apyBase` | percent (3.21) | decimal (0.0321) | ÷ 100 |
| `apyBorrow` | percent | decimal | ÷ 100 |
| `apyReward` | percent | decimal | ÷ 100 |
| `utilization` | decimal 0–1 (confirm) | decimal 0–1 | none (confirm Section 2.1) |

**Responsibility 7 — Validation (per-observation, not per-batch):**

```python
VALIDATION_RULES = {
    'defi.lending.supply_apy': {
        'range': (0.0, 5.0),
        'nullable': False,
        'extreme_threshold': 5.0,   # flag > 500% annualized for review
    },
    'defi.lending.borrow_apy': {
        'range': (0.0, 5.0),
        'nullable': True,
        'extreme_threshold': 5.0,
    },
    'defi.lending.reward_apy': {
        'range': (0.0, 10.0),
        'nullable': True,
        'extreme_threshold': 10.0,
    },
    'defi.lending.utilization_rate': {
        'range': (0.0, 1.0),
        'nullable': False,
        'extreme_threshold': None,  # no extreme flag — range is bounded
    },
}
```

A single pool's null `borrow_apy` does NOT cause the entire batch to fail.
The other three metric observations for that pool are written normally.

A null `utilization` IS a dead_letter event (`NULL_VIOLATION`) because
`is_nullable = false`. A pool with null utilization from Aave or Compound
is anomalous and warrants investigation.

**Responsibility 8 — Extreme value handling:**

Values within type constraints but outside `expected_range` are flagged
with rejection_code `EXTREME_VALUE_PENDING_REVIEW` and written to
`forge.dead_letter`, NOT silently dropped or written to `forge.observations`.
Manual triage required (e.g., a new protocol with 600% APY on launch day
may be legitimate or may be a DeFiLlama data error).

**Responsibility 9 — Idempotency:**

```python
# Observations keyed on:
# (metric_id, instrument_id, observed_at)
# where observed_at is the truncated 12h boundary timestamp.

# Re-running the adapter within the same 12h window:
# - Same observed_at is generated
# - Same value (DeFiLlama data is stable within 12h)
# - ReplacingMergeTree deduplicates on background merge
# - No dead_letter events generated for duplicate writes
# - data_version = 1 for all initial writes; increment only on
#   deliberate revision (not expected for DeFiLlama yields)
```

**Responsibility 10 — Observability:**

One `forge.collection_events` row written on every run, whether successful
or failed. Fields:

```python
collection_event = {
    'source_id':             <defillama UUID from source_catalog>,
    'started_at':            <run start timestamp>,
    'completed_at':          <run end timestamp>,
    'status':                'completed' | 'failed' | 'partial',
    'observations_written':  <count>,
    'observations_rejected': <count>,
    'metrics_covered':       [
        'defi.lending.supply_apy',
        'defi.lending.borrow_apy',
        'defi.lending.utilization_rate',
        'defi.lending.reward_apy',
    ],
    'instruments_covered':   <list of instrument_ids written to>,
    'error_detail':          <None or error message>,
    'metadata': {
        'pools_fetched':     <total /pools response count>,
        'pools_in_scope':    <count after v1 filter>,
        'endpoint':          'https://yields.llama.fi/pools',
    }
}
```

### 5.4 `instrument_id` Resolution Logic

```python
V1_INSTRUMENT_MAP = {
    # DeFiLlama symbol → ClickHouse instrument_id
    'USDC':  'USDC',
    'USDT':  'USDT',
    'DAI':   'DAI',
    'WETH':  'WETH',
    'ETH':   'WETH',   # DeFiLlama may use 'ETH' for wrapped ETH pools
    'WBTC':  'WBTC',
    'BTC':   'WBTC',   # DeFiLlama may use 'BTC' for WBTC pools
}

def resolve_instrument_id(pool: dict) -> str:
    symbol = pool.get('symbol', '').upper()
    # Strip common suffixes (e.g., 'USDC.e' on Arbitrum)
    base_symbol = symbol.split('.')[0].split('-')[0]
    return V1_INSTRUMENT_MAP.get(base_symbol, '__market__')
```

**Note on `ETH` vs `WETH`:** DeFiLlama uses both `ETH` and `WETH` for pools
whose underlying token is Wrapped Ether. The adapter normalizes both to
`WETH` to match the canonical instrument in `forge.instruments`. If `WETH`
is not a registered instrument in v1 (only `ETH` is), update this mapping.
Pre-flight check required before Phase 1 build — verify `forge.instruments`
for `WETH` and `WBTC` existence.

**Note on Arbitrum symbols:** Bridged tokens on Arbitrum sometimes carry
suffixes (e.g., `USDC.e` for bridged USDC vs native USDC). The
`split('.')[0]` in the resolver handles this. Verify against actual
DeFiLlama responses for Arbitrum pools.

### 5.5 Backfill Strategy

Backfill uses the per-pool historical endpoint `/chart/{pool_id}`. This is
a Phase 1 task, not a pre-condition.

```python
V1_BACKFILL_POOLS = {
    # pool_id (UUID from /pools)  : (project, chain, symbol)
    # Populate this map by running /pools once and extracting UUIDs
    # for the v1-scoped pools. Hard-code UUIDs in adapter config.
}

# Backfill for each pool:
# GET https://yields.llama.fi/chart/{pool_id}
# Response: { "data": [ { "timestamp": "...", "apy": ...,
#                         "apyBase": ..., "apyBorrow": ...,
#                         "tvlUsd": ... } ] }
#
# NOTE: /chart does not always include 'utilization' in historical records.
# For historical rows missing 'utilization', write NULL to Silver and
# set is_nullable temporarily true for backfill, OR skip utilization
# for historical rows only. Architect decision required at backfill time.
#
# Estimated depth:
#   Aave v3 Ethereum: ~2022-03-16 forward (~4 years)
#   Compound v3 Ethereum: ~2022-08-26 forward (~3.5 years)
#   ingested_at = backfill run timestamp (PIT-correct)
```

---

## 6. Catalog Changes — Complete Specification

### 6.1 New Metric: `defi.lending.supply_apy`

```sql
INSERT INTO forge.metric_catalog (
    canonical_name,
    domain,
    subdomain,
    description,
    unit,
    value_type,
    granularity,
    cadence_hours,
    staleness_threshold_hours,
    expected_range_low,
    expected_range_high,
    is_nullable,
    methodology,
    signal_pillar,
    status
) VALUES (
    'defi.lending.supply_apy',
    'defi',
    'lending',
    'Annualized supply (lending) APY for a lending pool. Base yield only, '
    'excluding protocol incentive rewards. Expressed as decimal (0.0321 = 3.21%).',
    'decimal',
    'numeric',
    'per_protocol',
    12,
    36,
    0.0,
    5.0,
    false,
    'DeFiLlama /yields endpoint, apyBase field. Converted from percent to '
    'decimal at ingestion (÷100). instrument_id = underlying asset canonical '
    'symbol for v1 assets (USDC, USDT, DAI, WETH, WBTC); __market__ sentinel '
    'for exotic underlying tokens outside v1 instruments catalog. '
    'Pool scope: aave-v3, aave-v2, compound-v3, compound-v2, curve on '
    'Ethereum and Arbitrum.',
    'defi_health',
    'active'
);
```

**collection_priority:** 2 (high)
**signal_eligible:** true

---

### 6.2 New Metric: `defi.lending.borrow_apy`

```sql
INSERT INTO forge.metric_catalog (
    canonical_name,
    domain,
    subdomain,
    description,
    unit,
    value_type,
    granularity,
    cadence_hours,
    staleness_threshold_hours,
    expected_range_low,
    expected_range_high,
    is_nullable,
    methodology,
    signal_pillar,
    status
) VALUES (
    'defi.lending.borrow_apy',
    'defi',
    'lending',
    'Annualized borrow APY for a lending pool. Variable rate. Null for '
    'supply-only pools (e.g., Curve liquidity pools used as lending rate '
    'reference). Expressed as decimal.',
    'decimal',
    'numeric',
    'per_protocol',
    12,
    36,
    0.0,
    5.0,
    true,   -- supply-only pools structurally lack borrow side; null is correct
    'DeFiLlama /yields endpoint, apyBorrow field. Converted from percent to '
    'decimal at ingestion (÷100). Null written when apyBorrow absent from '
    'pool record. is_nullable=true to avoid dead_letter noise for structurally '
    'incomplete pools.',
    'defi_health',
    'active'
);
```

**collection_priority:** 2 (high)
**signal_eligible:** true

**Feature engineering note:** The spread `borrow_apy − supply_apy` is a
Category C derived feature computed in forge_compute (Marts layer). It is
not stored in Silver. A null `borrow_apy` propagates as
`METRIC_UNAVAILABLE` for the spread feature.

---

### 6.3 New Metric: `defi.lending.reward_apy`

```sql
INSERT INTO forge.metric_catalog (
    canonical_name,
    domain,
    subdomain,
    description,
    unit,
    value_type,
    granularity,
    cadence_hours,
    staleness_threshold_hours,
    expected_range_low,
    expected_range_high,
    is_nullable,
    methodology,
    signal_pillar,
    status
) VALUES (
    'defi.lending.reward_apy',
    'defi',
    'lending',
    'Annualized APY from protocol incentive token rewards on a lending pool. '
    'Excludes base supply APY. Zero or null when no active reward program.',
    'decimal',
    'numeric',
    'per_protocol',
    12,
    36,
    0.0,
    10.0,   -- 1000% annualized; token incentive programs can be extreme at launch
    true,
    'DeFiLlama /yields endpoint, apyReward field. Converted from percent to '
    'decimal at ingestion (÷100). Null written when field absent.',
    'defi_health',
    'active'
);
```

**collection_priority:** 3 (standard)
**signal_eligible:** false

**Rationale for signal_eligible = false:** High reward APY distorts signal
interpretation without a regime-aware incentive context. A pool with 400%
APY from token incentives is not the same risk signal as a pool with 400%
APY from genuine borrowing demand. signal_eligible promoted to true at v1.1
when incentive-regime feature handling is designed in thread_3.

---

### 6.4 Existing Metric Update: `defi.lending.utilization_rate`

This is an UPDATE, not an INSERT. The canonical name is immutable and is
preserved. Only the `methodology` field changes.

```sql
-- Pre-flight: verify current methodology before updating
SELECT canonical_name, methodology, signal_pillar, status
FROM forge.metric_catalog
WHERE canonical_name = 'defi.lending.utilization_rate';
-- Must return exactly one row with status = 'active'.

-- Execute update
UPDATE forge.metric_catalog
SET methodology = (
    'DeFiLlama /yields endpoint, utilization field (decimal 0.0–1.0). '
    'Direct pool-level utilization from Phase 1. '
    'Replaces v1 proxy (borrow_tvl / supply_tvl from /protocols endpoint). '
    'Proxy methodology archived in decision log: RESULT_ID_E3. '
    'Canonical name unchanged per schema immutability rule. '
    'Pool scope: aave-v3, aave-v2, compound-v3, compound-v2 on Ethereum and Arbitrum. '
    'instrument_id = underlying asset canonical symbol or __market__ sentinel.'
)
WHERE canonical_name = 'defi.lending.utilization_rate';

-- Verify
SELECT canonical_name, methodology
FROM forge.metric_catalog
WHERE canonical_name = 'defi.lending.utilization_rate';
```

**What does NOT change on this row:** domain, subdomain, description, unit,
value_type, granularity, cadence_hours, staleness_threshold_hours,
expected_range_low (0.0), expected_range_high (1.0), is_nullable (false),
signal_pillar (defi_health), status (active), signal_eligible (true).

---

### 6.5 New Metric Lineage Rows (all four metrics)

```sql
-- supply_apy → defillama
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'defi.lending.supply_apy'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'defillama'),
    true,
    'DeFiLlama /yields apyBase field. Phase 1 E3 extension.';

-- borrow_apy → defillama
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'defi.lending.borrow_apy'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'defillama'),
    true,
    'DeFiLlama /yields apyBorrow field. Null for supply-only pools. Phase 1 E3.';

-- utilization_rate → defillama  (NEW lineage row — metric existed, lineage did not)
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'defi.lending.utilization_rate'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'defillama'),
    true,
    'DeFiLlama /yields utilization field. Direct source replacing v1 proxy. '
    'Phase 1 E3. Proxy (borrow_tvl/supply_tvl from /protocols) retired.';

-- reward_apy → defillama
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'defi.lending.reward_apy'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'defillama'),
    true,
    'DeFiLlama /yields apyReward field. Null when no active incentive program. Phase 1 E3.';
```

---

## 7. Dagster Asset Registration

### 7.1 Asset Architecture

All four yields metrics are collected in a **single HTTP request** to `/pools`.
The Dagster implementation uses one op that performs the fetch and validation,
fanned out to four asset materializations. This is the correct pattern —
it avoids four redundant HTTP requests and matches how DeFiLlama data is
actually structured (one response, multiple metrics).

```
defillama_yields_fetch (op)
    │
    ├── defillama__defi_lending_supply_apy        (asset)
    ├── defillama__defi_lending_borrow_apy        (asset)
    ├── defillama__defi_lending_utilization_rate  (asset)
    └── defillama__defi_lending_reward_apy        (asset)
```

### 7.2 Asset Definitions

| Asset key | metric_id | source_id | Upstream dependency | Freshness policy |
|---|---|---|---|---|
| `defillama__defi_lending_supply_apy` | `defi.lending.supply_apy` | `defillama` | `defillama__defi_aggregate_tvl` (existing) | `FreshnessPolicy(maximum_lag_minutes=780)` |
| `defillama__defi_lending_borrow_apy` | `defi.lending.borrow_apy` | `defillama` | `defillama__defi_lending_supply_apy` (co-produced) | `FreshnessPolicy(maximum_lag_minutes=780)` |
| `defillama__defi_lending_utilization_rate` | `defi.lending.utilization_rate` | `defillama` | `defillama__defi_lending_supply_apy` (co-produced) | `FreshnessPolicy(maximum_lag_minutes=780)` |
| `defillama__defi_lending_reward_apy` | `defi.lending.reward_apy` | `defillama` | `defillama__defi_lending_supply_apy` (co-produced) | `FreshnessPolicy(maximum_lag_minutes=780)` |

**Freshness policy rationale:** 780 minutes = 13 hours. Cadence is 12h; the
freshness window is cadence + 1h grace period.

**Upstream dependency note:** The three co-produced assets
(borrow_apy, utilization_rate, reward_apy) declare `supply_apy` as upstream.
This is a Dagster modeling convenience — it does not mean supply_apy is
computed first. All four materialize in one op execution. The dependency
edge communicates to Dagster that they share the same collection op.

### 7.3 Schedule

The yields collection op is added to the existing DeFiLlama collection
schedule. DeFiLlama currently runs on a 12h schedule. The yields fetch
adds one HTTP request to each run. No schedule change required.

```python
defillama_schedule = ScheduleDefinition(
    job=defillama_job,
    cron_schedule="0 0,12 * * *",  # midnight and noon UTC
    execution_timezone="UTC",
)
```

### 7.4 Dagster Code Server Restart

After inserting new metric_lineage rows, the Dagster code server must be
restarted to rebuild the asset graph from the updated catalog:

```bash
docker compose restart empire_dagster_code
# Verify new assets appear in the Dagster UI at http://192.168.68.11:3010
```

---

## 8. Signal Relevance and Phase 2 Feature Engineering Inputs

### 8.1 Signal Mapping

| metric_id | EDSx Pillar | ML Model | Feature Category | Signal Rationale |
|---|---|---|---|---|
| `defi.lending.supply_apy` | Liquidity/Flow (EDSx-03) | DeFi Stress | C (derived — lending spread) | Supply APY compression signals capital flooding into lending; spike signals stress-driven withdrawal. Primary input to lending spread feature. |
| `defi.lending.borrow_apy` | Liquidity/Flow (EDSx-03) | DeFi Stress | C (derived — lending spread) | Borrow APY elevation signals leverage demand and liquidity constraint. Paired with supply_apy to compute spread. |
| `defi.lending.utilization_rate` | Liquidity/Flow (EDSx-03) | DeFi Stress | A (raw) + C (z-score, momentum) | Core DeFi health metric. Utilization > 0.85 historically precedes stress events and rate spikes. Now direct from source — no longer proxy-degraded. |
| `defi.lending.reward_apy` | (deferred) | (deferred) | (deferred) | Informational only at v1. High reward APY distorts DeFi health signal without incentive-regime context. |

### 8.2 Phase 2 Feature Catalog Inputs

The following feature catalog entries should be created during Phase 2 when
these metrics first reach forge_compute. They are documented here to ensure
the Phase 2 feature engineering spec is aware of them.

**Feature: `defi.lending.borrow_supply_spread`**
- Type: Category C (derived ratio)
- Computation: `borrow_apy − supply_apy` per (protocol, instrument, timestamp)
- Null handling: `METRIC_UNAVAILABLE` if either input is null
- Signal use: Primary DeFi stress indicator. Spread compression below 50bps
  signals excess liquidity; spread expansion above 200bps signals stress.

**Feature: `defi.lending.utilization_zscore_52w`**
- Type: Category C (rolling z-score)
- Computation: z-score of `utilization_rate` over 52-week rolling window
- Minimum history: 52 weeks × 2 observations/day = 728 observations.
  `INSUFFICIENT_HISTORY` null state until met.
- Signal use: Z-score normalizes utilization across protocols for cross-
  sectional comparison.

**Feature: `defi.lending.utilization_momentum_4w`**
- Type: Category C (momentum)
- Computation: `utilization_rate[t] − utilization_rate[t−4w]`
- Signal use: Rising momentum toward 0.85+ is the leading indicator for
  stress events. Declining momentum from elevated levels = relief signal.

**Note on proxy retirement:** The v1 proxy feature
(`borrow_tvl / supply_tvl`) that currently populates `defi.lending.utilization_rate`
is retired in Phase 1 when the yields adapter goes live. The Phase 2 feature
catalog entries above replace the proxy-derived z-score and momentum features.
Backfilled historical utilization (from `/chart/{pool_id}`) will be used to
satisfy the 52-week minimum history requirement before feature computation begins.

---

## 9. Known Gap Closure Record

### Gap Closed: `defi.lending.utilization_rate` proxy

| Field | Value |
|---|---|
| Gap ID | `defi.lending.utilization_rate` proxy |
| Prior design_index entry | "Proxy: borrow/supply TVL ratio. v1.1 milestone — Aave/Compound subgraph adapter" |
| Prior thread_4 entry | "Proxy: borrow/supply TVL ratio from DeFiLlama. Stored under canonical name. Methodology field documents proxy. v1.1 milestone — Aave/Compound subgraph adapter." |
| Resolution | DeFiLlama /yields endpoint provides direct pool utilization rate. Subgraph integration is not required. Resolved in Phase 1 via E3 adapter extension. |
| New status | **Resolved. Remove from Known Gaps table.** |
| Design records | RESULT_ID_E3 (this document), E3+E4 expansion spec (2026-03-06) |
| Proxy methodology archived in | `methodology` field update on `defi.lending.utilization_rate` catalog row (Section 6.4) |

**design_index.md update — exact replacement:**

```
REMOVE FROM Known Gaps table:
| `defi.lending.utilization_rate` | Proxy: borrow/supply TVL ratio | v1.1 milestone |

ADD TO design_index.md resolved section or remove entirely:
[Resolved Phase 1 — E3: Direct utilization from DeFiLlama /yields. Subgraph not required.]
```

**thread_4_data_universe.md update — exact replacement:**

```
REMOVE FROM Known Gaps table:
| `defi.lending.utilization_rate` | Proxy: borrow/supply TVL ratio from DeFiLlama.
  Stored under canonical name. Methodology field documents proxy. |
  v1.1 milestone — Aave/Compound subgraph adapter |

REPLACE WITH (or remove if resolved-gaps section does not exist):
[Resolved Phase 1. E3: DeFiLlama /yields direct utilization. See RESULT_ID_E3.]
```

---

## 10. Pre-Flight Checks for Phase 1 Build Prompt

The Phase 1 execution prompt for this adapter extension must include the
following pre-flight checks. These must pass before any code is written.

```sql
-- PF-1: Verify defillama source exists in catalog
SELECT source_id, canonical_name, tier, redistribution
FROM forge.source_catalog
WHERE canonical_name = 'defillama';
-- Expected: 1 row, redistribution = true (or NULL if not yet set)

-- PF-2: Verify existing utilization_rate metric exists
SELECT metric_id, canonical_name, methodology, status
FROM forge.metric_catalog
WHERE canonical_name = 'defi.lending.utilization_rate';
-- Expected: 1 row, status = 'active'

-- PF-3: Verify new metrics do NOT already exist (idempotency guard)
SELECT canonical_name FROM forge.metric_catalog
WHERE canonical_name IN (
    'defi.lending.supply_apy',
    'defi.lending.borrow_apy',
    'defi.lending.reward_apy'
);
-- Expected: 0 rows

-- PF-4: Verify no lineage row for utilization_rate → defillama yet
SELECT ml.lineage_id
FROM forge.metric_lineage ml
JOIN forge.metric_catalog mc ON ml.metric_id = mc.metric_id
JOIN forge.source_catalog sc ON ml.source_id = sc.source_id
WHERE mc.canonical_name = 'defi.lending.utilization_rate'
  AND sc.canonical_name = 'defillama';
-- Expected: 0 rows

-- PF-5: Verify WETH and WBTC exist as instruments (for instrument_id resolution)
SELECT canonical_symbol, instrument_type, is_active
FROM forge.instruments
WHERE canonical_symbol IN ('WETH', 'WBTC', 'USDC', 'USDT', 'DAI');
-- Expected: rows for each. If WETH absent, adapter must map ETH pools
-- to 'ETH' instead. If WBTC absent, map BTC pools to 'BTC' instead.
-- Update V1_INSTRUMENT_MAP in adapter accordingly.
```

```bash
# PF-6: Confirm /pools response structure (run before Phase 1 build begins)
# See Section 2.1 for full command. Record utilization value range here.
# utilization observed value: ________________
# Confirmed decimal (not percent): YES / NO
```

All six pre-flight checks must pass and PF-6 result must be recorded before
the cc execution prompt is issued.

---

## 11. Document Control

| Field | Value |
|---|---|
| Result ID | E3 |
| Companion | RESULT_ID_E4_CFTC_COT |
| Phase target | Phase 1 (adapter extension, backfill) |
| Gate dependency | Phase 1 gate criterion: DeFiLlama yields adapter live, ≥1 Silver observation row for `defi.lending.supply_apy` |
| Blocking items | Pre-build validation (Section 2.1) must be completed and recorded |
| Documents to update | `design_index.md` (Known Gaps), `thread_4_data_universe.md` (Known Gaps, metric catalog), `thread_5_collection.md` (collector inventory, DeFiLlama adapter spec) |
| SQL bundle status | Ready to execute. All statements in Sections 6.1–6.5. |
| Architect sign-off | Confirmed 2026-03-06 |
| Next action | Run pre-flight checks → record PF-6 result → issue Phase 1 cc execution prompt |

---

*Document authored: 2026-03-06. All decisions locked. No open assumptions.*
*Changes to locked decisions require architect approval and a new RESULT_ID document.*
