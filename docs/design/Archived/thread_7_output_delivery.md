# Thread 7 — Output Delivery
## FromTheBridge / Empire Architecture

**Date:** 2026-03-05
**Status:** Written. Authoritative.
**Depends on:** thread_2_signal.md (output contract), thread_infrastructure.md (Layer 8),
thread_6_build_plan.md (Phase 5/6 scope)
**Session:** Design discussion → document. All agenda items resolved before writing.

---

## 1. Product Surface Definition

### What v1 sells

A systematic, domain-driven signal API covering the crypto market across derivatives,
capital flows, DeFi, and macro — producing directional signals with calibrated
confidence scores, regime context, and full component provenance, updated on schedule,
available via REST.

### The v1 customer

Savvy retail with a technical lean. Understands the underlying mechanics — knows what
funding rate, OI, and MVRV mean without a glossary. Runs their own analysis but is
looking for a systematic, data-rich layer they trust more than social noise. Evaluates
the product by whether the methodology is legible, not whether it has a compliance
audit trail. Will read a methodology doc if it is clear. Does not submit RFPs.

Primary use case: informing entry and exit decisions. Secondary use case: portfolio
allocation context. Both are served — neither is deprioritized in the API design.
Broad coverage is part of the value proposition. The customer discovers instruments
they were not watching.

### "Institutional grade" defined

Institutional grade is an **internal quality bar**, not a customer descriptor. It
disciplines data integrity and methodology: PIT-correct historical data, reproducible
outputs with full audit trails, calibrated ML probabilities, no self-certification of
quality gates, dead letter logging for every rejected value. The customer never sees
most of this — but it is what makes the signal defensible when they ask how it was
produced.

### What is not in v1

- Dashboard or UI of any kind
- Self-serve account creation or billing infrastructure
- Push delivery to customers (webhook is v1 infrastructure but customer-initiated
  integration only — see Section 5)
- Tiered subscription management (manual key issuance only)
- Index or benchmark licensing
- White-label / embedded analytics

---

## 2. Delivery Model

**v1: API-first.** The API is the product surface and the primary customer deliverable.
Every field that a future dashboard would display exists in the API response from day
one. The UI does not require an API redesign — it consumes what is already there.

**v2: Dashboard.** Read-only signal dashboard. Gated on quality, not a calendar date.
Trigger conditions in Section 9.

**Social distribution** is a separate channel — funnel and audience-building, not
customer delivery. See Section 6.

---

## 3. API Surface

### Authentication

Manual key issuance in v1. No self-serve. Stephen provisions keys directly.
Key rotation on request or on suspected compromise — manual procedure, response
within 24 hours.

Every request carries the API key in the `X-API-Key` header. Tier is resolved from
the key at request time. No JWT, no OAuth in v1.

### Tiers

**Free tier** — no key required for browsing the docs; key required for API calls
(rate limiting). Data is publicly available elsewhere but normalized and aggregated
here. The hook is "if I can get it all here I will pay."

**Paid tier** — key required. Signals, scores, regime, component breakdowns,
provenance. Nothing in this tier exists anywhere else.

**Redistribution-gated** — data from Coinalyze, CoinMetrics, and SoSoValue is
excluded from all tiers until Phase 6 ToS audit clears. The API enforces this
structurally at the response layer, not by policy documentation alone. A query
that would return gated data returns an empty result set with an explicit
`redistribution_pending` flag on the affected fields.

### Rate Limits

| Tier | Requests/minute | Requests/day |
|---|---|---|
| Free | 30 | 1,000 |
| Paid | 120 | 20,000 |

Rate limit headers returned on every response: `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`. HTTP 429 on breach with
`Retry-After` header.

### Endpoints

#### Free Tier

---

**`GET /v1/market/prices`**

Current price, market cap, and 24-hour change for the crypto instrument universe.
Source: CoinPaprika.

Request parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `instruments` | string (comma-separated) | No | Filter to specific instruments. Default: full universe. |
| `page` | integer | No | Default: 1 |
| `per_page` | integer | No | Default: 100. Max: 500. |

