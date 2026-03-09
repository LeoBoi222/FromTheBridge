# FromTheBridge — Consolidated v3 Plan
**Date:** 2026-03-06  
**Status:** Design synthesis complete. All 9 results locked. Ready for Phase 1 execution.  
**Prepared for:** Architect review before Phase 5 build prompt generation.

---

## 1. Phase Status

| Phase | Description | Status | Notes |
|-------|-------------|--------|-------|
| Design | All thread files + synthesis session | ✅ Complete | 9 results locked 2026-03-06 |
| Phase 0 | Schema Foundation | ✅ Complete | 13/13 gate criteria passed |
| Phase 1 | Data Collection | ⬜ Not started | Blocked: Dagster service not in docker-compose yet |
| Phase 2 | Feature Engineering | ⬜ Not started | Depends on Phase 1 gate |
| Phase 3 | EDSx Signal | ⬜ Not started | Depends on Phase 2 gate |
| Phase 4 | ML Track (Shadow) | ⬜ Not started | Depends on Phase 3 gate |
| Phase 5 | Serving | ⬜ Not started | Depends on Phase 4 gate |
| Phase 6 | Productization | ⬜ Not started | Depends on Phase 5 gate |

---

## 2. What the Design Synthesis Session Locked

### B1 — Layer 2 Synthesis Algorithm → thread_2_signal.md §L2.1–L2.8

Complete EDSx/ML composite scoring algorithm. Replaces the §11.3 placeholder that existed in the prior design.

Key locked decisions:
- EDSx pillar aggregation: SR modulation, G1–G3 guardrails, null pillar exclusion, renormalization
- ML model aggregation: entropy-discounted directional weights, Volatility Regime as conditioner only (no directional contribution), minimum 2 active directional models or ml_composite = None
- Synthesis: `final_score = 0.5 × edsx + 0.5 × ml` default; horizon-adjusted (1D: ML weight = 0; 30D: ML weight × 0.70); agreement boost/penalty ±15%/–20%
- VLA regime weights table locked for H2 2026 (four-quadrant: Full Offense / Selective Offense / Defensive Drift / Capital Preservation)
- Full `/v1/signals` response schema including provenance block (§L2.8)
- 1D horizon: EDSx-only (ML 14D horizon incompatible)

### C1 — Silver→Gold Hybrid Export → thread_infrastructure.md, thread_5, thread_6

Resolves the 6h fixed export cadence that would have breached the 90min signal freshness SLA.

Key locked decisions:
- Trigger: `multi_asset_sensor` polling 10 collection asset keys at 30s intervals + `@hourly` fallback
- ClickHouse query: `SELECT ... FINAL WHERE ingested_at > {watermark}` with 3-minute lag floor protecting in-flight writes
- Gold write: PyIceberg partition overwrite (not append). Partition key: `(year_month, metric_domain)`
- Watermark: stored in Dagster asset materialization metadata
- Anomaly guard: failure if delta > 10× rolling 7-day avg or > 2M rows; bypass via `force_backfill=True`
- Worst-case freshness path: **44 minutes** (vs. 90min SLA — 46min margin)
- Phase 1 gate benchmarks: FINAL query 500k-row window < 60s

### E3+E4 — DeFiLlama Yields + CFTC COT → thread_4, thread_5, thread_6

Two new sources, 7 new metrics, resolves utilization proxy and macro positioning gap.

**E3 — DeFiLlama Yields (4 metrics, extends existing adapter):**
- `defi.lending.supply_apy` — DeFiLlama /yields apyBase ÷ 100; not nullable
- `defi.lending.borrow_apy` — DeFiLlama /yields apyBorrow ÷ 100; nullable (supply-only pools)
- `defi.lending.reward_apy` — DeFiLlama /yields apyReward ÷ 100; nullable; signal_eligible=false until v1.1
- `defi.lending.utilization_rate` — methodology updated from proxy (borrow/supply TVL) to direct pool utilization; canonical name unchanged (schema immutability)
- Scope: Aave v3/v2, Compound v3/v2, Curve on Ethereum + Arbitrum; USDC/USDT/DAI/WETH/WBTC
- `instrument_id`: underlying asset canonical symbol; `__market__` sentinel for exotic tokens
- `observed_at`: request time truncated to 12h boundary (midnight/noon UTC)
- Pre-flight PF-6: verify utilization field is decimal (not percent) before Phase 1 build
- 4 Dagster assets as fan-out from single `collect_yields()` op

