# Layers Overview (Canonical)

This document describes the canonical 7‑layer architecture for the Canonical Investment Data Platform.  
Each layer has: purpose, responsibilities, inputs, outputs, and “done” criteria.  
Layers are numbered from the bottom (L1) to the top (L7).

---

## L1 – Data Sources

**Purpose**  
Define and manage all upstream data sources (vendors and user data) that feed the platform.

**Responsibilities**  
- Enumerate and document each external provider (coverage, latency, licensing, SLAs).  
- Specify data types: market data (quotes, trades, order book), fundamentals, macro, alt‑data, user holdings/transactions.  
- Maintain a catalog of endpoints / file formats / update schedules.  
- Provide realistic constraints (rate limits, historical depth).

**Typical Inputs**  
- Vendor APIs (HTTP/WebSocket).  
- Vendor file drops (CSV/Parquet over SFTP/object storage).  
- User‑linked accounts (e.g., Plaid investments).

**Outputs**  
- A machine‑readable catalog of all sources (e.g., `sources.yaml`).  
- Sample payloads and schemas for each source.  
- Defined backfill ranges per source (e.g., 10+ years US equities EOD).

**Done Criteria**  
- Every initial vendor/source is listed with: auth, limits, coverage, refresh cadence, sample payload.  
- Backfill strategy is defined per vendor (how far back, in what chunks).  
- Quality checks required at ingestion are documented (dupes, gaps, anomalies).

---

## L2 – Ingestion Pipelines

**Purpose**  
Reliably pull data from L1 sources into the platform’s raw storage (Bronze), with basic validation.

**Responsibilities**  
- Implement batch pipelines (e.g., Dagster/Airflow) for daily/hourly loads.  
- Implement streaming pipelines (Kafka/Flink or equivalent) for real‑time feeds.  
- Apply initial schema validation and quality checks.  
- Handle backfills and re‑runs idempotently.

**Typical Inputs**  
- L1 vendor endpoints, files, and user data connectors.  
- Source catalog and schemas from L1.

**Outputs**  
- Raw, vendor‑shaped data files/streams in Bronze (e.g., partitioned Parquet/Delta).  
- Ingestion logs and basic quality metrics (row counts, error counts, anomalies).  
- Metadata linking raw files to source, time, and job.

**Done Criteria**  
- For each initial source, there is at least one ingestion job (batch or streaming) that lands data into Bronze with monitoring.  
- Ingestion can be re‑run without corrupting history (idempotent or versioned).  
- Backfill for initial scope (e.g., US equities EOD 10+ years) can be executed and verified.

---

## L3 – Lakehouse (Bronze / Silver / Gold)

**Purpose**  
Store all historical data in a stable, query‑ready form, with clearly defined schemas and partitions.

**Responsibilities**  
- Bronze: Immutable raw data as landed from L2, with minimal changes.  
- Silver: Cleaned, normalized, vendor‑agnostic tables (e.g., instruments, prices, corporate actions, macro, transactions).  
- Gold: Aggregated and feature‑rich datasets (factor panels, summaries) optimized for analytics and serving.  
- Enforce schema stability and partitioning strategy.

**Typical Inputs**  
- Raw files/streams from L2.  
- Data model definitions and canonical schemas.

**Outputs**  
- Bronze tables/files partitioned by date/vendor.  
- Silver tables with stable schemas for multi‑asset support.  
- Gold tables for downstream analytics and API consumption.

**Done Criteria**  
- All canonical Silver tables defined in `architecture.yaml` exist and are populated for the initial asset scope.  
- Schemas are version‑controlled and treated as immutable post‑MVP (changes require version bump).  
- Queries from L4–L5 can rely on Silver/Gold tables without referencing Bronze directly.

---

## L4 – Semantic Layer (Data Marts)

**Purpose**  
Expose business‑oriented views and data marts tailored to products, tiers, and use‑cases.

**Responsibilities**  
- Implement dbt (or equivalent) models that transform Silver/Gold into curated views.  
- Create separate marts/views for different user tiers and segments (Free, Pro, Elite).  
- Encapsulate business logic (e.g., “Pro daily panel”, “Elite intraday panel”, “Casual fundamentals view”).  
- Attach rich metadata (descriptions, owners, tests).

**Typical Inputs**  
- Silver and Gold tables from L3.  
- Business rules from product and analytics.

**Outputs**  
- dbt models and materialized views/tables per product need.  
- Data dictionary describing each mart (fields, definitions, refresh cadence).  
- Tests for data freshness, integrity, and business rules.

**Done Criteria**  
- Each initial product tier has at least one dedicated mart that fully supports its promised features.  
- dbt tests pass (schema, uniqueness, referential integrity, basic business checks).  
- L5 APIs/UI endpoints read only from L4 marts for business data (not directly from Silver).

---

## L5 – Serving Layer (APIs & UI)

**Purpose**  
Provide user‑facing interfaces—APIs and web UI—on top of the semantic layer, enforcing auth and tiers.

**Responsibilities**  
- Implement REST/GraphQL APIs mapped to L4 marts (quotes, factors, macro, portfolio, backtests, lineage).  
- Build the main web application (e.g., Next.js/Tailwind) with dashboards, screeners, analytics, transparency views.  
- Enforce authentication, authorization, and rate limits per tier.  
- Provide docs, SDK examples, and a developer‑friendly experience.

**Typical Inputs**  
- L4 marts and dictionaries.  
- User auth/tier information.  
- Backtesting/analytics services (L6).

**Outputs**  
- Stable API contracts (OpenAPI/GraphQL schema).  
- Production UI for Free/Pro/Elite tiers.  
- Usage metrics (per endpoint, per user, per tier).

**Done Criteria**  
- Pro and Elite features listed in `architecture.yaml` are accessible via documented APIs and UI pages.  
- APIs have tests and mocks; UI has basic E2E tests.  
- Rate limiting and tier enforcement are in place for all monetized endpoints.

---

## L6 – Analytics & Compute Engines

**Purpose**  
Compute factors, signals, and model outputs that power advanced analytics and features.

**Responsibilities**  
- Implement batch factor pipelines (e.g., daily cross‑sectional factors, risk metrics).  
- Implement streaming or near‑real‑time computation for intraday signals.  
- Train and deploy models (e.g., for scoring, recommendations, anomaly detection).  
- Write outputs back into Gold (and, where needed, marts in L4).

**Typical Inputs**  
- Silver/Gold tables from L3.  
- Configuration of factor definitions and model parameters.  
- Historical data for training and backtesting.

**Outputs**  
- Factor time‑series, scores, and model outputs in Gold.  
- Backtest results exposed via APIs.  
- Performance and quality metrics for factors/models.

**Done Criteria**  
- Initial factor library (e.g., 20+ factors) computed and stored for target universes.  
- Backtesting paths exist for at least one example strategy per tier.  
- Factors and signals used in L5 features have defined

