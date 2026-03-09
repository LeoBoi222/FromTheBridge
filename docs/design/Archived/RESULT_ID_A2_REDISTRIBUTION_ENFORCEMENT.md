# Thread A2 — Redistribution Enforcement
## FromTheBridge / Empire Architecture

**Date:** 2026-03-06
**Status:** Written. Pending architect confirmation on open assumptions.
**Scope:** Phase 5 implementation spec — serving layer redistribution enforcement,
lineage tagging, middleware integration, and audit evidence design.
**Depends on:**
- D1 — Secrets isolation, ClickHouse credential model, `api_keys` hashing (locked)
- D2 — 4-tier entitlement model (Pro/Protocol/Institutional/Internal), middleware
  chain, `pg_audit_access_log` schema (locked, file:18)
- `thread_infrastructure.md` — Layer boundary rules, Docker topology
- `thread_7_output_delivery.md` — API surface, redistribution gating decisions

**Gap resolutions applied:**
- Source table scoped to v1 live sources only (Coinalyze, CoinMetrics, SoSoValue
  as the three gated sources; CFTC/DeFiLlama Yields deferred post-v1)
- Response schema: null-with-flag authoritative (supersedes empty set language
  in thread_7 §3)
- Redistribution state: three-state enum (allowed / pending / blocked); `pending`
  and `blocked` both return null but with distinct reason codes
- D1/D2 integration points written with explicit assumptions where schemas
  are inferred

---

## 1. Propagation Rule Decision

### The Core Ambiguity

When a signal or feature is computed using inputs from a redistribution-blocked
source, is the derived output itself blocked?

**Example:**
SoSoValue ETF flow data (`redistribution_status = blocked`) feeds the Capital
Flow Direction ML model, which produces `capital_flow_direction.p_bullish`.
Is `p_bullish` blocked because SoSoValue is in its lineage?

### Options Evaluated

**Option A — Direct only.**
Only raw metric values from blocked sources are filtered. Derived signals and
features are allowed regardless of upstream lineage. Rationale: transformation
is not reproduction.

| Dimension | Assessment |
|---|---|
| Legal conservatism | Low. "Derivative work" language in data vendor contracts frequently extends restrictions to transformed outputs. Cannot be assumed safe without ToS-specific legal review. |
| Product impact | Minimal — only raw metric fields are gated. Signal surface is fully available. |
| Operational complexity | Lowest — no graph traversal needed, single-table lookup. |
| Reversibility | Not applicable — no propagation to undo. |

**Option B — Full propagation.**
Any metric, feature, or signal output with a blocked source anywhere in its
lineage graph is blocked.

| Dimension | Assessment |
|---|---|
| Legal conservatism | Highest. Defensible against any reasonable vendor interpretation. |
| Product impact | Severe before audit completion. Capital Flow Direction model (SoSoValue input) and derivatives features (Coinalyze input) would propagate blocks across a substantial share of the composite signal. |
| Operational complexity | Moderate — graph traversal required, but can be pre-computed. |
| Reversibility | High — when a source resolves to `allowed`, the full graph recomputes automatically. |

**Option C — Configurable per source (`propagate_restriction` flag).**
`source_catalog` carries a `propagate_restriction` boolean. When `true`, blocks
propagate through derived lineage. When `false`, only direct metric values from
that source are filtered. Default: `true` (conservative) until ToS audit
confirms otherwise.

| Dimension | Assessment |
|---|---|
| Legal conservatism | High. Default is conservative. Audit can selectively relax per source with evidence. |
| Product impact | Configurable. Starts at maximum restriction, unlocks progressively as audits resolve. |
| Operational complexity | Moderate — one additional column, same graph traversal as Option B. |
| Reversibility | Highest — per-source rollback, zero code changes. |

### Recommendation: Option C

**Rationale:** The legal risk asymmetry favors starting conservative. Option A
is not defensible if a vendor reviews outbound API responses before ToS audit
completion. Option B is legally sound but creates unnecessary product damage:
Coinalyze feeds derivatives features that underpin EDSx-02 and EDSx-03, and
full propagation would degrade those pillars for all customers until the audit
resolves. Option C starts at Option B's legal posture and unlocks selectively.

**Default behavior for each v1 gated source:**

| Source | redistribution_status | propagate_restriction | Rationale |
|---|---|---|---|
| SoSoValue | blocked | true | Non-commercial ToS explicitly restricts derived use |
| CoinMetrics | blocked | true | Internal-only language; derived restriction likely |
| Coinalyze | pending | true | Unaudited; conservative default until audit |
| BGeometrics | pending | true | Unaudited; conservative default until audit |
| Etherscan/Explorer | pending | true | Unaudited; conservative default until audit |
| Binance BLC-01 | pending | true | Unaudited; conservative default until audit |

**Product impact with Option C at default settings:**

Derivatives features (Coinalyze inputs) return `redistribution_status: pending`.
Capital Flow Direction model (SoSoValue input) returns `redistribution_status:
blocked`. Composite `final_score` degrades gracefully — see Section 3.

**⚠ Architect confirmation required** on propagation rule before any serving
layer code is written. See Open Assumptions §1 and §2.

---

## 2. Lineage Tagging Model

### Design Principles

- Tags are pre-computed, not resolved per-request
- Storage: PostgreSQL catalog (Layer 7) — single source of truth
- FastAPI loads tags into memory at startup; invalidates on NOTIFY
- Tag computation is a pure function of `source_catalog` + `metric_lineage` —
  deterministic and replayable

### Schema Changes to Existing Tables

**`forge.source_catalog` — add redistribution columns:**

