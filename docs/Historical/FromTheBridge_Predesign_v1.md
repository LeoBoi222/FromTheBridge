**Empire: Complete System Design — Top-Down Specification**  
**Date:** 2026-03-04  
   
 **Type:** Multi-thread comprehensive architecture design  
   
 **Owner:** Stephen (architect, sole operator)  
   
 **Mandate:** Design the complete system from revenue outputs to data sources. Every layer contracted to the one above it. No code until the full specification is complete. No drift from specification during build.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANklEQVR4nO3OMQ2AABAAsSNBCkLfFDZwwIgHRiywEZJWQZeZ2ao9AAD+4lyruzq+ngAA8Nr1AOH0BedHjjlfAAAAAElFTkSuQmCC)  
**Your Role**  
You are designing a complete market intelligence platform from scratch. Assume nothing exists. The fact that infrastructure and collectors already run is an implementation advantage — but the design must not be constrained by what was built before. If the right design requires discarding existing work, so be it.  
You need to operate as:  
- **Product architect** — what do customers pay for, what are the revenue streams  
- **Data architect** — what is the canonical data model, how is it organized, how does it scale  
- **Database engineer** — schema design, time-series optimization, query performance  
- **Systems architect** — how do layers connect, what are the contracts between them  
- **Data scientist** — what features are computable, what signal generation approaches work, what validation proves they work  
- **Financial engineer** — derivatives, macro, flows, volatility — the domain semantics of market data  
Every decision must be justified by what the layer above requires. No speculative infrastructure. No "we might need this."  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANElEQVR4nO3OQQmAUBBAwSd8bOHVnBvBkAaxgjcRZhLMNjNHdQUAwF/cq9qr8+sJAACvrQctgQNH4A++9QAAAABJRU5ErkJggg==)  
**Design Philosophy**  
**Top-Down, Consumer-First**  
The system is designed from the top (revenue) downward (sources). Each layer exists only because the layer above it requires specific inputs. The design sequence:  
Layer 7: Revenue Streams      — What customers pay for  
 Layer 6: Output Products       — Concrete deliverables (API, signals, content, data feeds)  
 Layer 5: Signal Generation     — Scoring, ML models, composite signals  
 Layer 4: Feature Engineering   — Computed features, transforms, aggregations  
 Layer 3: Data Universe         — Normalized, auditable, canonical data store  
 Layer 2: Normalization         — Source adapters, cleaning, validation  
 Layer 1: Raw Collection        — Agents pulling from external sources  
 Layer 0: Sources               — External APIs, WebSockets, bulk files  
   