**E4 — CFTC COT (4 metrics, new source):**
- `macro.cot.institutional_net_position` — TFF non-commercial net (long minus short)
- `macro.cot.institutional_long_pct` — non-commercial long fraction of total reportable
- `macro.cot.open_interest_contracts` — total open interest contracts
- `macro.cot.dealer_net_position` — dealer/intermediary net position
- Source: CFTC TFF report via Socrata API (`data.cftc.gov`), weekly release Fridays 3:30pm ET
- `observed_at`: Tuesday as-of date (not Friday release date); features account for 3-day publication lag
- Instruments: BTC (CME Bitcoin futures + Micro BTC aggregated) and ETH (CME Ether futures)
- Signal relevance: EDSx-05 Tactical Macro (REM-22/23), ML Capital Flow Direction

### D2 — Entitlement & Tenant Model → thread_7, thread_infrastructure, thread_6

Complete Phase 5 access control system.

Key locked decisions:
- 12 new PostgreSQL tables in `forge` schema: `customers`, `api_keys`, `plans`, `rate_limit_policies`, `subscriptions`, `plan_endpoint_access`, `plan_field_access`, `plan_lookback_config`, `plan_instrument_access`, `customer_instrument_overrides`, `redistribution_blocked_metrics`, `audit_access_log` (partitioned)
- 4-tier plan matrix: Free / Pro / Protocol / Institutional (supersedes all prior Free/Paid binary references)
- 12-step middleware chain: key format → lookup → key state → account state → subscription → endpoint access → rate limit → instrument access → lookback → handler → redistribution filter → field filter → async audit write
- Rate limit backend: PostgreSQL sliding window against `audit_access_log` (no Redis in v1)
- In-process LRU cache: `cachetools.TTLCache`, 1,000 entries, 60s TTL per worker
- Concurrent limit: `asyncio.Semaphore` per `customer_id`
- Audit log: 90d hot (PostgreSQL monthly partitions), 7-year warm archive (MinIO Iceberg)
- `api_keys.key_prefix` = 12 characters (`ftb_` + 8 chars token entropy)
- Operator SQL templates ready for manual customer management (v1, no Stripe)

**4-Tier Entitlement Matrix summary:**

| Tier | Endpoints | Rate (RPM/RPD) | Concurrent | Webhook | Provenance |
|------|-----------|----------------|------------|---------|------------|
| Free | market/prices, macro, instruments, health | 30 / 1,000 | 2 | No | No |
| Pro | + signals, performance (365d window) | 120 / 20,000 | 5 | Yes | No |
| Protocol | Pro + pillar attribution (scoped instruments) | 300 / 100,000 | 20 | Yes | No |
| Institutional | All + features/{id}, window=all, reliability diagram | 300 / 100,000 | 20 | Yes | Yes |

### A2 — Redistribution Enforcement (Option c) → thread_7

Reconciles D2 (direct-lineage-only) with A2 (three-state enum). Option c ruling: adopt direct-lineage enforcement from D2 + three-state enum (`allowed`/`pending`/`blocked`) from A2.

Key locked decisions:
- Propagation: direct lineage only (weight > 0 inputs per `metric_lineage`); no transitive DAG walk
- Coinalyze: `pending` (unaudited, not hard-blocked; ToS audit Phase 6)
- CoinMetrics: `blocked` (redistribution=false, internal-only ToS)
- SoSoValue: `blocked` (non-commercial ToS)
- Response schema: field omission + top-level `_redistribution_notice` with per-field detail (`fields_suppressed`, `status`, `reason`, `sources`)
- Refresh mechanism: `forge_redistribution_refresh` Dagster asset (sole writer to `forge.redistribution_blocked_metrics`); daily 02:00 UTC + sensor on `source_catalog` changes
- Operator resolution path: single SQL UPDATE on `source_catalog` → Dagster sensor fires → cache refreshes ≤10 minutes → fields unblocked (zero code changes)
- 5 audit evidence query patterns documented for vendor compliance