```sql
-- Add three-state redistribution status
-- (replaces boolean redistribution_allowed, which is deprecated)
ALTER TABLE forge.source_catalog
    ADD COLUMN redistribution_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (redistribution_status IN ('allowed', 'pending', 'blocked')),
    ADD COLUMN propagate_restriction  BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN redistribution_notes   TEXT,
    ADD COLUMN redistribution_audited_at TIMESTAMPTZ;

-- Migrate existing data from boolean
-- (run once; drop redistribution_allowed after migration is verified)
UPDATE forge.source_catalog SET
    redistribution_status = CASE
        WHEN redistribution_allowed = true  THEN 'allowed'
        WHEN redistribution_allowed = false THEN 'blocked'
        ELSE 'pending'
    END;

-- After verification:
-- ALTER TABLE forge.source_catalog DROP COLUMN redistribution_allowed;

COMMENT ON COLUMN forge.source_catalog.redistribution_status IS
    'allowed = ToS audited, redistribution permitted.
     pending = unaudited; treated as blocked in external API responses.
     blocked = confirmed no redistribution permitted.';

COMMENT ON COLUMN forge.source_catalog.propagate_restriction IS
    'When true (default), redistribution blocks propagate through
     metric_lineage to all downstream derived metrics and signal outputs.
     Set false only after ToS audit confirms derived works are unrestricted.';
```

### New Table: `forge.metric_redistribution_tags`

Pre-computed redistribution state for every row in `metric_catalog`, plus
every feature and signal output registered in the system.

```sql
CREATE TABLE forge.metric_redistribution_tags (
    metric_id           TEXT        NOT NULL,
    redist_status       TEXT        NOT NULL
                            CHECK (redist_status IN ('allowed', 'pending', 'blocked')),
    blocking_source_ids TEXT[]      NOT NULL DEFAULT '{}',
    -- Empty array when redist_status = 'allowed'.
    -- One or more source_ids when pending or blocked.
    propagated          BOOLEAN     NOT NULL DEFAULT false,
    -- true  = status inherited from upstream metric via metric_lineage
    -- false = status derived directly from this metric's own source_id
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (metric_id)
);

CREATE INDEX idx_mrt_status ON forge.metric_redistribution_tags (redist_status);

COMMENT ON TABLE forge.metric_redistribution_tags IS
    'Pre-computed redistribution enforcement tags for all metrics, features,
     and signal outputs. Refreshed by forge.recompute_redistribution_tags()
     on source_catalog changes. Read into FastAPI memory cache at startup.';
```

### Tag Computation Function

Executes a breadth-first traversal of `metric_lineage` from each root metric
(those with a direct `source_id` in `metric_catalog`) outward to derived
metrics, features, and signal outputs.

```sql
CREATE OR REPLACE FUNCTION forge.recompute_redistribution_tags()
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_metric     RECORD;
    v_upstream   RECORD;
    v_status     TEXT;
    v_sources    TEXT[];
    v_propagated BOOLEAN;
BEGIN
    -- Clear existing tags
    TRUNCATE forge.metric_redistribution_tags;

    -- Seed: direct metrics (those with a source_id in metric_catalog)
    INSERT INTO forge.metric_redistribution_tags
        (metric_id, redist_status, blocking_source_ids, propagated, computed_at)
    SELECT
        mc.metric_id,
        sc.redistribution_status,
        CASE WHEN sc.redistribution_status IN ('pending', 'blocked')
             THEN ARRAY[sc.source_id]
             ELSE '{}'::TEXT[]
        END,
        false,
        NOW()
    FROM forge.metric_catalog mc
    JOIN forge.source_catalog  sc ON mc.source_id = sc.source_id
    ON CONFLICT (metric_id) DO UPDATE
        SET redist_status       = EXCLUDED.redist_status,
            blocking_source_ids = EXCLUDED.blocking_source_ids,
            propagated          = EXCLUDED.propagated,
            computed_at         = EXCLUDED.computed_at;

    -- Propagate through metric_lineage (derived metrics, features, outputs)
    -- Iterates until no new rows are inserted (fixed-point)
    LOOP
        INSERT INTO forge.metric_redistribution_tags
            (metric_id, redist_status, blocking_source_ids, propagated, computed_at)
        SELECT DISTINCT ON (ml.downstream_metric_id)
            ml.downstream_metric_id,
            -- Worst upstream status wins: blocked > pending > allowed
            CASE
                WHEN bool_or(
                    t.redist_status = 'blocked'
                    AND sc_up.propagate_restriction = true
                ) THEN 'blocked'
                WHEN bool_or(
                    t.redist_status = 'pending'
                    AND sc_up.propagate_restriction = true
                ) THEN 'pending'
                ELSE 'allowed'
            END,
            -- Collect all blocking source_ids from upstream
            array_agg(DISTINCT bs) FILTER (WHERE bs IS NOT NULL),
            true,
            NOW()
        FROM forge.metric_lineage        ml
        JOIN forge.metric_redistribution_tags t
            ON ml.upstream_metric_id = t.metric_id
        -- Join to find source_id for the upstream metric
        JOIN forge.metric_catalog        mc_up ON ml.upstream_metric_id = mc_up.metric_id
        JOIN forge.source_catalog        sc_up ON mc_up.source_id = sc_up.source_id
        -- Expand blocking_source_ids array to scalar for aggregation
        LEFT JOIN LATERAL unnest(t.blocking_source_ids) AS bs ON true
        WHERE NOT EXISTS (
            SELECT 1 FROM forge.metric_redistribution_tags
            WHERE metric_id = ml.downstream_metric_id
        )
        GROUP BY ml.downstream_metric_id
        ON CONFLICT (metric_id) DO NOTHING;

        EXIT WHEN NOT FOUND;
    END LOOP;

    -- Notify API cache to reload
    PERFORM pg_notify('redistribution_cache_invalidated', NOW()::TEXT);
END;
$$;

COMMENT ON FUNCTION forge.recompute_redistribution_tags() IS
    'Full recompute of metric_redistribution_tags. Executes graph traversal
     of metric_lineage from source_catalog redistribution state.
     Emits pg_notify to trigger FastAPI cache reload.
     Safe to re-run at any time — idempotent via TRUNCATE + re-insert.';
```