Response schema:

```json
{
  "timestamp": "2026-03-05T12:00:00Z",
  "count": 157,
  "data": [
    {
      "instrument_id": "BTC",
      "name": "Bitcoin",
      "price_usd": 87430.21,
      "market_cap_usd": 1724000000000,
      "change_24h_pct": 2.14,
      "volume_24h_usd": 38200000000,
      "source": "coinpaprika",
      "observed_at": "2026-03-05T11:55:00Z"
    }
  ]
}
```

---

**`GET /v1/macro`**

Current values for all FRED macro series in the catalog. Yields, DXY, VIX, SP500,
employment, inflation, central bank balance sheets. Public domain — no redistribution
restriction.

Request parameters: none.

Response schema:

```json
{
  "timestamp": "2026-03-05T12:00:00Z",
  "data": [
    {
      "metric_id": "macro.rates.us_10y_yield",
      "label": "US 10-Year Treasury Yield",
      "value": 4.31,
      "unit": "percent",
      "observed_at": "2026-03-05T00:00:00Z",
      "source": "fred",
      "series_id": "DGS10"
    }
  ]
}
```

---

**`GET /v1/instruments`**

The full instrument catalog. What is covered, what tier, what domains. No data —
catalog only. Useful for developers building against the API and for customers
exploring coverage before subscribing.

Request parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `domain` | string | No | Filter by domain: `derivatives`, `flows`, `defi`, `macro` |
| `signal_eligible` | boolean | No | Filter to signal-eligible instruments only |

Response schema:

```json
{
  "count": 157,
  "data": [
    {
      "instrument_id": "BTC",
      "name": "Bitcoin",
      "asset_class": "crypto",
      "domains": ["derivatives", "flows", "defi"],
      "tier": "signal_eligible",
      "venue": "cross-venue",
      "signal_eligible": true,
      "coverage_note": null
    }
  ]
}
```

---

#### Paid Tier

---

**`GET /v1/signals`**

Full universe signal snapshot. The primary entry point — "what is interesting right
now across the whole covered universe." Filterable to surface the most actionable
instruments.

Request parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `direction` | string | No | `bullish`, `bearish`, `neutral` |
| `confidence_min` | float | No | Minimum confidence score (0.0–1.0) |
| `regime` | string | No | `risk_on`, `risk_off`, `transitional` |
| `domain` | string | No | Filter by signal domain |
| `instruments` | string (comma-separated) | No | Filter to specific instruments |
| `page` | integer | No | Default: 1 |
| `per_page` | integer | No | Default: 50. Max: 200. |

Response schema:

```json
{
  "generated_at": "2026-03-05T12:00:00Z",
  "regime": {
    "state": "risk_on",
    "confidence": 0.74,
    "as_of": "2026-03-05T12:00:00Z"
  },
  "count": 157,
  "stale_sources": [],
  "data": [
    {
      "instrument_id": "BTC",
      "signal": {
        "direction": "bullish",
        "confidence": 0.73,
        "confidence_tier": "high",
        "magnitude": 0.45,
        "horizon": "14d",
        "as_of": "2026-03-05T12:00:00Z"
      },
      "edsx": {
        "direction": "bullish",
        "confidence": 0.71,
        "pillars_computed": 4,
        "pillars_available": 5
      },
      "ml": {
        "direction": "bullish",
        "probability": 0.76,
        "calibrated": true
      },
      "staleness_flag": false
    }
  ]
}
```

`confidence_tier` is a human-readable label derived from the confidence float:
`low` (< 0.40), `moderate` (0.40–0.59), `high` (0.60–0.79), `very_high` (≥ 0.80).
This field exists so a customer reading the response without documentation open can
interpret the number. The float is always present alongside it.

---

**`GET /v1/signals/{instrument_id}`**

Single instrument detail with full component breakdown and provenance. The deep-dive
endpoint — used when a customer wants to understand why the signal is what it is.

Response schema:

```json
{
  "instrument_id": "BTC",
  "name": "Bitcoin",
  "generated_at": "2026-03-05T12:00:00Z",
  "signal": {
    "direction": "bullish",
    "confidence": 0.73,
    "confidence_tier": "high",
    "magnitude": 0.45,
    "horizon": "14d",
    "regime": "risk_on"
  },
  "synthesis": {
    "edsx_weight": 0.50,
    "ml_weight": 0.50,
    "agreement": true,
    "confidence_adjustment": null
  },
  "edsx": {
    "direction": "bullish",
    "confidence": 0.71,
    "pillars_computed": 4,
    "pillars_available": 5,
    "pillars": {
      "trend_structure": {
        "direction": "bullish",
        "score": 0.68,
        "null_state": null
      },
      "liquidity_flow": {
        "direction": "bullish",
        "score": 0.74,
        "null_state": null
      },
      "valuation": {
        "direction": "neutral",
        "score": 0.51,
        "null_state": null
      },
      "structural_risk": {
        "direction": "bullish",
        "score": 0.66,
        "null_state": null
      },
      "tactical_macro": {
        "direction": null,
        "score": null,
        "null_state": "METRIC_UNAVAILABLE"
      }
    }
  },
  "ml": {
    "direction": "bullish",
    "probability": 0.76,
    "calibrated": true,
    "components": {
      "derivatives_pressure": {
        "direction": "bullish",
        "weight": 0.40,
        "confidence": 0.81
      },
      "capital_flows": {
        "direction": "neutral",
        "weight": 0.35,
        "confidence": 0.65
      },
      "defi_health": {
        "direction": "neutral",
        "weight": 0.15,
        "confidence": null,
        "null_state": "INSUFFICIENT_HISTORY"
      },
      "macro_context": {
        "direction": "bullish",
        "weight": 0.10,
        "confidence": 0.70
      }
    }
  },
  "provenance": {
    "features_as_of": "2026-03-05T11:45:00Z",
    "oldest_observation_used": "2026-03-04T00:00:00Z",
    "sources_contributing": ["coinalyze", "etherscan", "defillama", "fred"],
    "stale_sources": [],
    "staleness_flag": false
  }
}
```

Null states are always explicit. Three valid values (from thread_3):
`INSUFFICIENT_HISTORY`, `SOURCE_STALE`, `METRIC_UNAVAILABLE`. A null score with no
null_state is a bug, not a valid response state.

---

**`GET /v1/regime`**

Current regime state with supporting context. Regime drives composite weight selection
in the synthesis layer — this endpoint exposes that context directly so customers
understand why instrument signals are weighted the way they are.

Response schema:

```json
{
  "as_of": "2026-03-05T12:00:00Z",
  "regime": {
    "state": "risk_on",
    "label": "Full Offense",
    "confidence": 0.74,
    "quadrant": "high_liquidity_low_volatility"
  },
  "anchors": {
    "volatility": {
      "value": 18.4,
      "state": "low",
      "source": "fred",
      "metric_id": "macro.volatility.vix"
    },
    "liquidity": {
      "value": 0.68,
      "state": "high",
      "source": "derived"
    }
  },
  "edsx_weights": {
    "trend_structure": 0.30,
    "liquidity_flow": 0.30,
    "valuation": 0.20,
    "structural_risk": 0.10,
    "tactical_macro": 0.10
  }
}
```

Regime label maps to the four quadrants defined in thread_2:
`Full Offense`, `Selective Offense`, `Defensive Drift`, `Capital Preservation`.
Human-readable label is always present alongside the machine-readable state.

---

**`GET /v1/health`**

System freshness and staleness flags per source. This endpoint exists for two
audiences: customers checking whether the signals they are reading are fresh, and
developers debugging integrations. It is also the basis for SLA measurement.

Response schema:

```json
{
  "as_of": "2026-03-05T12:00:00Z",
  "status": "healthy",
  "sources": [
    {
      "source_id": "coinalyze",
      "last_successful_run": "2026-03-05T10:30:00Z",
      "expected_cadence_hours": 8,
      "freshness_state": "fresh",
      "instruments_affected": 121,
      "stale": false
    },
    {
      "source_id": "fred",
      "last_successful_run": "2026-03-05T06:00:00Z",
      "expected_cadence_hours": 24,
      "freshness_state": "fresh",
      "stale": false
    }
  ],
  "signals_last_computed": "2026-03-05T12:00:00Z",
  "stale_instrument_count": 0
}
```