### D1 — Security Posture Baseline → thread_infrastructure, thread_6, thread_7

Minimum viable security before first external customer key.

Key locked decisions:
- Secrets: file-based bind mounts in `/opt/empire/FromTheBridge/secrets/` (chmod 600, root:root). No credentials in environment variables, docker-compose.yml, or docker inspect output. Initialized via `scripts/init_secrets.sh`
- `crypto_user`: operator terminal only; never mounted in any container
- MinIO root key: operator + `mc` CLI only; never mounted in containers
- ClickHouse isolation: `ch_writer` = INSERT-only on `forge.observations` + `forge.dead_letter`; `ch_export_reader` = SELECT on `forge.observations FINAL` only; no other service has ClickHouse credentials (Rule 2 enforced structurally)
- MinIO service accounts: `bronze_writer`, `gold_reader`, `export_writer` (separate credentials, scoped per bucket)
- Customer API key delivery: 1Password secure share (one-time-view link); never email plaintext
- Annual rotation: March 2027 (calendar entry required at Phase 6 gate)
- 4 incident response playbooks: API key compromise, ClickHouse credential exposure, MinIO root key exposure, PostgreSQL forge_user exposure
- Phase 6 security gate: `init_secrets.sh` executed, no `REPLACE_ME` values remain, isolation verified, Tier 0 tests pass

### B3 — Performance History Endpoint → thread_7, thread_3, thread_6

`GET /v1/signals/performance` as Phase 5 scope addition.

Key locked decisions:
- 2 new dbt marts: `signal_outcomes` (daily incremental, PIT JOIN on Tiingo closes strictly after `computed_at`) and `performance_metrics` (~7,380 rows pre-aggregated across 5 windows × 3 tracks × 4 horizons × 121 instruments)
- PIT rules: anchor price = first close WHERE `observed_at > computed_at`; backfill guard = `WHERE ingested_at_signal <= outcome_observed_at`; unresolved = `outcome_date > CURRENT_DATE - 1`
- Metrics: directional accuracy (neutral threshold ±0.10 fixed), quintile returns (Q1–Q5), Sharpe (population std, no Bessel), max drawdown, ECE (10 bins, target <0.05), Brier score, reliability diagram (request-time DuckDB, <50ms), regime-conditional (PIT-stored regime at emission time)
- Reliability diagram: only non-pre-materialized metric; computed at request time from `signal_outcomes`; DuckDB grouped aggregation
- Cross-sectional: equal-weight; instruments with <30 resolved signals excluded entirely (no zero contribution)
- SLA: p95 ≤ 500ms
- Field-tier mapping: Free=403; Pro=directional/quintile/Sharpe/ECE/regime (365d); Protocol=+pillar attribution (scoped); Institutional=+reliability diagram/window=all
- **Pre-Phase 5 blocking action:** Audit EDSx-02 and EDSx-03 R3 backfill `ingested_at` values before `signal_outcomes` dbt model is written — determines verified performance history depth at launch

### F1 — Customer Acquisition Plan → thread_1_revenue, thread_7, design_index

Go-to-market strategy. **Option c timing resolution locked:** Pro tier opens after Phase 5 gate (not Phase 4 shadow). Protocol/Institutional customers remain Phase 6.