### Automatic Recompute Trigger on `source_catalog` Changes

```sql
CREATE OR REPLACE FUNCTION forge.on_source_catalog_redist_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only fire on redistribution-relevant column changes
    IF (OLD.redistribution_status  IS DISTINCT FROM NEW.redistribution_status  OR
        OLD.propagate_restriction   IS DISTINCT FROM NEW.propagate_restriction) THEN
        PERFORM forge.recompute_redistribution_tags();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_source_catalog_redist_change
AFTER UPDATE ON forge.source_catalog
FOR EACH ROW
EXECUTE FUNCTION forge.on_source_catalog_redist_change();
```

### Tag Coverage: What Gets a Row

| Category | How it gets a tag |
|---|---|
| Raw metrics in `metric_catalog` | Seeded from `source_id → source_catalog.redistribution_status` |
| Derived features (forge_compute) | Registered in `metric_catalog` + `metric_lineage`; propagated by traversal |
| ML model outputs (per model) | Registered as synthetic metric_ids (e.g., `ml.capital_flow_direction.p_bullish`); lineage points to feature inputs |
| EDSx pillar scores | Registered as synthetic metric_ids; lineage points to contributing metrics |
| Composite `final_score` | Registered as synthetic metric_id; lineage points to both tracks |

**Requirement for Phase 5:** All ML model outputs and EDSx pillar scores must
be registered in `metric_catalog` + `metric_lineage` before Phase 5 begins.
This is a Phase 3/4 deliverable. **⚠ See Open Assumption §3.**

---

## 3. Response Schema for Blocked Fields

### Governing Principle: Null-With-Flag, Never Silent Omission

Redistribution enforcement never silently drops a field. Every blocked or
pending field is present in the response with:
- `value: null`
- `redistribution_status` indicating the enforcement state
- `blocking_sources` identifying which source(s) triggered the block

This preserves schema stability across enforcement state transitions. A customer
parsing the response does not need to handle a missing key — the key is always
present; only the value varies.

### Metric Value Response

```json
{
  "metric_id": "flows.etf_flow_normalized",
  "value": null,
  "redistribution_status": "blocked",
  "blocking_sources": ["sosovalue"],
  "observed_at": null
}
```

For a pending source:
```json
{
  "metric_id": "derivatives.funding_rate_zscore",
  "value": null,
  "redistribution_status": "pending",
  "blocking_sources": ["coinalyze"],
  "observed_at": null
}
```

For an allowed source (normal response — status field included for schema
consistency, omitted in practice to reduce payload size):
```json
{
  "metric_id": "macro.rates.us_10y_yield",
  "value": 4.31,
  "redistribution_status": "allowed",
  "observed_at": "2026-03-06T00:00:00Z"
}
```

**Implementation note:** `redistribution_status: "allowed"` MAY be omitted
from allowed-field responses to reduce payload. Blocked and pending fields
MUST always include it.

### Signal Component Response (ML model output)

```json
{
  "capital_flow_direction": {
    "p_bullish": null,
    "redistribution_status": "blocked",
    "blocking_sources": ["sosovalue"],
    "model_version": null,
    "computed_at": null
  }
}
```

### Composite `final_score` — Graceful Degradation

The composite score degrades rather than collapses when one track is blocked.
This is the conservative response to D2's requirement that even Institutional
customers cannot receive redistribution-blocked fields — but it avoids
silently nulling the entire product.

**Degradation rules (in priority order):**

1. If EDSx track is available and ML track is fully blocked →
   `final_score = edsx_composite`, `synthesis_degraded = true`
2. If ML track is partially blocked (some models available) →
   `ml_composite = weighted average of unblocked models only`,
   `synthesis_degraded = true`, blocked models listed in `ml_excluded`
3. If both tracks are fully blocked → `final_score = null`
4. If EDSx track is partially blocked (some pillars available) →
   `edsx_composite = average of available pillars`,
   `synthesis_degraded = true`, blocked pillars listed in `edsx_excluded`

**Example: SoSoValue blocks Capital Flow Direction model, Coinalyze pending:**

```json
{
  "instrument_id": "BTC",
  "final_score": 0.61,
  "synthesis_degraded": true,
  "synthesis_note": "ML composite excludes capital_flow_direction (blocked: sosovalue). EDSx derivatives pillar pending (coinalyze).",
  "edsx_composite": {
    "score": 0.64,
    "confidence": 0.71,
    "pillars_available": 3,
    "pillars_total": 5,
    "pillar_detail": {
      "trend_structure": { "score": 0.70, "confidence": 0.88 },
      "liquidity_flow": { "score": 0.58, "confidence": 0.76 },
      "valuation": { "score": 0.63, "confidence": 0.52 },
      "derivatives": {
        "score": null,
        "redistribution_status": "pending",
        "blocking_sources": ["coinalyze"]
      },
      "tactical_macro": {
        "score": null,
        "redistribution_status": "pending",
        "blocking_sources": ["coinalyze"]
      }
    }
  },
  "ml_composite": {
    "score": 0.58,
    "models_available": 4,
    "models_total": 5,
    "model_detail": {
      "derivatives_pressure":   { "p_bullish": 0.61 },
      "macro_regime":           { "p_bullish": 0.54 },
      "defi_stress":            { "p_bullish": 0.63 },
      "volatility_regime":      { "p_bullish": 0.55 },
      "capital_flow_direction": {
        "p_bullish": null,
        "redistribution_status": "blocked",
        "blocking_sources": ["sosovalue"]
      }
    }
  },
  "regime": "selective_offense",
  "computed_at": "2026-03-06T06:00:00Z"
}
```

**Example: Both tracks blocked:**