`status` is `healthy`, `degraded` (one or more sources stale, signals still
computing on available data), or `impaired` (signal computation affected).

### API Versioning

All endpoints are versioned at the path level (`/v1/`). The v1 contract is stable
once Phase 5 gate passes. Fields may be added to responses without a version bump.
Fields will not be removed or renamed within a version. Breaking changes require
a new version path (`/v2/`) with a minimum 60-day deprecation notice for v1.

### Latency SLAs

Measured at the 50th, 95th, and 99th percentile from the API process, excluding
client network latency. Prometheus histogram on all endpoint response times.

| Endpoint | p50 | p95 | p99 |
|---|---|---|---|
| `GET /v1/signals` | 200ms | 800ms | 1500ms |
| `GET /v1/signals/{instrument}` | 100ms | 400ms | 800ms |
| `GET /v1/regime` | 50ms | 200ms | 400ms |
| `GET /v1/health` | 50ms | 150ms | 300ms |
| `GET /v1/market/prices` | 100ms | 400ms | 800ms |
| `GET /v1/macro` | 50ms | 200ms | 400ms |
| `GET /v1/instruments` | 50ms | 150ms | 300ms |

SLA breach defined as p95 exceeding the committed value for 3 consecutive 5-minute
measurement windows. Breach triggers customer notification (see Section 7).

### Error Responses

All errors return a consistent envelope:

```json
{
  "error": {
    "code": "INSTRUMENT_NOT_FOUND",
    "message": "Instrument ETH2 is not in the covered universe.",
    "request_id": "req_01jnx4k2m8f3p"
  }
}
```

Standard error codes: `INSTRUMENT_NOT_FOUND`, `INVALID_PARAMETER`,
`REDISTRIBUTION_RESTRICTED`, `RATE_LIMIT_EXCEEDED`, `UNAUTHORIZED`,
`SOURCE_STALE` (signal requested but all contributing sources stale),
`SIGNAL_UNAVAILABLE` (instrument below signal eligibility threshold).

Every error response includes a `request_id` for support tracing.

---

## 4. Signal Cadence and Freshness

Signals are recomputed on the Dagster asset graph trigger — event-driven on metric
ingestion, not wall-clock. From thread_3: computation trigger is metric ingestion,
not scheduled.

In practice, given source cadences:

| Source cadence | Expected signal refresh |
|---|---|
| Coinalyze (8h) | Derivatives-dependent signals refresh ~3x/day |
| Explorer / Etherscan (8h) | Flow-dependent signals refresh ~3x/day |
| DeFiLlama (12h) | DeFi-dependent signals refresh ~2x/day |
| FRED (24h) | Macro-dependent signals refresh ~1x/day |

The `as_of` timestamp on every signal response is the feature computation timestamp,
not the API request timestamp. Customers always know how fresh the signal is.

The `staleness_flag` on a signal means one or more contributing sources missed their
expected ingestion window. The signal is still served on the most recent available
data, but the flag is explicit. This is the "propagate staleness honestly" principle
from thread_2.

---

## 5. Webhook Delivery

Webhook is available in v1 as a customer-initiated integration — customers who want
push delivery configure an endpoint and receive signal updates without polling.
It is not a customer onboarding default.

### Payload Schema

```json
{
  "event": "signal.updated",
  "webhook_id": "wh_01jnx4k2m8f3p",
  "delivered_at": "2026-03-05T12:01:03Z",
  "payload": {
    "instrument_id": "BTC",
    "signal": {
      "direction": "bullish",
      "confidence": 0.73,
      "confidence_tier": "high",
      "magnitude": 0.45,
      "horizon": "14d",
      "as_of": "2026-03-05T12:00:00Z"
    },
    "regime": "risk_on",
    "staleness_flag": false
  }
}
```