Key locked decisions:
- Profile A (prosumer quant): pull via content; no cold outreach; self-serve after Phase 5 gate
- Profile B (protocol ecosystem): direct outreach; warm intro path; minimum 3 Profile A subscribers + 60-day live history before first approach
- Pricing: Pro $199/month ($1,990/year); Protocol founding $4,500 (one-time); Protocol standard $7,500; 90-day retainer $18,000; Institutional $2,500/month
- OPEX: ~$85/month (Tiingo ~$50, electricity ~$30, domain ~$5)
- Break-even (6 months): 10 Pro subscribers OR 1 protocol engagement
- 48h-delayed public preview: top-10 composite score on `fromthebridge.net`, no account required, no pillar detail, live at shadow week 2 (Phase 3 EDSx confirmed)
- Single highest-leverage content asset: 2,000-word PIT-correctness post published simultaneously as Twitter/X thread + Substack; links to methodology doc and GitHub schema
- Public GitHub repo: metric catalog schema (sanitized), PIT-correctness README, EDSx pillar weight specification (not implementation code)
- 90-day sprint: ~27 hours total (~1.7h/week); all GTM work batched during natural build pauses
- Priority protocol targets: Aave (primary), Uniswap, Lido, ARB Foundation, Solana Foundation (long-lead)
- v0 Aave report: drafted during Phase 3 as a production validation test (not waiting for Phase 6)
- Stripe deferred: trigger = 20+ active subscribers (design_index Known Gap)

### C2+C3 — Bronze Cold Archive + Signal Snapshot Cache → thread_infrastructure, thread_6

Zero new infrastructure components. All architecture invariants confirmed intact.

**C2 — Bronze Cold Archive (Phase 1):**
- Two-bucket MinIO deployment: `bronze-hot` (90d lifecycle expiry) + `bronze-archive` (indefinite)
- Separate credentials: `MINIO_BRONZE_ARCHIVE_USER` isolated from `bronze-hot`
- Archive job: daily 02:00 UTC, window today-9 to today-2 (2-day lag; 88-day safety margin before hot expiry)
- Idempotency: `forge.bronze_archive_log` in PostgreSQL (Rule 3 compliant — admin metadata only)
- Partition discovery: DuckDB query over Iceberg metadata (~50ms); no additional PostgreSQL table
- Reprocessing path: `mc cp --recursive` archive → hot → force-materialize Bronze → Silver deduplication via ReplacingMergeTree `data_version` → trigger Silver→Gold export → rematerialize features
- Phase 1 gate: C2-01 through C2-10

**C3 — Signal Snapshot Cache (Phase 5):**
- In-process Python dict in FastAPI `app.state.signal_cache`; ~250KB heap; GIL-safe atomic swap
- Option C entitlement model: redistribution filter at populate time (gated field values never in cache object); tier filter per-request
- Population: Dagster HTTP POST to `/internal/cache/refresh` (INTERNAL_CACHE_TOKEN) after `signal_snapshot_writer` asset completes
- Canonical store: `MinIO gold/snapshots/latest.json` (warm start source on FastAPI restart; <5s)
- Stale threshold: 9h (6h cadence × 1.5); serves stale with `is_stale=True + next_computation_estimated=null`; no HTTP error
- Readiness: `/healthz/ready` → 503 until `cache.ready=True`; Docker health check uses this endpoint
- Latency gate: GET /v1/signals full universe p95 < 50ms (cache hit)
- Phase 5 gate: C3-01 through C3-11

---

## 3. Consolidated Phase Gate Criteria

### Phase 1 Gate (Data Collection)
Original criteria per thread_6 plus session additions:

**Dagster infrastructure:**
- 3 Dagster services healthy (webserver, daemon, code server)
- Dagster service definition added to docker-compose.yml (currently missing)

**Collection:**
- ≥1 Dagster asset per source (all 10 v1 sources)
- Tiingo Silver rows confirmed
- All 10 sources producing Silver rows
- Bronze Iceberg table exists and is queryable via DuckDB
- Great Expectations checkpoint configured and passing
- Dead letter captures bad rows correctly
- BLC-01 rsync from Server2 operational
- NAS backups confirmed (×2 rotation)
- ClickHouse credential isolation verified (ch_writer not on FastAPI; no other service has SELECT)
- Full round-trip: collection → Bronze → Silver → export trigger → Gold readable
- `macro.credit.hy_oas` confirmed in FRED (original gate criterion)