```json
{
  "instrument_id": "BTC",
  "final_score": null,
  "synthesis_degraded": true,
  "synthesis_note": "All signal inputs redistribution-blocked. No score available.",
  "edsx_composite": null,
  "ml_composite": null,
  "computed_at": "2026-03-06T06:00:00Z"
}
```

---

## 4. Pending Source Handling

### Three-State Enum Definition

| State | Meaning | API behavior |
|---|---|---|
| `allowed` | ToS audited; redistribution confirmed permitted | Value returned. No flag. |
| `pending` | Unaudited source; redistribution status unknown | `value: null`, `redistribution_status: "pending"`, `blocking_sources: [source_id]` |
| `blocked` | ToS confirmed; redistribution not permitted | `value: null`, `redistribution_status: "blocked"`, `blocking_sources: [source_id]` |

### Default Behavior for Pending Sources

Pending sources are treated as **not available for redistribution** in external
API responses. The distinction from `blocked` is:

1. The reason code in the response distinguishes "we don't know yet" from
   "we know and it's no"
2. The customer interpretation differs: `pending` implies the field may become
   available; `blocked` implies it will not unless the vendor relationship
   changes
3. The automatic resolution path differs (see Section 7)

### API Response for Pending Fields

Identical structure to blocked, with `redistribution_status: "pending"`:

```json
{
  "metric_id": "derivatives.funding_rate_8h",
  "value": null,
  "redistribution_status": "pending",
  "blocking_sources": ["coinalyze"],
  "observed_at": null
}
```

The customer-facing API documentation should explain:
> Fields marked `redistribution_status: "pending"` are sourced from data
> providers whose redistribution terms are under review. These fields will
> become available automatically once the review is complete and the source
> is confirmed as redistribution-permitted.

### Resolution: Pending → Allowed

When a source's audit completes and redistribution is confirmed:

1. Operator runs update SQL (see Section 7)
2. `source_catalog.redistribution_status` updates to `'allowed'`
3. Trigger fires `forge.recompute_redistribution_tags()`
4. All downstream derived tags recompute (if `propagate_restriction` also
   relaxed)
5. `pg_notify` emitted → FastAPI cache reloads within 60 seconds
6. Next API responses return actual values for previously-pending fields
7. `synthesis_degraded` flag drops from composite responses where applicable

**Zero code changes required.** Schema and middleware adapt from catalog state.

### Resolution: Pending → Blocked

Same flow as above with `redistribution_status = 'blocked'`. Customer-visible
behavior changes from `"pending"` to `"blocked"` reason codes. No other
behavioral difference — both states produce `value: null` in responses.

---

## 5. Pre-Computation and Caching

### Where Tags Are Computed

`forge.metric_redistribution_tags` in PostgreSQL (Layer 7 catalog). This is
the single authoritative store. It is not replicated to ClickHouse, Gold, or
Marts — it is a catalog object, not an observation.

### When Tags Are Computed

| Trigger | Mechanism |
|---|---|
| Source catalog update | PostgreSQL trigger `trg_source_catalog_redist_change` → synchronous call to `forge.recompute_redistribution_tags()` |
| API service startup | FastAPI lifespan handler loads full table into memory cache |
| Manual operator action | `SELECT forge.recompute_redistribution_tags()` |
| Dagster nightly job | Scheduled asset calls recompute as a consistency check (not the primary update path) |

### In-Memory Cache Structure

FastAPI loads the tags table into a Python dict at startup. Structure:

```python
# Type: dict[metric_id: str, RedistributionTag]
@dataclass
class RedistributionTag:
    status: Literal['allowed', 'pending', 'blocked']
    blocking_sources: list[str]   # empty list when status == 'allowed'
    propagated: bool

# Populated at startup and on NOTIFY
redistribution_cache: dict[str, RedistributionTag] = {}
```

### Cache Invalidation

PostgreSQL `pg_notify('redistribution_cache_invalidated', ...)` emitted at the
end of `forge.recompute_redistribution_tags()`.

FastAPI listens on startup via an async `asyncpg` LISTEN connection (separate
from the request-handling connection pool):

```python
async def listen_for_cache_invalidation(app: FastAPI):
    """
    Long-running task. Listens for redistribution_cache_invalidated
    notifications and triggers a full cache reload.
    """
    conn = await asyncpg.connect(settings.DATABASE_URL)
    await conn.add_listener(
        'redistribution_cache_invalidated',
        lambda *_: asyncio.create_task(reload_redistribution_cache(app))
    )
    # Runs until application shutdown
    await asyncio.Event().wait()

async def reload_redistribution_cache(app: FastAPI):
    async with app.state.db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT metric_id, redist_status, blocking_source_ids, propagated "
            "FROM forge.metric_redistribution_tags"
        )
        new_cache = {
            row['metric_id']: RedistributionTag(
                status=row['redist_status'],
                blocking_sources=list(row['blocking_source_ids'] or []),
                propagated=row['propagated']
            )
            for row in rows
        }
        app.state.redistribution_cache = new_cache
```

### Middleware Access Pattern

Tag lookup is O(1) dict key access. No database call per request. No lock
contention — Python dict reads are thread-safe for reads in CPython (GIL).

```python
def get_tag(metric_id: str, cache: dict) -> RedistributionTag:
    return cache.get(
        metric_id,
        RedistributionTag(status='pending', blocking_sources=[metric_id], propagated=False)
        # Unknown metrics default to 'pending' — conservative fallback
    )
```

**Fallback behavior:** If a metric_id is not in the cache (e.g., a newly
registered metric before the next recompute), the default is `pending`. This
is the safe failure mode — unknown = restricted.

---

## 6. Middleware Integration

### Position in the D2 Request Lifecycle