Event types in v1: `signal.updated`, `signal.stale`, `health.degraded`,
`health.impaired`.

### Delivery Guarantee

At-least-once delivery. Retry on non-2xx response: 3 attempts with exponential
backoff (30s, 5min, 30min). After 3 failures, event is logged to dead letter and
customer is notified via their registered contact.

### HMAC Signature

Every webhook request carries an `X-Signature-256` header:
`hmac-sha256=<hex digest>` computed over the raw request body using the customer's
webhook secret. Customer verifies on receipt before processing payload.

Webhook secrets are provisioned separately from API keys. Rotation on request.

---

## 6. Social Distribution

Social channels are a distribution and audience-building funnel — not a customer
delivery mechanism. Paying customers receive their deliverable via API. Social posts
are marketing infrastructure.

### Channels

- **Telegram** (`@FromTheBridgeChannel`) — automated, existing infrastructure
- **X** (`@BridgeDispatch`) — automated, configuration set, retest required before
  activation

Both channels receive identical content. One automation feeds both.

### Post Format

Human-readable signal summaries. Self-contained — no link required, no landing page
dependency. Example format:

```
📊 BTC — Bullish | High Confidence | 14d horizon
Derivatives pressure: bullish (strong)
Capital flows: neutral
Regime: Risk On

Signal generated 2026-03-05 12:00 UTC
fromthebridge.net
```

Format principles: directional call first, confidence tier in plain English
(not the float), horizon explicit, regime context, timestamp, domain reference.
No jargon that requires a glossary. A non-technical reader understands the call;
a technical reader sees enough to evaluate it.

### Gate Condition

Social automation does not activate until signals pass the Phase 5 shadow period
and graduation criteria. Broadcasting before signal quality earns it damages the
public record. The gate is the same graduation bar that governs production deployment.

Specific trigger: social automation activates after the first 30-day shadow period
produces consistent accuracy and stability (matching the Phase 4 ML graduation
criteria). Stephen activates manually after reviewing shadow evaluation results.

### Failure Handling

Telegram delivery failure: logged, not retried automatically. Post cadence is
best-effort — a missed social post is not an SLA event. Customer delivery SLAs
are on the API, not social channels.

---

## Data Source Legal Compliance Framework

### Four-Layer Mitigation Structure

**Layer 1 — Architecture:** Raw source data never exits the system. The 9-layer stack enforces this structurally: raw payloads land in Bronze (internal), observations write to Silver (internal), features compute in Gold/Marts (internal). The serving layer (Layer 8) reads Marts only — composite, transformed outputs. No endpoint returns raw source data.

**Layer 2 — Tier Compliance:** Every source in `forge.source_catalog` carries a `redistribution_status` flag (`allowed`, `blocked`, `pending`). The serving layer filters on this flag at response time. Sources flagged `blocked` or `pending` are excluded from all customer-facing outputs until the flag is explicitly changed after audit. This is enforced in code, not policy.

**Layer 3 — Formal ToS Audit:** Per-source legal review before Phase 6 gate. Each source receives one of three dispositions:
- **Clear** — ToS permits commercial use of derived products. No action required.
- **Upgrade required** — Free tier prohibits commercial use. Must upgrade to commercial license or exclude from customer outputs.
- **Exclude** — ToS prohibits derived product distribution. Source remains internal-only (training data, internal analytics) but is excluded from all customer-facing signal computation.

**Layer 4 — Signal Abstraction:** The legal defense for derived products rests on genuine multi-source composite transformation. A signal that is a relabeled single-source metric is not sufficiently transformed. Sufficient transformation requires: (a) multiple independent data sources contributing to the output, (b) non-trivial computation (scoring, normalization, regime classification, ML inference), and (c) the output cannot be reverse-engineered to recover the original source data.

### Transformation Sufficiency Standard

A signal is sufficiently transformed when it is a genuine multi-source composite — not a relabeled single-source metric. The EDSx pillars and ML models inherently satisfy this: each combines features from 3–7 independent sources through non-trivial computation. Single-source pass-through metrics (e.g., raw funding rate from Coinalyze) are never exposed in customer-facing endpoints.