**E3+E4 specific:**
- PF-6: curl DeFiLlama /yields → verify utilization field is decimal (not percent); result recorded before adapter build
- DeFiLlama yields adapter producing Silver rows for all 4 metrics across scoped pools
- CFTC COT adapter producing Silver rows for 4 metrics (BTC + ETH)
- Dead letter tests for null `borrow_apy` (valid) vs. null `utilization_rate` (dead letter violation) pass

**C2 specific (C2-01 through C2-10):**
- bronze-archive bucket initialized in MinIO
- MINIO_BRONZE_ARCHIVE_USER credential created and isolated
- 90-day lifecycle policy applied to bronze-hot
- `forge.bronze_archive_log` DDL migration deployed (`0002_bronze_archive_log.sql`)
- `bronze_hot_partitions` DuckDB view operational
- `bronze_cold_archive` Dagster asset running on 02:00 UTC schedule
- `bronze_expiry_audit` Dagster asset operational (daily)
- Prometheus metrics wired for archive monitoring
- Cold-start sequence updated with bronze-archive steps
- Phase 1 FINAL query benchmarks documented as Phase 2 regression baseline

### Phase 2 Gate (Feature Engineering)
- Gold Iceberg readable by DuckDB
- All dbt models pass
- `forge_compute` produces features
- Null states tested (INSUFFICIENT_HISTORY / SOURCE_STALE / METRIC_UNAVAILABLE)
- PIT audit passes
- Breadth scores verified

### Phase 3 Gate (EDSx Signal)
- All 5 pillars scoring (2 live, 3 planned with null/partial states)
- `marts.signals_history` populated with `computed_at`, `ingested_at`, `regime`, `pillar_scores`, `p_bullish` fields (required for B3 PIT guard — explicit gate criterion)
- Confidence computation correct
- Regime classification (M2-only) operational
- Output contract conformant to §L2.8 response schema
- `regime` field stored at signal emission time (not recomputed at query time)

### Phase 4 Gate (ML Shadow)
- All 5 LightGBM models trained (walk-forward)
- Graduation criteria on OOS (5 hard criteria)
- Shadow mode deployed
- ≥30-day shadow minimum (60–90 day target per F1)
- Shadow period artifacts generated: daily signal snapshots, weekly digests, outcome log
- 48h-delayed public preview live on `fromthebridge.net` (shadow week 2, after Phase 3 EDSx confirmed)

### Phase 5 Gate (Serving)
**Infrastructure:**
- FastAPI service deployed and reachable
- Redistribution filter operational (T0 tests pass — gate-blocking before any external key issued)
- ClickHouse credential isolation verified (D1 structural gate)
- `scripts/init_secrets.sh` executed; no `REPLACE_ME` values remain
- All secrets/ files chmod 600, directory chmod 700

**Entitlement (D2 Tier 0 tests — all required before first external key):**
- T0-1: Coinalyze `pending` → `edsx.pillars.liquidity_flow` + `ml.components.derivatives_pressure` absent; `_redistribution_notice` present; HTTP 200
- T0-2: Coinalyze suppression is surgical (trend_structure + valuation present and populated)
- T0-3: Revoked key returns 401; cache invalidation ≤60s TTL
- T0-4: Free tier cannot reach `/v1/signals` → 403 `endpoint_not_permitted`

**C3 Signal Snapshot Cache (C3-01 through C3-11):**
- All dataclasses, lifespan warm-start, refresh endpoint, readiness endpoint implemented
- Redistribution filter at populate time (Option C)
- Tier filter per-request using thread_7 §4 field gates
- Timing middleware with structured latency logging
- Response envelope fields on all `/v1/signals` responses
- `signal_snapshot_writer` Dagster asset HTTP POST after signal compute
- Prometheus metrics wired
- SIGNAL_CACHE_TTL_SECONDS=32400 in environment config
- **Latency gate: GET /v1/signals full universe p95 < 50ms (cache hit, 121 instruments)**