Each layer's specification is a contract: "I provide X to the layer above me. I require Y from the layer below me. Here is my exact interface."  
**No Drift Principles**  
1. **Schema is immutable once specified.** Adding a new source or metric adds catalog entries, not columns or tables.  
2. **Every layer has a contract.** Input format, output format, error handling, null handling — all specified before build.  
3. **Adapters absorb source variance.** If a source returns data in an unexpected format, the adapter handles it. Nothing above the adapter ever sees source-specific structure.  
4. **Validation is structural.** Every value that enters the system is checked against its metric definition. Out-of-range, wrong type, wrong cadence — rejected with audit trail, not silently stored.  
5. **No build without complete spec.** Each layer's specification is reviewed and approved before implementation begins. Implementation delivers exactly what was specified.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OMQ2AABAAsSNBCUpfD6ZYGZDAgAU2QtIq6DIzW7UHAMBfHGt1V+fXEwAAXrseHCoGAe/SKtAAAAAASUVORK5CYII=)  
**LAYER 7: Revenue Streams**  
The design starts here. Everything below exists to serve these.  
**What do customers pay for?**  
Define this explicitly. Candidates:  
**A. Data-as-a-Service**  
- Normalized, auditable, multi-domain market data via API  
- Historical data with guaranteed PIT integrity  
- Metric catalog with documented methodology  
- Competitive with: Glassnode, Coin Metrics, Messari  
- Revenue model: subscription tiers by data depth, resolution, and metric count  
**B. Intelligence-as-a-Service**  
- Directional signals (bullish/neutral/bearish per instrument)  
- Regime classification (risk-on, risk-off, transition)  
- Composite scores with confidence intervals  
- Alert-based delivery (Telegram, email, webhook)  
- Revenue model: subscription by signal count, frequency, and instrument coverage  
**C. Content Products**  
- Daily/weekly market briefs  
- Event-driven analysis (FOMC, ETF approvals, liquidation cascades)  
- Portfolio-level intelligence  
- Revenue model: subscription, potentially ad-supported content  
**D. Portfolio Intelligence**  
- Position-level recommendations with conviction scoring  
- Risk-adjusted sizing  
- Entry/exit signals with backtested performance  
- Revenue model: performance-based or premium subscription  
**Design Session Must Decide:**  
- Which revenue streams are primary? (affects everything downstream)  
- Which are v1 vs. future?  
- What is the minimum viable product for first revenue?  
- Does Empire sell data, signals, or both? The answer determines whether Layer 3 (Data Universe) or Layer 5 (Signal Generation) is the product surface.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANElEQVR4nO3OQQmAABRAsSdYxKY/jbnMIJ7FCt5E2BJsmZmt2gMA4C+Otbqr8+sJAACvXQ85TgYRMv3/cwAAAABJRU5ErkJggg==)  
**LAYER 6: Output Products**  
For each revenue stream selected in Layer 7, define the concrete product:  
**For each output:**  
- **Format:** API endpoint? Webhook? Dashboard? File? Content?  
- **Schema:** Exact response structure. What fields, what types, what ranges.  
- **Frequency:** Real-time? Hourly? Daily?  
- **Coverage:** Which instruments? Which metrics? Which asset classes?  
- **SLA:** Freshness guarantee. Availability target. Error handling.  
- **Authentication:** API keys? Subscription tiers? Rate limits?  
- **Documentation:** Every metric documented with methodology, source, limitations.  
**Example (if Data-as-a-Service is primary):**  
GET /v1/timeseries/metrics?instrument=BTC&metrics=funding_rate,open_interest&start=2024-01-01&end=2026-03-01&interval=1d  
   
 Response:  
 {  
   "instrument": "BTC",  
   "data": [  
     {  
       "timestamp": "2024-01-01T00:00:00Z",  
       "funding_rate": { "value": 0.0001, "source": "coinalyze", "confidence": 1.0 },  
       "open_interest": { "value": 24500000000, "source": "coinalyze", "confidence": 1.0 }  
     }  
   ],  
   "metadata": {  
     "funding_rate": { "unit": "rate_8h", "range": [-0.05, 0.05], "description": "..." },  
     "open_interest": { "unit": "usd", "range": [0, null], "description": "..." }  
   }  
 }  
   
**Example (if Intelligence-as-a-Service is primary):**  
GET /v1/signals/instrument?instrument=BTC  
   
 Response:  
 {  
   "instrument": "BTC",  
   "timestamp": "2026-03-04T12:00:00Z",  
   "signal": {  
     "direction": "bullish",  
     "confidence": 0.73,  
     "magnitude": 0.45,  
     "horizon": "14d",  
     "regime": "risk_on"  
   },  
   "components": {  
     "derivatives_pressure": { "direction": "bullish", "weight": 0.40, "confidence": 0.81 },  
     "capital_flows": { "direction": "neutral", "weight": 0.35, "confidence": 0.65 },  
     "defi_health": { "direction": "neutral", "weight": 0.15, "confidence": null },  
     "macro_context": { "direction": "bullish", "weight": 0.10, "confidence": 0.70 }  
   }  
 }  
   