### Per-Source Risk Assessment

| Source | Risk Level | Basis | Phase 5 Action Required |
|--------|-----------|-------|------------------------|
| FRED | None | US government public domain | None |
| CFTC COT | None | US government public domain | None |
| DeFiLlama | Low | Open source, permissive license | Confirm license terms cover commercial derived products |
| CoinMetrics | Medium | `redistribution = false` on community CSV; derived signal rights unclear | Verify whether derived signals (not raw data) are permitted. Exclude from customer outputs if not. |
| SoSoValue | Medium | `redistribution = false`; free tier non-commercial ToS | Verify whether derived signals permitted. Upgrade or exclude before Phase 6. |
| Tiingo | Medium | Paid tier; commercial redistribution clause needs verification | **FRG-45: Verify before Phase 1 live collection.** Confirm derived product rights under paid agreement. |
| Coinalyze | Medium | Free tier; ToS not yet audited for commercial derived product use | Audit ToS. Upgrade or exclude if free tier prohibits. |
| BGeometrics | Medium | Free tier; ToS not yet audited | Audit ToS. Upgrade or exclude if free tier prohibits. |
| Etherscan | Medium | Freemium tier; ToS not yet audited for commercial use | Audit ToS. Upgrade or exclude if free tier prohibits. |
| CoinPaprika | Medium | Free tier; ToS not yet audited | Audit ToS. Upgrade or exclude if free tier prohibits. |
| Binance BLC-01 | Medium | Public WebSocket stream; commercial use of derived products not verified | Audit Binance API ToS for derived product distribution rights. |

### Policy Statement

Any source whose free tier ToS prohibits commercial use must either be upgraded to a commercial license or excluded from customer-facing signal outputs before the Phase 6 gate. There are no exceptions. The `redistribution_status` flag in `forge.source_catalog` is the enforcement mechanism — it must be set to `allowed` before any source's data flows into customer-visible responses.

### Audit Execution

Pipeline items FRG-40 through FRG-46 track per-source audit execution. FRG-45 (Tiingo) triggers before Phase 1 live collection. All others trigger at Phase 5 pre-gate. FRG-46 is the architect sign-off that all 11 sources are dispositioned.

---

## 7. SLA Definitions

Four commitments. Each has a measurement method and a breach definition. These are
the commitments made to paying customers.

### SLA 1 — Signal Freshness

**Commitment:** Signals are recomputed within 90 minutes of a source ingestion event
completing.

**Measurement:** Prometheus metric `signal_compute_lag_seconds` — time between
`ingestion_completed_at` and `signal_as_of` for each compute cycle. Alerting rule
fires if lag exceeds 5400 seconds (90 minutes).

**Breach definition:** Signal compute lag exceeds 90 minutes for any signal-eligible
instrument for more than one consecutive compute cycle.

**Customer notification:** Email to registered contact within 30 minutes of breach
detection.

### SLA 2 — API Uptime

**Commitment:** 99.5% monthly uptime for all paid-tier endpoints.

**Measurement:** Blackbox exporter probing `/v1/health` every 60 seconds.
Downtime = consecutive failed probes for ≥ 2 minutes. Monthly uptime calculated
from Prometheus `probe_success` metric.

**Breach definition:** Monthly uptime falls below 99.5% (allows ~3.6 hours downtime
per month).

**Customer notification:** Status update posted to a public status page (minimal —
a static page updated manually in v1) within 15 minutes of outage start. Resolution
notice within 30 minutes of recovery.

### SLA 3 — Staleness Notification

**Commitment:** Customers are notified within 60 minutes of a source failure that
affects their signals.

**Measurement:** Forge monitor checks source freshness every 30 minutes. Alert fires
when `freshness_state` transitions to `stale` for any source. Notification pipeline
latency tracked from alert fire to customer email send.

**Breach definition:** Customer notification sent more than 60 minutes after
staleness detection.