**B3 Performance Endpoint:**
- Pre-build blocking action completed: EDSx-02 and EDSx-03 R3 backfill `ingested_at` audit result recorded
- `signal_outcomes` dbt model: PIT unit tests pass (synthetic lookahead excluded; backfill guard verified)
- `performance_metrics` mart: all (track, horizon, window) combinations populated for ≥1 instrument
- GET `/v1/signals/performance`: p95 ≤ 500ms
- Null contract: <30 resolved signals → metric objects present with nulls and `min_observations_met: false`
- Tier field redaction: each tier sees exactly B3 field-tier mapping (T0-level test)
- Reliability diagram request-time computation < 50ms

**F1 — Pro tier opens after Phase 5 gate:**
- First external API key issued (Pro tier)
- Pricing page live on `fromthebridge.net`
- API documentation link live
- Performance summary page live (Profile A format)
- Methodology document published at `fromthebridge.net/methodology`

### Phase 6 Gate (Productization)
- Health monitoring operational
- Methodology docs complete (§3.1–§4.5 per B3 mapping — draft during Phase 5, not after)
- ToS audit completed for all 10 v1 sources (Coinalyze ToS audit; redistribution status confirmed or updated)
- Redistribution flags verified for all external-facing signals
- First protocol/institutional customer delivery (direct engagement, written agreement, manual invoicing)
- Annual rotation calendar entry created (March 2027)
- `scripts/init_secrets.sh` and secrets/ directory structure confirmed in CLAUDE.md runbook

---

## 4. Phase 5 Build Prompt Scope

The consolidated Phase 5 build prompt covers three independent deliverable tracks that can be sequenced within Phase 5.

### Track A — Entitlement DDL + Seeding (Phase 5, Step 1)
File: `db/migrations/entitlement/0001_entitlement_schema.sql`

Delivers:
- 12 PostgreSQL tables (D2 schema — corrected TEXT[] types, concurrent_limit column)
- Plan seed data: Free / Pro / Protocol / Institutional rows
- `rate_limit_policies` seed (RPM/RPD/burst/concurrent per tier)
- `plan_endpoint_access` seed (endpoint patterns per tier)
- `plan_field_access` seed (field paths + strip_behavior per tier)
- `plan_lookback_config` seed (lookback_days per tier per endpoint family)
- `plan_instrument_access` seed (access_mode per tier)
- Redistribution source catalog updates: Coinalyze=pending, CoinMetrics=blocked, SoSoValue=blocked

### Track B — Dagster Assets (Phase 5, Step 2)
Requires Track A DDL deployed.

Delivers:
- `forge_redistribution_refresh` asset: walks `source_catalog` → `metric_lineage` → populates `forge.redistribution_blocked_metrics`; triggers on source_catalog changes + daily 02:00 UTC
- `audit_partition_creator` scheduled asset: creates monthly `audit_access_log` partitions at 00:01 on 1st of month
- `signal_snapshot_writer` asset (C3): builds snapshot from Marts → writes `gold/snapshots/latest.json` → HTTP POST to FastAPI `/internal/cache/refresh`

### Track C — FastAPI Serving Layer (Phase 5, Step 3)
Requires Track A DDL + Track B Dagster assets deployed.

Delivers:
- `SignalCache` + `SignalSnapshot` dataclasses (C3)
- FastAPI lifespan with warm-start from MinIO (C3)
- `/internal/cache/refresh` endpoint with INTERNAL_CACHE_TOKEN auth (C3)
- `/healthz/ready` readiness endpoint (C3)
- `apply_redistribution_filter()` at populate time — Option C (C3)
- `EntitlementMiddleware` — 12-step chain (D2)
- Per-request tier filter using thread_7 §4 field gates (C3)
- Timing middleware with structured latency logging (C3)
- Response envelope fields on all `/v1/signals` responses (C3)
- `GET /v1/signals/performance` endpoint (B3): DuckDB reads `performance_metrics` mart; request-time reliability diagram from `signal_outcomes`
- Prometheus metrics wired (C3 + D2)

### Track D — Test Suite (Phase 5, Step 4)
Delivers D2 Tier 0 + Tier 1 + Tier 2 tests + C3 latency gate + B3 PIT unit tests.

---

## 5. Pending Housekeeping (Pre-Execution)

