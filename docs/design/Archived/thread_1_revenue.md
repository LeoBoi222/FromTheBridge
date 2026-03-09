# Thread 1: Revenue & Product Definition
## FromTheBridge — Empire Architecture v1.0

*Part of the modular design spec. See `docs/design_index.md` for the full index.*

---

## REVENUE ARCHITECTURE

Three economically distinct positions exist in market data:

**Position 1 — Infrastructure provider:** Clean, normalized, auditable data. Customers are builders. Layer 3 is the product surface. Competitors: Glassnode, Coin Metrics, Kaiko.

**Position 2 — Intelligence provider:** Interpretation and signals. Customers are decision-makers. Layer 5 is the product surface. Competitors: research desks, quant shops.

**Position 3 — Workflow product:** Complete tool — dashboard, alerts, briefs. Customers are operators. The UI is the product surface.

**FromTheBridge occupies Position 2 with Position 1 as the structural foundation.** The existing EDSx + ML architecture, pillar structure, and prediction targets all point to an intelligence product. The data layer is internal infrastructure that becomes an additional revenue surface, not the primary product.

---

## REVENUE STREAMS (MULTI-STREAM FROM DAY ONE)

**Stream 1 — Direct subscriptions (tiered)**
Prosumer to institutional tiers. Dashboard + API access gated by feature depth and history. Lowest friction to start.

**Stream 2 — API data licensing (B2B)**
Counterparties pay for normalized, auditable, attributed data to power their own products. They don't use the interface — they build on the data layer. Customers: exchanges, funds, fintech builders, index providers. Higher ACV, fewer customers, relationship-driven. Layer 3 is the product surface for this stream.

**Stream 3 — Protocol / ecosystem reporting (sponsored)**
A protocol (Solana Foundation, Uniswap Labs, etc.) pays for ongoing data-driven reports on their ecosystem. Recurring B2B revenue with no subscriber dependency. Maps naturally to DeFi data coverage. Underexploited by competition.

**Stream 4 — Index / benchmark licensing**
Rules-based index constructed from the canonical data, licensed to financial product issuers for settlement benchmarks. Deferred to v2 — requires methodology documentation and legal structure, but the data infrastructure is already the hard part.

**Stream 5 — Embedded analytics (white-label)**
Signal engine or data feeds embedded in a fund or exchange's own interface. Sticky, large contracts, longer sales cycles.

---

## CONTENT ORIGINALITY

The differentiator is not qualitative research dressed up with charts. It is: **systematic, backtested, quantitative signals with documented methodology and PIT-correct historical data.** This is what Glassnode, Kaiko, and Messari do not produce. EDSx's deterministic scoring with full audit trail, combined with calibrated ML probabilities, is a genuine differentiation in a market full of opinion-based research.

---

## COVERAGE FRAMING

Coverage is expressed as domain breadth, not ticker count. The product is "derivatives + flows + DeFi + macro intelligence across the instruments those domains cover" — not "BTC, ETH, SOL signals." The instrument universe emerges from data completeness thresholds in Phase 1. Coinalyze alone covers 121 instruments on derivatives. Coverage is substantially broader than 3 assets from day one.

---

## MVP DEFINITION

**MVP = one signal report, delivered on schedule, to one paying customer, that they trust enough to act on.**

No billing infrastructure. No self-serve onboarding. No dashboard. The signal, on time, defensibly produced, to a small number of institutional early-access customers paying real money.

---

## DECISIONS LOCKED

| Decision | Outcome |
|---|---|
| Primary revenue | Intelligence-as-a-Service |
| Revenue architecture | Multi-stream from day one |
| Product surface | Layer 5 (signals), Layer 3 as B2B secondary |
| Content originality | Quantitative, systematic, auditable — not qualitative |
| Asset coverage | Domain-driven, not ticker-driven |
| MVP | Signal product, institutional early access, manual invoicing |
| Not in v1 | Dashboard UI, billing infrastructure, content products |
| Index licensing | v2 — deferred. Trigger: methodology documented + ToS audited. |