**Customer notification is the SLA event itself.** Email to registered contact
including: which source is stale, which instruments are affected, estimated
resolution timeline if known.

### SLA 4 — Methodology Change Notice

**Commitment:** Customers receive a minimum 14-day advance notice before any change
to signal methodology that affects interpretation of outputs.

**Scope:** Model retraining that changes output distributions, pillar weight
adjustments, regime classification changes, confidence scoring changes. Does NOT
cover: bug fixes that correct erroneous outputs, source replacements that do not
change the metric being measured.

**Measurement:** Manual process. Stephen logs methodology changes in the changelog
(Section 8) and sends customer notice at the time of logging, minimum 14 days before
deployment.

**Breach definition:** Methodology change deployed without 14-day advance customer
notice.

---

## 8. Methodology Documentation

### Purpose

The methodology doc is what a paying customer reads before trusting the signal enough
to act on it. It is written for the v1 customer profile: technically literate,
does not need compliance-grade documentation, will evaluate credibility from clarity
and specificity rather than length.

### Location

Public URL: `fromthebridge.net/methodology`

Static page in v1. Not behind auth — readable by anyone evaluating the product.
Versioned in git alongside the codebase. Every deployed version has a stable URL
(`fromthebridge.net/methodology/v1`, `fromthebridge.net/methodology/v1.1`, etc.).

### Sections

**1. What this product produces**
One paragraph. The signal, in plain terms: what it predicts, over what horizon, and
what it does not predict. Sets expectations before the customer reads further.

**2. Coverage**
Instrument universe. How instruments become signal-eligible (the tier promotion rules
from thread_4). What domains are covered and which instruments fall into each domain.
Known coverage gaps with documented plans.

**3. Signal architecture**
The two tracks (EDSx and ML) in plain terms. What each produces, how they are
combined in synthesis. What agreement and disagreement mean for confidence. Regime
classification and how it affects weights. Written to be understood by the v1 customer
without requiring thread_2 as a prerequisite.

**4. EDSx methodology**
The five pillars: definitions, what each measures, which data sources contribute to
each pillar. How confidence is computed (signals_computed / signals_available — data
completeness, not prediction confidence). How null states propagate.

**5. ML methodology**
The five domain models: what each predicts, what features each uses (at the domain
level — not exhaustive feature lists). Training approach (walk-forward). Calibration
(isotonic). Graduation criteria in plain terms. What "calibrated probability" means
without requiring an ML background.

**6. Data sources**
Every source contributing to v1 signals, with: what it provides, collection cadence,
known limitations, redistribution status. This section is updated when sources are
added, removed, or ToS status changes.

**7. Known limitations**
Every gap in the known gaps register that affects customer-visible outputs. Written
honestly — not as disclaimers but as factual statements about current coverage.
Includes: proxy metrics where actuals are not yet available, null-propagated domains,
instruments below signal eligibility threshold.

**8. Changelog**
Version history. Every methodology change with: date deployed, what changed, what
the customer impact is, whether advance notice was given. This is the audit trail
for SLA 4 compliance. Entries are added at the time of notice, not at deployment.

### Versioning

Methodology doc version increments when:
- A model is retrained and the output distribution changes materially
- A pillar definition changes
- A source is added or removed from signal computation
- Regime classification logic changes

Minor corrections (typos, clarifications that do not change methodology) do not
increment the version.

The current version is always served at `fromthebridge.net/methodology`. Prior
versions remain accessible at their versioned URLs indefinitely.

---

## 9. First Customer Onboarding

Complete sequence from first contact to first invoice. Stephen is the responsible
party for all steps in v1.

### Pre-delivery

1. **Initial contact** — inbound (social or referral) or outbound (direct). No
   cold outreach in v1 — first customers come through the network or the social funnel.

2. **Pre-read materials** — customer receives the methodology doc URL and the
   instrument catalog endpoint (`GET /v1/instruments`) before any commercial
   conversation. They evaluate the product before pricing is discussed.

3. **Pricing conversation** — direct. Manual invoicing. Monthly subscription, paid
   upfront. No free trials. No discounts in exchange for testimonials or referrals
   (conflicts with methodology credibility).