The output product schema defines EVERYTHING below it. The signal response structure determines what Signal Generation must produce. The data API response structure determines what the Data Universe must contain.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OsQ1AABRAwSdRaPXGMOCv7WkPK+hEcjfBLTNzVFcAAPzFvVZbdX49AQDgtf0BSpoDXv5TGXgAAAAASUVORK5CYII=)  
**LAYER 5: Signal Generation**  
Exists only if revenue includes intelligence/signals. Otherwise skip to Layer 4.  
**Two independent signal tracks (existing architecture decision M1 — validate whether this still holds):**  
**Track A: Deterministic Scoring (EDSx)**  
- Rule-based pillar scoring  
- Transparent, auditable, explainable  
- Per-instrument scores + regime classification  
- What pillars? What inputs per pillar? What output format?  
- How many pillars? What are their weights?  
- How is the composite formed?  
- What validation proves it works?  
**Track B: ML Models**  
- 5 Layer 1 domain models → Layer 2 synthesis  
- Probabilistic, data-driven  
- Per-instrument probability vectors + magnitude + confidence  
- What models? What inputs? What output format?  
- How is training validated?  
- What is the graduation path from shadow to production?  
**Contract to Layer 6:**  
Signal Generation must produce exactly the output schema that Layer 6 requires. If Layer 6 needs { direction, confidence, magnitude, horizon, regime }, then Signal Generation's output contract is exactly that structure — no more, no less.  
**Contract from Layer 4:**  
Signal Generation consumes features. The feature contract specifies exactly which features, at what granularity, at what cadence, with what null handling.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OYQ1AABSAwY9JoICqL4Z8Ikiggn9mu0twy8wc1RkAAH9xbdVa7V9PAAB47X4A9CgEJQFjJ/EAAAAASUVORK5CYII=)  
**LAYER 4: Feature Engineering**  
Transforms normalized data into model-ready features. Shared by both signal tracks (Layer 0 shared features per M1 decision — validate).  
**Feature categories (from existing ML architecture):**  
- A: Rolling transforms (value → window → statistic)  
- B: Cross-sectional ranks (value across instrument universe)  
- C: Ratio / interaction features  
- D: Regime / state labels  
- E: Calendar / structural features  
- F: Breadth / aggregation (per-instrument → market aggregate)  
- G: Cross-asset features  
**For each feature:**  
- **Name** (canonical, unique)  
- **Category** (A-G)  
- **Input metrics** (exact metric names from Layer 3)  
- **Computation** (exact formula or algorithm)  
- **Output type** (continuous, categorical, binary)  
- **Granularity** (per-instrument, per-protocol, market-level)  
- **Window sizes** (if rolling)  
- **Null handling** (what happens when input is missing)  
- **PIT constraint** (can this feature only use data available at computation time?)  
**Contract to Layer 5:**  
Feature Engineering produces a feature matrix: (instrument, timestamp, feature_name, value) satisfying PIT constraints. Every feature is documented, reproducible, and deterministic.  
**Contract from Layer 3:**  
Feature Engineering reads normalized metrics from the Data Universe. It never touches raw source data. It never needs to know where a metric came from.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OMQ2AABAAsSNBCUrfDqrYGVDAgAU2QtIq6DIzW7UHAMBfHGt1V+fXEwAAXrseHCQGBEuErVgAAAAASUVORK5CYII=)  
**LAYER 3: Data Universe**  
The canonical, normalized, auditable market data store. The center of the system.  
**Core Design Questions:**  
**Schema model:**  
- Entity-Attribute-Value (EAV): (entity, metric, timestamp, value) — maximally flexible, potentially slow for wide queries  
- Hybrid: typed tables for different observation patterns (instrument metrics, protocol metrics, market-level metrics, event data)  
- Wide tables: one column per metric — fast reads, schema changes when metrics added  
**Metric catalog:**  
- Every metric fully defined before any data enters the system  
- Definition includes: name, domain, granularity, value type, unit, expected range, source(s), cadence, computation method (if derived), PIT handling  
- Catalog is the schema. If it's not in the catalog, it doesn't exist.  
**Point-in-time integrity:**  
- How are late revisions handled?  
- How is backfill distinguished from real-time collection?  
- What timestamp semantics? (observation time, collection time, insertion time)  
**Validation:**  
- Range checks against metric definitions  
- Completeness checks (expected cadence vs. actual)  
- Cross-metric consistency checks  
- Continuous, not ad-hoc  
**Asset-class extensibility:**  
- Crypto today. Equities, commodities, forex tomorrow.  
- Instrument is an instrument. Price is a price. Rate is a rate.  
- Schema doesn't change when asset class expands.  
**Contract to Layer 4:**  
The Data Universe provides (entity, metric, timestamp, value) with full provenance. Features read from this interface exclusively.  
**Contract from Layer 2:**  
The Data Universe receives cleaned, validated, normalized data from the adapter layer. Every value arrives with source tag, collection timestamp, and has passed validation.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAAM0lEQVR4nO3OMQ0AIAwAwZKQ6kBqjSAOJywYYCIkd9OP36pqRMQMAAB+sfqJfLoBAMCN3NYoAzBA+QG0AAAAAElFTkSuQmCC)  
**LAYER 2: Normalization & Validation**  
Adapters that transform raw source-shaped data into the canonical universe model.  
**For each source, an adapter that:**  
1. Maps source-specific fields to canonical metric names  
2. Converts units (wei → ETH, basis points → rate, etc.)  
3. Validates values against metric definitions (range, type, nullability)  
4. Rejects invalid data to dead letter queue with audit trail  
5. Handles source-specific quirks (rate limits, pagination, deduplication)  
6. Tags every value with source identifier and collection timestamp  
**Adapter pattern:**  
- One adapter per source  
- Adding a new source = writing a new adapter  
- Adapter changes never propagate above Layer 2  
- If a source changes its API response format, only its adapter changes  
**Contract to Layer 3:**  
Adapters produce (entity, metric, timestamp, value, source, collected_at, validated) — the exact input format Layer 3 expects.  
**Contract from Layer 1:**  
Adapters read from raw collection tables. They do not call external APIs.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OMQ2AABAAsSPBCj5fFyM6mJHAjAU2QtIq6DIzW7UHAMBfnGt1V8fXEwAAXrsexOEF35f1aEgAAAAASUVORK5CYII=)  
**LAYER 1: Raw Collection**  
Agents that pull data from external sources and store it in source-native format.  
**What exists (keep running):**  
- Coinalyze agent → derivatives data (8h cadence, 121 instruments)  
- Explorer agent → exchange flows (8h, 18 instruments)  
- DeFiLlama agent → DeFi protocols, stablecoins, lending, DEX (12h)  
- FRED agent → macro indicators (daily, 23 series)  
- ETF agent → ETF flows (12h, BTC/ETH/SOL)  
- Liquidation collector → WebSocket real-time on srv-rack-02  
- EDS legacy → Tiingo OHLCV, CoinPaprika (in TimescaleDB/PostgreSQL)  
**What may need to be added (determined by Layers above):**  
- Additional sources identified by the gap between what Layer 3 needs and what Layer 1 provides  
- Backfill jobs for historical depth  
- New agents for new asset classes (future)  
**Contract to Layer 2:**  
Raw collection stores source-native data in landing tables. Adapters read from these. Format is whatever the source provides — the adapter handles normalization.  
**Principle:**  
Raw data is never modified after collection. It's an audit trail. If a source sends garbage, it's stored as garbage and the adapter rejects it into dead letters during normalization. The raw layer is append-only evidence.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OMQ2AABAAsSNhZscYahheJwqQgQU2QtIq6DIze3UGAMBf3Gu1VcfXEwAAXrseoqcEQXyAWBgAAAAASUVORK5CYII=)  
**LAYER 0: Sources**  
External data providers. Not designed — evaluated and selected.  
**For each source:**  
- **What it provides** (metrics, instruments, cadence, history depth)  
- **Terms of service** (commercial use, redistribution, attribution)  
- **Reliability** (uptime, consistency, schema stability)  
- **Cost** (free tier limits, paid tier pricing)  
- **Alternatives** (what replaces it if it disappears)  
**Current sources:**  
| | | | | |  
|-|-|-|-|-|  
| **Source** | **Tier** | **Cost** | **ToS Risk** | **Data Domains** |   
| Tiingo | T1 | Free | Low | OHLCV |   
| Coinalyze | T1 | Free | Unaudited | Derivatives |   
| DeFiLlama | T1 | Free | Low | DeFi, stablecoins, DEX, lending |   
| FRED | T1 | Free | None | Macro |   
| SoSoValue | T1 | Free | Non-commercial only | ETF flows |   
| CoinPaprika | T1 | Free | Low | Market data |   
| Etherscan V2 | T2 | Free | Unaudited | Exchange flows (ETH+Arb only) |   
| Binance bulk | — | Free | VPN required | Historical derivatives metrics |   
| CoinMetrics community | — | Free | Community terms | BTC+ETH on-chain |   
| Binance WebSocket | — | Free | VPN required | Real-time liquidations |   
   