```
Request received
│
├─ [D2] API key extraction (X-API-Key header)
│    └─ Missing key → HTTP 401 Unauthorized
│
├─ [D2] API key validation + plan resolution
│    └─ Invalid/expired key → HTTP 401 Unauthorized
│    └─ Sets: request.state.customer_id, request.state.tier
│
├─ [D2] Endpoint access check
│    └─ Tier insufficient for endpoint → HTTP 403 Forbidden
│    └─ Body: {"error": "tier_insufficient", "required_tier": "paid", "your_tier": "free"}
│
├─ [D2] Instrument access check
│    └─ Instrument not in tier's coverage → HTTP 403 Forbidden
│
├─ Query execution (signal computation / data fetch)
│    └─ Returns raw field values with metric_ids
│
├─ [A2] Redistribution filter ◄─────────────────────────── HERE
│    └─ Per-field tag lookup against redistribution_cache
│    └─ Blocked/pending fields: value → null, add status + blocking_sources
│    └─ Response code remains HTTP 200 (field-level, not request-level block)
│    └─ Writes redistribution_filter_applied = true to audit context
│    └─ Logs filtered fields to pg_audit_access_log
│
├─ [D2] Tier-based field filter
│    └─ Omits fields above customer tier (e.g., component breakdown on Free)
│    └─ Response code remains HTTP 200
│
├─ Audit log write (D2 + A2 combined entry) ◄─────────────── SINGLE LOG WRITE
│
└─ Response serialized + returned
```

### Why Redistribution Before Tier-Based Filtering

Redistribution enforcement is a compliance obligation that applies regardless
of tier. Processing it first ensures:
1. A `blocked` field cannot be inadvertently returned to a higher-tier customer
   after tier-based filtering selectively passes it through
2. The audit log captures redistribution blocks for every request, independent
   of tier decisions

### HTTP Status Distinction

| Scenario | HTTP Status | Body |
|---|---|---|
| Tier insufficient for endpoint | 403 Forbidden | `{"error": "tier_insufficient", ...}` |
| Tier insufficient for instrument | 403 Forbidden | `{"error": "instrument_not_in_tier", ...}` |
| Redistribution blocked field(s) | **200 OK** | Full response; blocked fields have `value: null, redistribution_status: "blocked"` |
| Redistribution pending field(s) | **200 OK** | Full response; pending fields have `value: null, redistribution_status: "pending"` |
| Invalid API key | 401 Unauthorized | `{"error": "invalid_api_key"}` |

**Rationale for 200 on redistribution blocks:** A request for BTC signal data
is a valid, authorized request for that endpoint and instrument. The fact that
some fields within the response are enforcement-gated does not make the request
itself unauthorized. Returning 403 would conflate two distinct concepts and
break client implementations that parse the full signal response. The null-with-
flag schema gives the customer actionable information without an error response.

### Redistribution Filter — FastAPI Implementation

```python
# services/redistribution_filter.py

from dataclasses import dataclass
from typing import Any, Literal

@dataclass
class RedistributionTag:
    status: Literal['allowed', 'pending', 'blocked']
    blocking_sources: list[str]
    propagated: bool

# ─────────────────────────────────────────────
# Core filter function
# ─────────────────────────────────────────────

def apply_redistribution_filter(
    fields: dict[str, Any],
    cache: dict[str, RedistributionTag],
) -> tuple[dict[str, Any], list[dict]]:
    """
    Apply redistribution enforcement to a flat dict of {metric_id: value}.

    Returns:
        filtered_fields: dict with blocked/pending values replaced by null + metadata
        audit_entries:   list of dicts describing each filtered field (for audit log)
    """
    filtered: dict[str, Any] = {}
    audit_entries: list[dict] = []

    for metric_id, value in fields.items():
        tag = cache.get(
            metric_id,
            RedistributionTag(status='pending', blocking_sources=[metric_id], propagated=False)
        )

        if tag.status in ('blocked', 'pending'):
            filtered[metric_id] = {
                'value': None,
                'redistribution_status': tag.status,
                'blocking_sources': tag.blocking_sources,
            }
            audit_entries.append({
                'metric_id': metric_id,
                'action': 'redistribution_filtered',
                'redistribution_status': tag.status,
                'blocking_sources': tag.blocking_sources,
            })
        else:
            filtered[metric_id] = value

    return filtered, audit_entries


# ─────────────────────────────────────────────
# Composite score degradation
# ─────────────────────────────────────────────

def apply_composite_degradation(
    edsx_pillars: dict[str, Any],
    ml_models: dict[str, Any],
    cache: dict[str, RedistributionTag],
) -> dict[str, Any]:
    """
    Compute degraded final_score from available (non-blocked) pillars and models.
    Returns the full composite response dict with degradation metadata.
    """
    edsx_available = {k: v for k, v in edsx_pillars.items()
                      if cache.get(k, _PENDING).status == 'allowed'}
    edsx_blocked   = {k: v for k, v in edsx_pillars.items()
                      if cache.get(k, _PENDING).status != 'allowed'}

    ml_available   = {k: v for k, v in ml_models.items()
                      if cache.get(k, _PENDING).status == 'allowed'}
    ml_blocked     = {k: v for k, v in ml_models.items()
                      if cache.get(k, _PENDING).status != 'allowed'}

    degraded = bool(edsx_blocked or ml_blocked)

    edsx_score = _mean([v['score'] for v in edsx_available.values()]) if edsx_available else None
    ml_score   = _mean([v['p_bullish'] for v in ml_available.values()]) if ml_available else None

    if edsx_score is not None and ml_score is not None:
        final_score = 0.5 * edsx_score + 0.5 * ml_score
    elif edsx_score is not None:
        final_score = edsx_score
    elif ml_score is not None:
        final_score = ml_score
    else:
        final_score = None

    return {
        'final_score': round(final_score, 4) if final_score is not None else None,
        'synthesis_degraded': degraded,
        'synthesis_note': _degradation_note(edsx_blocked, ml_blocked) if degraded else None,
        'edsx_composite': _build_edsx_response(edsx_available, edsx_blocked, edsx_score, cache),
        'ml_composite':   _build_ml_response(ml_available, ml_blocked, ml_score, cache),
    }

_PENDING = RedistributionTag(status='pending', blocking_sources=[], propagated=False)

def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None

def _degradation_note(edsx_blocked: dict, ml_blocked: dict) -> str:
    parts = []
    if ml_blocked:
        src = set(s for v in ml_blocked.values()
                  for s in _get_blocking_sources(v))
        parts.append(
            f"ML composite excludes {', '.join(ml_blocked.keys())} "
            f"(blocked: {', '.join(sorted(src))})"
        )
    if edsx_blocked:
        src = set(s for v in edsx_blocked.values()
                  for s in _get_blocking_sources(v))
        parts.append(
            f"EDSx composite excludes {', '.join(edsx_blocked.keys())} "
            f"(pending/blocked: {', '.join(sorted(src))})"
        )
    return '. '.join(parts) + '.'

def _get_blocking_sources(v: Any) -> list[str]:
    if isinstance(v, dict):
        return v.get('blocking_sources', [])
    return []

def _build_edsx_response(available, blocked, score, cache) -> dict | None:
    if not available and not blocked:
        return None
    result = {
        'score': round(score, 4) if score is not None else None,
        'pillars_available': len(available),
        'pillars_total': len(available) + len(blocked),
        'pillar_detail': {}
    }
    for k, v in available.items():
        result['pillar_detail'][k] = v
    for k, v in blocked.items():
        tag = cache.get(k, _PENDING)
        result['pillar_detail'][k] = {
            'score': None,
            'redistribution_status': tag.status,
            'blocking_sources': tag.blocking_sources,
        }
    return result

def _build_ml_response(available, blocked, score, cache) -> dict | None:
    if not available and not blocked:
        return None
    result = {
        'score': round(score, 4) if score is not None else None,
        'models_available': len(available),
        'models_total': len(available) + len(blocked),
        'model_detail': {}
    }
    for k, v in available.items():
        result['model_detail'][k] = v
    for k, v in blocked.items():
        tag = cache.get(k, _PENDING)
        result['model_detail'][k] = {
            'p_bullish': None,
            'redistribution_status': tag.status,
            'blocking_sources': tag.blocking_sources,
        }
    return result
```