These are documentation updates required before build prompts are issued. No new design decisions — recording what was deferred during the synthesis session.

| Item | File | Change | Blocking For |
|------|------|--------|--------------|
| bronze-archive bucket credential | CLAUDE.md Infrastructure | Add `MINIO_BRONZE_ARCHIVE_USER` to Docker services credential list | Phase 1 CC prompt |
| Internal cache token | CLAUDE.md Infrastructure | Add `/internal/cache/refresh` + `INTERNAL_CACHE_TOKEN` to Phase 5 notes | Phase 5 CC prompt |
| Dagster service definition | docker-compose.yml | Add Dagster webserver/daemon/code server services (already flagged Phase 0 gap) | Phase 1 CC prompt |
| Dagster mount scope | docker-compose.yml | Add `MINIO_BRONZE_ARCHIVE_USER` mount to `empire_dagster_code` scoped to archive asset | Phase 1 CC prompt |
| signals_history gate | thread_6 Phase 3 gate | Explicitly require `computed_at`, `ingested_at`, `regime`, `pillar_scores`, `p_bullish` in gate criteria | Phase 3 CC prompt |
| regime emit-time | thread_2 §L2.8 | Explicit statement: `regime` stored at signal emission time in signal record | Phase 3 CC prompt |
| Methodology doc scope | Phase 5 completion checklist | §3.1–§4.5 draft as parallel Phase 5 deliverable (avoid Phase 6 bottleneck) | Phase 6 gate |
| Stripe deferred | design_index Known Gaps | Add "Stripe / billing infrastructure — deferred, trigger: 20+ active subscribers" | Phase 6 gate |

---

## 6. Key Numbers at a Glance

| Parameter | Value |
|-----------|-------|
| v1 active sources | 10 |
| Metrics in catalog | 74 (Phase 0) + 3 E3 + 4 E4 = **81** |
| Instruments covered | 121 (derivatives domain) |
| Signal cadence | 6h |
| Worst-case freshness path | 44 min (vs. 90min SLA) |
| Bronze volume (steady state) | ~2 GB (hot) + ~8 GB/year (archive) |
| Silver volume (daily) | ~5,800–6,000 rows |
| Performance mart rows | ~7,380 (121 instruments × 60 combinations) |
| Signal snapshot heap | ~250 KB (121 instruments) |
| Phase 5 serving latency target | p95 < 50ms (cache hit, full universe) |
| Phase 5 performance endpoint SLA | p95 ≤ 500ms |
| Total build estimate | 13–20 weeks (Phase 1 → Phase 6) |
| Pro tier price | $199/month |
| Protocol founding rate | $4,500 (one-time) |
| Institutional tier | $2,500/month |
| Monthly OPEX | ~$85 |
| Break-even (6 months) | 10 Pro subscribers OR 1 protocol engagement |

---

## 7. Architecture Invariants — Still Intact

All three hard rules and all design invariants confirmed intact after 9 locked results:

**Rule 1 — One-way gate:** Data flows down only. No session result changes this. C3 cache reads Marts (Layer 6). B3 reads Marts. C2 archive is outside the normal pipeline.

**Rule 2 — ClickHouse write-only:** D1 structural credential isolation enforces this. FastAPI has no ClickHouse credentials. `ch_export_reader` mounted only on export asset. C3 cache populates from MinIO, not ClickHouse.

**Rule 3 — No time series in PostgreSQL:** `forge.bronze_archive_log` (C2) is admin metadata only — no `observed_at + value` columns confirmed. `forge.audit_access_log` (D2) stores operational events, not metric observations.

**Schema immutability post-Phase 0:** `defi.lending.utilization_rate` methodology update (E3) changed the `methodology` field only — no DDL changes. Canonical metric name unchanged. Rule holds.

**S3 migration path:** Both C2 buckets are S3-compatible (zero-code migration). C3 MinIO snapshot path is S3-compatible. D1 lifecycle policy XML is compatible with AWS S3 lifecycle configuration syntax.

---

*All locked decisions are authoritative. This document is a synthesis artifact — the thread files are canonical for build execution.*