**ToS audit required before any external data product (existing decision PL-L2).**  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANElEQVR4nO3OQQmAABRAsad4FCtY9ecwnkms4E2ELcGWmTmrKwAA/uLeqrU6vp4AAPDa/gDzUgM9+S8z3AAAAABJRU5ErkJggg==)  
**CROSS-CUTTING CONCERNS**  
**Audit Trail**  
Every value in the system must be traceable from output product back to source. Given a signal or data point served to a customer, you must be able to answer:  
- What metric values produced this?  
- Where did each metric value come from?  
- When was it collected?  
- When was it ingested?  
- Did it pass validation?  
- Has it been revised since original ingestion?  
**Monitoring & Health**  
- Agent health: collection success rate, latency, freshness  
- Normalization health: rejection rate, dead letter volume, adapter lag  
- Universe health: completeness (expected vs. actual observations), value distribution drift  
- Signal health: prediction accuracy, confidence calibration, regime performance  
- System health: database performance, storage growth, service uptime  
**Error Handling**  
Every layer has a defined error contract:  
- What happens when a source is down?  
- What happens when data fails validation?  
- What happens when a feature can't be computed (missing input)?  
- What happens when a model can't produce a signal?  
- What does the customer see when upstream data is unavailable?  
Nulls propagate honestly, with coverage tracking. No invented data. No stale data presented as current.  
**Scalability**  
- Adding instruments: register in asset catalog, adapters pick them up  
- Adding metrics: register in metric catalog, add computation if derived  
- Adding sources: write adapter, map to existing metrics  
- Adding asset classes: register instruments, map existing domain concepts (price, volume, OI, flows)  
- Adding consumers: read from Data Universe or Signal Generation via defined interfaces  
None of these require schema changes.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANElEQVR4nO3OQQmAUBBAwSf8GGLWDWFDY3ixgjcRZhLMNjNHdQYAwF9cq1rV/vUEAIDX7gcRXAQ2s/16gwAAAABJRU5ErkJggg==)  
**EXISTING ASSETS TO EVALUATE**  
The design assumes starting from scratch, but implementation can reuse:  
**Keep as-is:**  
- Infrastructure (proxmox, srv-rack-02, bluefin, networking)  
- Docker Compose orchestration  
- Database engines (PostgreSQL, TimescaleDB)  
- Cloudflare tunnel + domain  
**Keep but potentially restructure:**  
- Forge agents (collection works, but may need to write to different landing tables)  
- Forge raw tables (become Layer 1 landing zone)  
- TimescaleDB OHLCV data (normalize into universe)  
- Backfilled data (Coinalyze 5yr, Binance bulk, FRED decades, CoinMetrics)  
- Asset taxonomy and event calendar (move to universe metadata)  
**Evaluate — keep, replace, or discard:**  
- forge_compute (either becomes Layer 4 or is replaced by feature engineering layer)  
- EDSx-03 (either adapts to read from universe or is rebuilt)  
- EDS legacy pipeline (EDS → MAE → CAA → W6)  
- Contract infrastructure (CON-01–04)  
- Content engine (consumer — adapts to new data interfaces)  
- Bridge UI (consumer — adapts)  
**Discard if design requires:**  
- Source-shaped Forge raw tables (replaced by properly designed landing zone + universe)  
- forge.computed_metrics (replaced by feature engineering layer)  
- Any table or component that encodes source-specific structure above the adapter layer  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAALUlEQVR4nO3OQQ0AIAwEsAMlSJ0UrOFkGngRklZBR1WtJDsAAPzizNcDAADuNcKwAyU+nb+5AAAAAElFTkSuQmCC)  
**DESIGN SESSION STRUCTURE**  
This is a multi-thread design. Each thread produces concrete deliverables. The architect (Stephen) reviews and approves between threads. No thread begins until the previous thread's output is approved.  
**Thread 1: Revenue & Product Definition (Layers 7 + 6)**  
**Input:** This document.  
   
 **Output:**  