4. **Agreement** — written. Simple terms: what they receive, what the SLAs are,
   what the redistribution restrictions are (specifically: they may not redistribute
   signals derived from ToS-restricted sources). No legal infrastructure in v1 —
   a clear written email exchange is sufficient. This is reviewed before v2.

### Access Provisioning

5. **API key issued** — manually. Key provisioned with paid tier permissions.
   Webhook secret provisioned separately if customer requests push delivery.

6. **Onboarding note** — one-page document (or structured email) covering:
   - API base URL and authentication
   - Recommended first queries (instruments catalog, then signals with confidence_min
     filter to see the highest-conviction calls)
   - How to read confidence tiers and null states
   - How to check staleness (`GET /v1/health`)
   - Support contact (direct email to Stephen in v1)
   - Methodology doc URL

### First Delivery

7. **First signal pull** — customer makes their first API call. No ceremony — the
   API is the product. Stephen is available for questions via direct email for the
   first 7 days.

8. **Day 7 check-in** — brief. Three questions: is the data format working for your
   use case, are there instruments you expected to see that are missing, any
   methodology questions. This is product feedback, not account management.

### Ongoing

9. **Methodology change notifications** — per SLA 4. Email to customer's registered
   contact minimum 14 days before any material methodology change.

10. **Staleness notifications** — per SLA 3. Automated where possible. Manual
    fallback if automation fails.

11. **Invoicing** — monthly. Manual in v1. Invoice sent on the same day each month.
    Payment terms: net 7.

---

## 10. v2 Trigger Conditions

v2 development (dashboard, self-serve onboarding, tiered billing infrastructure)
begins when **both** of the following conditions are true:

**Condition A:** The API has paying customers and conversion friction is measurably
losing signups. Defined as: inbound interest (tracked via social engagement or direct
inquiry) that does not convert, where the stated or inferred reason is the absence
of a UI or self-serve access.

**Condition B:** The social funnel is generating inbound volume that the API alone
cannot convert at an acceptable rate. Defined as: consistent inbound inquiries from
the social channels where the prospect's first question is about a dashboard or
visual interface rather than the API.

Neither condition alone is sufficient. The first condition validates that the product
has traction. The second validates that UI friction is the binding constraint on
growth, not signal quality or coverage.

If signal quality or coverage is the reason inbound does not convert, the fix is in
Phase 3/4/5 (signal quality, coverage expansion) — not a dashboard. Building v2
before both conditions are met risks shipping a UI on top of a signal that hasn't
earned trust.

---

## Decisions Locked

| Decision | Outcome |
|---|---|
| v1 delivery model | API-first. No dashboard. |
| v1 customer | Savvy retail, technical lean, learns from product, broad coverage interest |
| "Institutional grade" | Internal quality bar only — not a customer descriptor |
| Free tier | 3 endpoints: prices, macro, instruments. Schema is public contract on ship. |
| Paid tier | signals, signals/{instrument}, regime, health |
| Redistribution gating | Coinalyze, CoinMetrics, SoSoValue excluded until Phase 6 ToS audit |
| Free/paid tier mobility | Tier boundaries move freely. Schema changes require versioned deprecation. |
| Authentication | Manual key issuance in v1. No self-serve. |
| Social distribution | Telegram + X. Human-readable summaries. Funnel only — not customer delivery. |
| Social gate | Activates after Phase 4 shadow period passes. Manual activation by Stephen. |
| Webhook | Available in v1 as customer-initiated integration. At-least-once, HMAC-signed. |
| SLA count | Four: signal freshness, API uptime, staleness notification, methodology change notice |
| Methodology doc | Public URL. Versioned. 8 sections. Updated on every material methodology change. |
| First customer | Direct engagement. Written agreement. No free trials. Manual invoicing. |
| v2 triggers | Both: API conversion friction measurable AND social inbound UI-blocked. Neither alone sufficient. |

---

*Session completed: 2026-03-05*
*Next action: Architect confirms. Prompt 05 begins.*