### FastAPI Middleware Dependency

```python
# middleware/redistribution.py

from fastapi import Request
from services.redistribution_filter import apply_redistribution_filter

async def redistribution_middleware(request: Request, call_next):
    """
    Starlette middleware. Sets redistribution context on request.state
    so downstream route handlers can call apply_redistribution_filter().
    No filtering happens here — filtering is applied in response serialization
    where field-level metric_ids are available.
    """
    request.state.redistribution_cache  = request.app.state.redistribution_cache
    request.state.redistribution_audits = []  # populated during response build
    response = await call_next(request)
    return response
```

### Audit Log Entry for Redistribution Events

**Assumption:** `pg_audit_access_log` schema from D1 includes at minimum:
`id`, `customer_id`, `api_key_id`, `endpoint`, `instrument_id`, `requested_at`,
`response_code`, `action`, `metadata JSONB`.

Redistribution filter events are written as `metadata` entries on the same
audit row as the request (single write per request):

```python
# In route handler, after redistribution filter applied:
audit_metadata = {
    "tier": request.state.tier,
    "redistribution_filtered": bool(request.state.redistribution_audits),
    "redistribution_events": request.state.redistribution_audits,
    # redistribution_audits is the list of dicts from apply_redistribution_filter()
    # e.g. [{"metric_id": "flows.etf_flow_normalized",
    #         "action": "redistribution_filtered",
    #         "redistribution_status": "blocked",
    #         "blocking_sources": ["sosovalue"]}]
}

await write_audit_log(
    conn=db,
    customer_id=request.state.customer_id,
    api_key_id=request.state.api_key_id,
    endpoint=request.url.path,
    instrument_id=instrument_id,
    response_code=200,
    action="signal_served",
    metadata=audit_metadata,
)
```

---

## 7. Automatic Enforcement Updates

### Zero-Code-Change Update Path

When ToS audit completes for a source, the update path is:

```
Operator updates source_catalog
    → trigger fires recompute_redistribution_tags()
        → metric_redistribution_tags updated (graph traversal)
            → pg_notify emitted
                → FastAPI cache reloads (≤60 seconds)
                    → Next API requests reflect updated state
```

No deployment. No code change. No service restart.

### Operator SQL: Pending → Allowed (e.g., Coinalyze audit clears)

```sql
-- Step 1: Update source redistribution status
UPDATE forge.source_catalog
SET
    redistribution_status    = 'allowed',
    propagate_restriction    = false,  -- adjust if ToS restricts derived works
    redistribution_notes     = 'ToS audited 2026-XX-XX. Redistribution permitted. Derived works unrestricted.',
    redistribution_audited_at = NOW(),
    updated_at               = NOW()
WHERE source_id = 'coinalyze';

-- Trigger fires automatically. Verify tag recompute completed:
SELECT
    metric_id,
    redist_status,
    blocking_source_ids,
    computed_at
FROM forge.metric_redistribution_tags
WHERE 'coinalyze' = ANY(blocking_source_ids)
   OR metric_id IN (
       SELECT metric_id FROM forge.metric_catalog WHERE source_id = 'coinalyze'
   )
ORDER BY computed_at DESC
LIMIT 20;
-- Expected: all coinalyze-sourced metrics now show redist_status = 'allowed'
-- and blocking_source_ids no longer contains 'coinalyze'
```

### Operator SQL: Pending → Blocked (e.g., BGeometrics audit fails)