- Defined revenue streams with priority ranking  
- Exact output product specifications (API schemas, response formats)  
- Customer personas and use cases  
- MVP scope — what ships first for first revenue  
**Why first:** Everything below is shaped by this. If the product is data, the Data Universe IS the product surface. If the product is signals, the Data Universe is internal infrastructure. Different answer, different design.  
**Thread 2: Signal Architecture (Layer 5)**  
**Input:** Thread 1 output (product specs).  
   
 **Output:**  
- Signal generation architecture (what tracks, what models, what pillars)  
- Input/output contracts for each signal component  
- Validation methodology (how do we know signals work)  
- Feature requirements (what Layer 4 must provide)  
**Skip if:** Revenue is data-only with no signal/intelligence products.  
**Thread 3: Feature Engineering (Layer 4)**  
**Input:** Thread 2 output (feature requirements) or Thread 1 output (if data-only product).  
   
 **Output:**  
- Complete feature catalog with formulas  
- Computation architecture (batch, streaming, triggered)  
- PIT constraints per feature  
- Data requirements (what Layer 3 must provide)  
**Thread 4: Data Universe (Layer 3)**  
**Input:** Thread 3 output (data requirements).  
   
 **Output:**  
- Schema DDL  
- Metric catalog structure and seed data  
- PIT strategy  
- Query pattern verification against all consumer needs  
- Asset-class extensibility proof  
**Thread 5: Normalization + Collection (Layers 2 + 1)**  
**Input:** Thread 4 output (universe schema and metric catalog).  
   
 **Output:**  
- Adapter pattern and implementation for each current source  
- Landing zone table design  
- Validation rules per metric  
- Source gap analysis (what universe needs vs. what sources provide)  
- Backfill and migration plan  
**Thread 6: Integration & Build Plan**  
**Input:** All previous threads.  
   
 **Output:**  
- Complete build sequence (what gets built in what order)  
- Phase gates (what must work before next phase starts)  
- Migration plan from existing system  
- Parallel operation plan (old system runs while new builds)  
- Timeline estimates  
- Risk register  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANklEQVR4nO3OMQ2AABAAsSPBCj7fFRYQwYwEZiywEZJWQZeZ2ao9AAD+4lyruzq+ngAA8Nr1AMTJBeJDClAyAAAAAElFTkSuQmCC)  
**EXISTING DATA AUDIT RESULTS**  
The Forge data quality audit (run 2026-03-04) provides ground truth for what exists:  
**Data That Exists and Is Healthy:**  
| | | | |  
|-|-|-|-|  
| **Data** | **Rows** | **History** | **Quality** |   
| Derivatives (Coinalyze) | 185,066 | 2021-03 → present | GREEN (3 instruments with extreme funding rates need filtering) |   
| Macro (FRED) | 140,261 | 1950 → present | GREEN |   
| DEX Volume (DeFiLlama) | 88,239 | 2016 → present | GREEN |   
| Chain Activity (CoinMetrics) | 10,137 | 2009 → present | GREEN |   
| Lending Fees (DeFiLlama) | 9,651 | 2019 → present | GREEN |   
| Exchange Flows (Explorer) | 2,177 | 2026-01 → present | RED (Gate.io values in wei, not ETH) |   
| ETF Flows (SoSoValue) | 774 | 2024-12 → present | GREEN |   
| DeFi Protocols (DeFiLlama) | 195 | ~8 days | GREEN (but shallow — no backfill) |   
| Stablecoin Metrics (DeFiLlama) | 180 | ~8 days | GREEN (but shallow — no backfill) |   
| OHLCV (TimescaleDB, Tiingo) | ~800k+ | BTC from 2014, ETH from 2015 | Not audited yet |   
   