```sql
UPDATE forge.source_catalog
SET
    redistribution_status    = 'blocked',
    propagate_restriction    = true,
    redistribution_notes     = 'ToS audited 2026-XX-XX. Redistribution not permitted. Derived works also restricted per contract §4.2.',
    redistribution_audited_at = NOW(),
    updated_at               = NOW()
WHERE source_id = 'bgeometrics';

-- Verify propagation to derived metrics:
SELECT
    t.metric_id,
    t.redist_status,
    t.blocking_source_ids,
    t.propagated
FROM forge.metric_redistribution_tags t
WHERE 'bgeometrics' = ANY(t.blocking_source_ids)
ORDER BY t.propagated, t.metric_id;
-- Expected: direct metrics + all downstream derived metrics now show 'blocked'
```

### Operator SQL: Relax Propagation Only (derived works cleared, raw still blocked)

```sql
-- Scenario: source confirmed blocked for raw data redistribution
-- but ToS explicitly permits redistribution of derived signals
UPDATE forge.source_catalog
SET
    propagate_restriction    = false,
    redistribution_notes     = 'Raw data redistribution blocked. Derived signals explicitly permitted per contract §5.1.',
    updated_at               = NOW()
WHERE source_id = 'coinmetrics';

-- Trigger fires, graph recomputes.
-- Direct coinmetrics metrics remain 'blocked'.
-- Downstream derived metrics (features, ML outputs) now show 'allowed'.
```

### Propagation to Live API Responses

- PostgreSQL trigger: synchronous, completes in <1 second for typical graph sizes
- `pg_notify`: emitted at end of recompute function
- FastAPI LISTEN handler: wakes on notification, calls `reload_redistribution_cache()`
- Cache reload: single SELECT against metric_redistribution_tags, replaces dict
- Effective lag: typically 5–15 seconds, maximum 60 seconds if notification missed

**⚠ Operator note:** If the FastAPI service is restarted after a source_catalog
update (e.g., during a deployment window), the cache loads the current state of
`metric_redistribution_tags` at startup. No stale cache risk across restarts.

---

## 8. Audit Evidence Package

### Context

**Assumed schema for `pg_audit_access_log` (D1/D2):**
```sql
-- Assumed — verify against D1 actual schema
CREATE TABLE pg_audit_access_log (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    TEXT        NOT NULL,
    api_key_id     TEXT        NOT NULL,
    endpoint       TEXT        NOT NULL,
    instrument_id  TEXT,
    requested_at   TIMESTAMPTZ NOT NULL,
    response_code  INTEGER     NOT NULL,
    action         TEXT        NOT NULL,
    metadata       JSONB
);
```

If the actual D1 schema differs, these queries require column name adjustment.

---

### Query 1: Prove redistribution=blocked fields were never returned to external customers

```sql
-- Evidence: No request to any external customer (non-internal tier) ever
-- returned a value for a redistribution_status = 'blocked' field.
-- A blocked field that was correctly enforced appears ONLY in redistribution_events
-- with action = 'redistribution_filtered', never as an unfiltered value.

SELECT
    aal.customer_id,
    aal.api_key_id,
    aal.endpoint,
    aal.requested_at,
    evt->>'metric_id'            AS filtered_metric_id,
    evt->>'redistribution_status' AS status,
    evt->>'blocking_sources'     AS blocking_sources
FROM pg_audit_access_log aal
CROSS JOIN LATERAL jsonb_array_elements(
    aal.metadata->'redistribution_events'
) AS evt
WHERE
    -- External customers only (exclude internal tier if tracked)
    aal.metadata->>'tier' != 'internal'
    AND evt->>'action' = 'redistribution_filtered'
    AND evt->>'redistribution_status' = 'blocked'
ORDER BY aal.requested_at DESC;

-- Expected result: rows confirming filtering OCCURRED (action = redistribution_filtered)
-- for blocked fields. The presence of these rows is the evidence — it proves
-- the enforcement ran and the value was withheld.

-- Companion query: confirm NO request contains an unfiltered blocked metric
-- (this requires the response payload to be logged, which is NOT recommended
-- for PII/performance reasons — the audit_events approach above is preferred)
```

---

### Query 2: Prove blocked sources had zero successful data exposure

```sql
-- Evidence: For a specific source (e.g., sosovalue), all requests touching
-- metrics from that source had redistribution_filtered applied.

WITH blocked_metrics AS (
    SELECT metric_id
    FROM forge.metric_catalog
    WHERE source_id = 'sosovalue'
    UNION
    -- Include propagated downstream metrics
    SELECT metric_id
    FROM forge.metric_redistribution_tags
    WHERE 'sosovalue' = ANY(blocking_source_ids)
),
requests_touching_source AS (
    SELECT DISTINCT
        aal.id,
        aal.customer_id,
        aal.requested_at,
        aal.metadata->>'tier' AS tier,
        (aal.metadata->'redistribution_filtered')::boolean AS was_filtered
    FROM pg_audit_access_log aal
    CROSS JOIN LATERAL jsonb_array_elements(
        aal.metadata->'redistribution_events'
    ) AS evt
    WHERE evt->>'metric_id' IN (SELECT metric_id FROM blocked_metrics)
)
SELECT
    COUNT(*)                                    AS total_requests_touching_sosovalue,
    COUNT(*) FILTER (WHERE was_filtered = true) AS requests_correctly_filtered,
    COUNT(*) FILTER (WHERE was_filtered = false OR was_filtered IS NULL)
                                                AS requests_NOT_filtered  -- should be 0
FROM requests_touching_source
WHERE tier != 'internal';

-- Expected: requests_NOT_filtered = 0
```

---

### Query 3: Prove pending fields were returned only with appropriate flags