**Known Bugs (fixable):**  
1. forge_compute missing GRANTs on 3 tables (76.7% failure rate)  
2. computed_metrics partition range too narrow for historical data  
3. Explorer agent wei→ETH conversion for Gate.io _OTHER  
4. 3 instruments with extreme funding rate values (ANKR, FRAX, OGN — likely real but need validation rules)  
**Structural Issues (the reason for redesign):**  
1. 12 permanently null columns across 5 tables (source-shaped schema waste)  
2. Each table has different column names, different timestamp conventions, different entity identifiers  
3. No cross-table audit query possible without source-specific joins  
4. No validation against expected ranges at ingestion time  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANklEQVR4nO3OYQ1AABSAwc8mi5wvlAB6CKCAACr4Z7a7BLfMzFYdAQDwF+da3dX+9QQAgNeuB6fWBdZMUxZ2AAAAAElFTkSuQmCC)  
**EXISTING ARCHITECTURE DECISIONS TO VALIDATE**  
These were made during bottom-up development. The top-down redesign should evaluate each:  
| | | |  
|-|-|-|  
| **Decision** | **Description** | **Validate?** |   
| M1 | EDSx and ML fully independent. Shared Layer 0 only. | Does this still make sense with a unified Data Universe? |   
| M3 | 5 Layer 1 domain models | Are these the right domains given the product definition? |   
| M5 | 14-day horizon, volume-adjusted labels | Is this the right target for the revenue product? |   
| M9 | LightGBM for all models | Still appropriate? |   
| D6 | 4 sub-scores for EDSx | Does pillar structure survive top-down redesign? |   
| D10 | Methodology-driven validation (not calendar) | Keep. |   
| D41 | Three universe tiers (collection/scoring/trading) | Does this map to the new architecture? |   
   
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANElEQVR4nO3OMQ0AIAwAwZIgBKnVgjN8dGDBABMhuZt+/JaZIyJmAADwi9VP1NMNAABu1AaU3AUhiyfJeAAAAABJRU5ErkJggg==)  
**SUCCESS CRITERIA**  
The complete design is done when:  
1. **Revenue to source traceability:** You can trace any customer-facing output back through signals → features → metrics → raw data → source, and every link is a defined contract.  
2. **Schema immutability:** Adding a new data source, metric, instrument, or asset class requires zero schema changes — only catalog entries and adapters.  
3. **Audit completeness:** Every value in the system has: source, collection timestamp, ingestion timestamp, validation status, and revision history (if PIT).  
4. **Consumer ignorance:** No consumer above Layer 2 ever needs to know where data came from. Changing from Coinalyze to CoinGlass changes one adapter. Nothing else moves.  
5. **Validation is structural:** Out-of-range values are rejected at ingestion, not discovered months later by audit scripts.  
6. **Build plan is phased and gated:** Each phase has explicit deliverables and pass/fail criteria before the next phase begins.  
7. **One operator viability:** The system is operationally simple enough for one person to run, debug, and extend.  
8. **No unnamed gaps:** Every known data gap has a documented plan (source to acquire, timeline, or accepted permanent gap with impact assessment).  