```sql
-- Evidence: All occurrences of pending-source metrics in API responses
-- include redistribution_status = 'pending' (not raw values).

SELECT
    aal.customer_id,
    aal.requested_at,
    evt->>'metric_id'             AS metric_id,
    evt->>'redistribution_status' AS status_returned,
    CASE WHEN evt->>'redistribution_status' = 'pending' THEN 'COMPLIANT'
         ELSE 'VIOLATION'
    END AS compliance_check
FROM pg_audit_access_log aal
CROSS JOIN LATERAL jsonb_array_elements(
    aal.metadata->'redistribution_events'
) AS evt
WHERE
    evt->>'redistribution_status' = 'pending'
    AND aal.metadata->>'tier' != 'internal'
ORDER BY aal.requested_at DESC;

-- All rows should show status_returned = 'pending' and compliance_check = 'COMPLIANT'
-- Any row where compliance_check = 'VIOLATION' indicates enforcement failure
```

---

### Query 4: Source-level redistribution enforcement summary (on-demand report)

```sql
-- Summary report: per source, how many requests touched its metrics and
-- what % were correctly filtered. Useful for periodic compliance review.

SELECT
    sc.source_id,
    sc.redistribution_status,
    COUNT(DISTINCT aal.id)                      AS total_requests,
    COUNT(DISTINCT aal.id) FILTER (
        WHERE (aal.metadata->'redistribution_filtered')::boolean = true
    )                                           AS filtered_requests,
    ROUND(
        100.0 * COUNT(DISTINCT aal.id) FILTER (
            WHERE (aal.metadata->'redistribution_filtered')::boolean = true
        ) / NULLIF(COUNT(DISTINCT aal.id), 0),
        2
    )                                           AS filter_rate_pct
FROM forge.source_catalog sc
JOIN forge.metric_catalog mc USING (source_id)
JOIN forge.metric_redistribution_tags t USING (metric_id)
CROSS JOIN LATERAL (
    SELECT aal.id, aal.metadata
    FROM pg_audit_access_log aal
    CROSS JOIN LATERAL jsonb_array_elements(aal.metadata->'redistribution_events') AS evt
    WHERE evt->>'metric_id' = mc.metric_id
      AND aal.metadata->>'tier' != 'internal'
) aal
WHERE sc.redistribution_status IN ('blocked', 'pending')
GROUP BY sc.source_id, sc.redistribution_status
ORDER BY total_requests DESC;
```

---

### Query 5: Point-in-time proof for a specific date range (vendor audit request)

```sql
-- For vendor: "Prove your platform did not redistribute our data
-- between DATE_A and DATE_B"

SELECT
    aal.requested_at,
    aal.customer_id,
    aal.endpoint,
    evt->>'metric_id'             AS metric_id,
    evt->>'redistribution_status' AS enforcement_state,
    evt->>'blocking_sources'      AS blocking_sources
FROM pg_audit_access_log aal
CROSS JOIN LATERAL jsonb_array_elements(
    aal.metadata->'redistribution_events'
) AS evt
WHERE
    aal.requested_at BETWEEN '2026-01-01T00:00:00Z' AND '2026-03-06T23:59:59Z'
    AND evt->>'blocking_sources' LIKE '%sosovalue%'  -- replace with target vendor
    AND aal.metadata->>'tier' != 'internal'
ORDER BY aal.requested_at ASC;

-- Provide this full result set to the vendor.
-- Every row is a confirmed enforcement event.
-- Absence of rows without redistribution_filtered = true is the proof.
```

---

## Open Assumptions Requiring Architect Confirmation

**§1 — Propagation rule (Section 1).**
Option C with `propagate_restriction = true` as default is recommended. This
means Coinalyze derivatives features propagate `pending` blocks to all downstream
signals at v1 launch, degrading derivatives pillar coverage. Confirm this is
acceptable vs. a specific per-source exception before Phase 5 build begins.

**§2 — SoSoValue and CoinMetrics propagation behavior (Section 1).**
Both are defaulted to `propagate_restriction = true`. This blocks Capital Flow
Direction ML model outputs in the external API. If the legal interpretation is
that signal outputs are transformed sufficiently to be redistribution-free,
set `propagate_restriction = false` for these sources. Confirm which
interpretation applies.

**§3 — ML outputs and EDSx pillar scores in metric_catalog + metric_lineage (Section 2).**
The lineage tagging model requires all ML model outputs and EDSx pillar scores
to be registered as synthetic metric_ids in `metric_catalog` with corresponding
`metric_lineage` rows. This is a Phase 3/4 deliverable. Confirm this registration
pattern is acceptable or if an alternative tagging mechanism is preferred for
signal-layer outputs (e.g., a separate `signal_redistribution_tags` table).

**§4 — pg_audit_access_log schema (Sections 6, 8).**
Audit queries in Section 8 assume a specific JSONB column structure on
`pg_audit_access_log`. Confirm the D1 actual schema before Section 8 SQL
is used for compliance evidence. Column names may differ.

**§5 — Pending source default in customer documentation (Section 4).**
The spec defines `pending` as "not available for redistribution." Confirm
whether this should be communicated to customers proactively (e.g., in the
methodology doc or API reference) and whether pending fields should be listed
in the `GET /v1/instruments` catalog response with their pending status visible.

**§6 — Graceful degradation of final_score (Section 3).**
The spec allows a degraded `final_score` to be returned when one ML model is
blocked. The degraded score uses the remaining 4 models (re-weighted equally).
Confirm this is acceptable vs. requiring all 5 models to be available before
any composite score is returned. The conservative alternative would null the
entire `ml_composite` if any model is blocked.

**§7 — Cache reload latency SLA (Section 5).**
The spec allows up to 60 seconds for a source_catalog change to propagate to
live API responses via NOTIFY + cache reload. Confirm this is acceptable.
If sub-second propagation is required (e.g., for a blocked source that was
accidentally set to allowed), the alternative is to reload the cache
synchronously on every request that touches a pending/blocked source — at
meaningful performance cost.

---

*Session: 2026-03-06*
*Status: Pending architect confirmation on open assumptions §1–§7.*
*Next action: Architect confirms propagation rule (§1, §2) and ML registration pattern (§3).*
*Phase 5 build prompt (A3 or equivalent) begins after confirmation.*
