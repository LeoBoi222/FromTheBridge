-- =============================================================================
-- Phase 0 Corrective Migration — PostgreSQL (Layer 7)
-- FromTheBridge Architecture
-- Authority: FromTheBridge_design_v4.0.md (SSOT)
-- =============================================================================
-- Target: empire_postgres (port 5433), database: crypto_structured
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"
-- =============================================================================
-- PURPOSE: Rebuild metric_catalog, source_catalog, metric_lineage, instruments,
--          collection_events, and instrument_metric_coverage to match v4.0 DDL.
--          Add solo-ops event_calendar extensions. Seed 74 metrics, 10 sources,
--          4 instruments. Add calendar_writer and risk_writer roles.
-- =============================================================================
-- DESTRUCTIVE: Drops and recreates 6 tables. Safe because Phase 0 has no
--              production data — only seed rows from 0001_catalog_schema.sql.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. DROP dependent tables (reverse dependency order)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS forge.instrument_metric_coverage CASCADE;
DROP TABLE IF EXISTS forge.collection_events CASCADE;
DROP TABLE IF EXISTS forge.metric_lineage CASCADE;
DROP TABLE IF EXISTS forge.instruments CASCADE;
DROP TABLE IF EXISTS forge.metric_catalog CASCADE;
DROP TABLE IF EXISTS forge.source_catalog CASCADE;

-- ---------------------------------------------------------------------------
-- 2. ALTER event_calendar — solo-ops extensions
-- ---------------------------------------------------------------------------
-- Add 5 new columns for solo operator operations
ALTER TABLE forge.event_calendar
    ADD COLUMN IF NOT EXISTS system_id      TEXT NOT NULL DEFAULT 'ftb',
    ADD COLUMN IF NOT EXISTS severity       TEXT CHECK (severity IN ('info','yellow','red')) DEFAULT 'info',
    ADD COLUMN IF NOT EXISTS metadata       JSONB,
    ADD COLUMN IF NOT EXISTS expires_at     DATE,
    ADD COLUMN IF NOT EXISTS recurring_rule TEXT;

-- Expand event_type CHECK from 7 to 14 values
ALTER TABLE forge.event_calendar DROP CONSTRAINT IF EXISTS event_calendar_event_type_check;
ALTER TABLE forge.event_calendar ADD CONSTRAINT event_calendar_event_type_check
    CHECK (event_type IN (
        'fomc', 'cpi_release', 'nfp_release', 'gdp_release',
        'futures_expiry', 'options_expiry', 'token_unlock',
        'maintenance', 'hardware', 'milestone', 'recurring',
        'procurement', 'upgrade', 'tos_audit'
    ));

-- ---------------------------------------------------------------------------
-- 3. CREATE source_catalog (v4.0 DDL)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.source_catalog (
    source_id               TEXT        PRIMARY KEY,
    display_name            TEXT        NOT NULL,
    tier                    INTEGER     NOT NULL,
    tos_risk                TEXT        NOT NULL DEFAULT 'unaudited',
    commercial_use          BOOLEAN,
    redistribution_status   TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (redistribution_status IN ('allowed', 'pending', 'blocked')),
    propagate_restriction   BOOLEAN     NOT NULL DEFAULT true,
    redistribution_notes    TEXT,
    redistribution_audited_at TIMESTAMPTZ,
    attribution_required    BOOLEAN     NOT NULL DEFAULT true,
    cost_tier               TEXT        NOT NULL DEFAULT 'free',
    reliability_slo         NUMERIC(4,3),
    is_active               BOOLEAN     NOT NULL DEFAULT true,
    metadata                JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT sources_tos_risk_valid
        CHECK (tos_risk IN ('none','low','unaudited','restricted','prohibited')),
    CONSTRAINT sources_cost_tier_valid
        CHECK (cost_tier IN ('free','freemium','paid','enterprise'))
);

CREATE INDEX idx_source_catalog_tier ON forge.source_catalog (tier);
CREATE INDEX idx_source_catalog_redistribution ON forge.source_catalog (redistribution_status);

-- ---------------------------------------------------------------------------
-- 4. CREATE metric_catalog (v4.0 DDL)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.metric_catalog (
    metric_id           TEXT            PRIMARY KEY,
    domain              TEXT            NOT NULL,
    subdomain           TEXT,
    description         TEXT            NOT NULL,
    unit                TEXT            NOT NULL,
    value_type          TEXT            NOT NULL DEFAULT 'numeric',
    granularity         TEXT            NOT NULL,
    cadence             INTERVAL        NOT NULL,
    staleness_threshold INTERVAL        NOT NULL,
    expected_range_low  DOUBLE PRECISION,
    expected_range_high DOUBLE PRECISION,
    is_nullable         BOOLEAN         NOT NULL DEFAULT false,
    methodology         TEXT,
    computation         TEXT,
    sources             TEXT[]          NOT NULL DEFAULT '{}',
    signal_pillar       TEXT,
    status              TEXT            NOT NULL DEFAULT 'active',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,
    backfill_depth_days INTEGER,              -- Phase 1: target historical depth per metric

    CONSTRAINT metrics_domain_valid
        CHECK (domain IN ('derivatives','spot','flows','defi','macro','etf',
                          'stablecoin','chain','valuation','price','metadata')),
    CONSTRAINT metrics_value_type_valid
        CHECK (value_type IN ('numeric','categorical','boolean')),
    CONSTRAINT metrics_granularity_valid
        CHECK (granularity IN ('per_instrument','per_protocol','per_product',
                               'market_level')),
    CONSTRAINT metrics_status_valid
        CHECK (status IN ('active','deprecated','planned')),
    CONSTRAINT metrics_signal_pillar_valid
        CHECK (signal_pillar IS NULL OR signal_pillar IN (
            'trend_structure','liquidity_flow','valuation',
            'structural_risk','tactical_macro'))
);

CREATE INDEX idx_metric_catalog_domain ON forge.metric_catalog (domain);
CREATE INDEX idx_metric_catalog_status ON forge.metric_catalog (status);
CREATE INDEX idx_metric_catalog_signal_pillar ON forge.metric_catalog (signal_pillar)
    WHERE signal_pillar IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. CREATE metric_lineage (v4.0 DDL — many-to-many)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.metric_lineage (
    metric_id           TEXT        NOT NULL REFERENCES forge.metric_catalog (metric_id),
    source_id           TEXT        NOT NULL REFERENCES forge.source_catalog (source_id),
    compute_agent       TEXT        NOT NULL,
    compute_version     TEXT,
    input_metrics       TEXT[],
    formula_ref         TEXT,
    is_primary          BOOLEAN     NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,

    PRIMARY KEY (metric_id, source_id)
);

CREATE INDEX idx_metric_lineage_source ON forge.metric_lineage (source_id);
CREATE INDEX idx_metric_lineage_compute_agent ON forge.metric_lineage (compute_agent);

-- ---------------------------------------------------------------------------
-- 6. CREATE instruments (v4.0 DDL)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.instruments (
    instrument_id       TEXT            PRIMARY KEY,
    asset_class         TEXT            NOT NULL,
    name                TEXT            NOT NULL,
    is_active           BOOLEAN         NOT NULL DEFAULT true,
    collection_tier     TEXT            NOT NULL DEFAULT 'collection',
    base_currency       TEXT,
    quote_currency      TEXT            DEFAULT 'USD',
    metadata            JSONB           NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,

    CONSTRAINT instruments_asset_class_valid
        CHECK (asset_class IN ('crypto','equity','commodity','forex','index','etf',
                               'defi_protocol')),
    CONSTRAINT instruments_collection_tier_valid
        CHECK (collection_tier IN ('collection','scoring','signal_eligible','system'))
);

CREATE INDEX idx_instruments_collection_tier ON forge.instruments (collection_tier);
CREATE INDEX idx_instruments_asset_class ON forge.instruments (asset_class);

-- ---------------------------------------------------------------------------
-- 7. CREATE collection_events (v4.0 DDL)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.collection_events (
    event_id                BIGSERIAL   PRIMARY KEY,
    source_id               TEXT        NOT NULL REFERENCES forge.source_catalog (source_id),
    metric_id               TEXT        REFERENCES forge.metric_catalog (metric_id),
    instrument_id           TEXT        REFERENCES forge.instruments (instrument_id),
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ,
    status                  TEXT        NOT NULL,
    observations_written    INTEGER,
    observations_rejected   INTEGER,
    metrics_covered         TEXT[],
    instruments_covered     TEXT[],
    error_detail            TEXT,
    metadata                JSONB       NOT NULL DEFAULT '{}',

    CONSTRAINT event_status_valid
        CHECK (status IN ('running','completed','failed','partial',
                          'promotion_candidate','demotion'))
);

CREATE INDEX idx_collection_events_source ON forge.collection_events (source_id);
CREATE INDEX idx_collection_events_status ON forge.collection_events (status);
CREATE INDEX idx_collection_events_started ON forge.collection_events (started_at);

-- ---------------------------------------------------------------------------
-- 8. CREATE instrument_metric_coverage (v4.0 DDL)
-- ---------------------------------------------------------------------------
CREATE TABLE forge.instrument_metric_coverage (
    instrument_id       TEXT            NOT NULL REFERENCES forge.instruments (instrument_id),
    metric_id           TEXT            NOT NULL REFERENCES forge.metric_catalog (metric_id),
    source_id           TEXT            NOT NULL REFERENCES forge.source_catalog (source_id),
    first_observation   TIMESTAMPTZ,
    latest_observation  TIMESTAMPTZ,
    expected_cadence    INTERVAL,
    completeness_30d    NUMERIC(5,4),
    is_active           BOOLEAN         NOT NULL DEFAULT true,
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, metric_id, source_id)
);

-- ---------------------------------------------------------------------------
-- 9. INSERT source_catalog seeds (10 rows)
-- ---------------------------------------------------------------------------
INSERT INTO forge.source_catalog (
    source_id, display_name, tier, tos_risk, commercial_use,
    redistribution_status, propagate_restriction, redistribution_notes,
    attribution_required, cost_tier, is_active
) VALUES
    ('bgeometrics',   'BGeometrics',             2, 'unaudited', NULL,  'pending', true, 'MVRV/SOPR/NUPL/Puell Multiple',                                   true, 'free',     true),
    ('binance_blc01', 'Binance (BLC-01)',        2, 'unaudited', NULL,  'pending', true, 'Tick liquidations, WebSocket, ~65-72k events/day',                 true, 'free',     true),
    ('coinalyze',     'Coinalyze',               1, 'unaudited', NULL,  'pending', true, '121 perp instruments',                                             true, 'free',     true),
    ('coinmetrics',   'CoinMetrics',             2, 'restricted', NULL, 'blocked', true, 'On-chain transfer volume via GitHub CSVs — redistribution blocked', true, 'free',     true),
    ('coinpaprika',   'CoinPaprika',             1, 'unaudited', true,  'allowed', true, 'Market cap, sector, category metadata',                            true, 'free',     true),
    ('defillama',     'DeFiLlama',               1, 'unaudited', true,  'allowed', true, 'Keyless, free, excellent coverage',                                true, 'free',     true),
    ('etherscan',     'Etherscan V2 / Explorer', 2, 'unaudited', NULL,  'pending', true, 'ETH + Arbitrum: contract/token API + exchange flow wallet tracking', true, 'freemium', true),
    ('fred',          'FRED (Federal Reserve)',   1, 'none',      true,  'allowed', true, 'Public domain',                                                    false,'free',     true),
    ('sosovalue',     'SoSoValue',               1, 'restricted', false,'blocked', true, 'ETF flows — internal only, non-commercial ToS',                    true, 'free',     true),
    ('tiingo',        'Tiingo',                  1, 'unaudited', NULL,  'allowed', true, 'OHLCV, paid commercial tier',                                      true, 'paid',     true);

-- ---------------------------------------------------------------------------
-- 10. INSERT metric_catalog seeds (74 rows, 14 explicit columns, 7 take defaults)
-- ---------------------------------------------------------------------------
-- Mapping rules applied:
--   metric_id: 13 renames (flows.stablecoin→stablecoin, flows.etf→etf, meta→metadata)
--   domain: derived from metric_id prefix
--   description: from old display_name
--   unit: from old unit (NULL→'text' for metadata)
--   value_type: 'numeric' except metadata→'categorical'
--   granularity: instrument_scoped true→'per_instrument', false→'market_level',
--                DeFi protocol→'per_protocol'
--   cadence: hours→interval conversion
--   staleness_threshold: 2×cadence for ≤24h, cadence+24h for >24h, metadata='7 days'
--   sources: old source_id wrapped in array
--   status: 'active' except defi.bridge.volume_usd→'planned'
--   computation: non-NULL for derived metrics
-- ---------------------------------------------------------------------------

-- §5.1 Derivatives Domain (10 metrics) — source: coinalyze + planned
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('derivatives.perpetual.funding_rate',              'derivatives', 'perpetual', 'Perpetual Funding Rate',              'rate',      'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.perpetual.open_interest_usd',         'derivatives', 'perpetual', 'Perpetual Open Interest (USD)',        'usd',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.perpetual.open_interest_change_usd',  'derivatives', 'perpetual', 'Perpetual OI Change (USD, derived)',   'usd',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, 'open_interest_usd[t] - open_interest_usd[t-1]', '{coinalyze}', 'active'),
    ('derivatives.perpetual.liquidations_long_usd',     'derivatives', 'perpetual', 'Perpetual Liquidations Long (USD)',    'usd',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.perpetual.liquidations_short_usd',    'derivatives', 'perpetual', 'Perpetual Liquidations Short (USD)',   'usd',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.perpetual.perp_basis',                'derivatives', 'perpetual', 'Perpetual Basis (derived)',            'pct',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, '(perp_price - spot_price) / spot_price', '{}', 'active'),
    ('derivatives.perpetual.long_short_ratio',          'derivatives', 'perpetual', 'Long/Short Ratio',                    'ratio',     'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.perpetual.cumulative_volume_delta',   'derivatives', 'perpetual', 'Cumulative Volume Delta',             'usd',       'numeric', 'per_instrument', '8 hours',  '16 hours', false, NULL, '{coinalyze}', 'active'),
    ('derivatives.futures.expiry_proximity_days',       'derivatives', 'futures',   'Futures Expiry Proximity (days)',      'days',      'numeric', 'per_instrument', '1 day',    '2 days',   true,  NULL, '{}',          'active'),
    ('derivatives.options.delta_skew_25',              'derivatives', 'options',   '25-Delta Skew',                       'pct',       'numeric', 'per_instrument', '8 hours',  '16 hours', true,  NULL, '{}',          'planned');

-- §5.2 Exchange Flow Domain (8 metrics) — source: etherscan
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('flows.exchange.inflow_usd',        'flows', 'exchange', 'Exchange Inflow (USD)',                'usd',   'numeric', 'per_instrument', '8 hours', '16 hours', false, NULL, '{etherscan}', 'active'),
    ('flows.exchange.outflow_usd',       'flows', 'exchange', 'Exchange Outflow (USD)',               'usd',   'numeric', 'per_instrument', '8 hours', '16 hours', false, NULL, '{etherscan}', 'active'),
    ('flows.exchange.net_flow_usd',      'flows', 'exchange', 'Exchange Net Flow (USD, derived)',      'usd',   'numeric', 'per_instrument', '8 hours', '16 hours', false, 'inflow_usd - outflow_usd', '{etherscan}', 'active'),
    ('flows.exchange.reserve_proxy_usd', 'flows', 'exchange', 'Exchange Reserve Proxy (USD)',         'usd',   'numeric', 'per_instrument', '8 hours', '16 hours', false, NULL, '{etherscan}', 'active'),
    ('flows.whale.transaction_count',    'flows', 'whale',    'Whale Transaction Count',              'count', 'numeric', 'per_instrument', '8 hours', '16 hours', false, NULL, '{etherscan}', 'active'),
    ('flows.whale.net_direction',        'flows', 'whale',    'Whale Net Direction (derived)',         'ratio', 'numeric', 'per_instrument', '8 hours', '16 hours', false, '(whale_inflow - whale_outflow) / whale_total', '{etherscan}', 'active'),
    ('flows.exchange.spot_volume_usd',   'flows', 'exchange', 'Exchange Spot Volume (USD)',           'usd',   'numeric', 'per_instrument', '1 day',   '2 days',   false, NULL, '{}',          'active'),
    ('flows.exchange.btc_net_flow',      'flows', 'exchange', 'BTC Exchange Net Flow',                'usd',   'numeric', 'market_level',   '1 day',   '2 days',   false, NULL, '{}',          'active');

-- §5.3 ETF Flow Domain (2 metrics, per_instrument) — source: sosovalue
-- RENAMED: flows.etf.* → etf.flows.*, consolidated from 5 per-asset to 2 generic
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('etf.flows.net_flow_usd',            'etf', 'flows', 'ETF Net Flow (USD)',                          'usd', 'numeric', 'per_instrument', '1 day', '2 days', false, NULL, '{sosovalue}', 'active'),
    ('etf.flows.cumulative_flow_usd',     'etf', 'flows', 'ETF Cumulative Flow (USD, derived)',           'usd', 'numeric', 'per_instrument', '1 day', '2 days', false, 'SUM(net_flow_usd) OVER (PARTITION BY instrument_id ORDER BY observed_at)', '{sosovalue}', 'active');

-- §5.4 Stablecoin Domain (5 metrics) — source: defillama
-- RENAMED: flows.stablecoin.* → stablecoin.*
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('stablecoin.supply.per_asset_usd',    'stablecoin', 'supply', 'Stablecoin Per-Asset Supply (USD)',   'usd',   'numeric', 'per_instrument', '12 hours', '1 day',    false, NULL, '{defillama}', 'active'),
    ('stablecoin.supply.total_usd',        'stablecoin', 'supply', 'Total Stablecoin Supply (USD)',        'usd',   'numeric', 'market_level',   '12 hours', '1 day',    false, 'SUM(per_asset_usd)', '{defillama}', 'active'),
    ('stablecoin.peg.price_usd',           'stablecoin', 'peg',    'Stablecoin Peg Price (USD)',          'usd',   'numeric', 'per_instrument', '12 hours', '1 day',    false, NULL, '{defillama}', 'active'),
    ('stablecoin.peg.deviation',           'stablecoin', 'peg',    'Stablecoin Peg Deviation (derived)',  'pct',   'numeric', 'per_instrument', '12 hours', '1 day',    false, 'abs(peg_price_usd - 1.0)', '{defillama}', 'active'),
    ('stablecoin.supply.mint_burn_events', 'stablecoin', 'supply', 'Stablecoin Mint/Burn Events',         'count', 'numeric', 'per_instrument', '1 day',    '2 days',   true,  NULL, '{defillama}', 'active');

-- §5.5 DeFi Domain (12 metrics) — source: defillama
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('defi.protocol.tvl_usd',              'defi', 'protocol', 'Protocol TVL (USD)',                       'usd',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, NULL, '{defillama}', 'active'),
    ('defi.protocol.revenue_usd',          'defi', 'protocol', 'Protocol Revenue (USD)',                   'usd',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, NULL, '{defillama}', 'active'),
    ('defi.protocol.revenue_to_tvl_ratio', 'defi', 'protocol', 'Protocol Revenue/TVL Ratio (derived)',     'ratio', 'numeric', 'per_protocol',   '12 hours', '1 day',  false, 'revenue_usd / tvl_usd', '{defillama}', 'active'),
    ('defi.lending.borrow_apy',            'defi', 'lending',  'Lending Borrow APY',                       'pct',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, NULL, '{defillama}', 'active'),
    ('defi.lending.supply_apy',            'defi', 'lending',  'Lending Supply APY',                       'pct',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, NULL, '{defillama}', 'active'),
    ('defi.lending.borrow_supply_spread',  'defi', 'lending',  'Lending Borrow-Supply Spread (derived)',   'pct',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, 'borrow_apy - supply_apy', '{defillama}', 'active'),
    ('defi.lending.utilization_rate',      'defi', 'lending',  'Lending Utilization Rate (proxy)',          'pct',   'numeric', 'per_protocol',   '12 hours', '1 day',  false, NULL, '{defillama}', 'active'),
    ('defi.dex.volume_usd_24h',           'defi', 'dex',      'DEX Volume 24h (USD)',                      'usd',   'numeric', 'market_level',   '1 day',   '2 days', false, NULL, '{defillama}', 'active'),
    ('defi.dex.volume_by_chain_usd',      'defi', 'dex',      'DEX Volume by Chain (USD)',                 'usd',   'numeric', 'market_level',   '1 day',   '2 days', false, NULL, '{defillama}', 'active'),
    ('defi.dex.volume_to_tvl_ratio',      'defi', 'dex',      'DEX Volume/TVL Ratio (derived)',            'ratio', 'numeric', 'market_level',   '1 day',   '2 days', false, 'dex_volume_usd_24h / aggregate_tvl_usd', '{defillama}', 'active'),
    ('defi.aggregate.tvl_usd',            'defi', 'aggregate', 'Aggregate DeFi TVL (USD)',                  'usd',   'numeric', 'market_level',   '12 hours', '1 day',  false, 'SUM(protocol.tvl_usd)', '{defillama}', 'active'),
    ('defi.bridge.volume_usd',            'defi', 'bridge',   'Bridge Volume (USD, deferred)',              'usd',   'numeric', 'market_level',   '1 day',   '2 days', true,  NULL, '{defillama}', 'planned');

-- §5.6 On-Chain Domain (6 metrics) — mixed sources
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('chain.valuation.mvrv_ratio',          'chain', 'valuation', 'MVRV Ratio',                       'ratio', 'numeric', 'market_level', '1 day', '2 days', false, NULL, '{bgeometrics}', 'active'),
    ('chain.valuation.sopr',                'chain', 'valuation', 'SOPR',                             'ratio', 'numeric', 'market_level', '1 day', '2 days', false, NULL, '{bgeometrics}', 'active'),
    ('chain.valuation.nupl',                'chain', 'valuation', 'NUPL',                             'ratio', 'numeric', 'market_level', '1 day', '2 days', false, NULL, '{bgeometrics}', 'active'),
    ('chain.valuation.puell_multiple',      'chain', 'valuation', 'Puell Multiple (BTC only)',        'ratio', 'numeric', 'market_level', '1 day', '2 days', false, NULL, '{bgeometrics}', 'active'),
    ('chain.activity.transfer_volume_usd',  'chain', 'activity',  'On-Chain Transfer Volume (USD)',   'usd',   'numeric', 'market_level', '1 day', '2 days', false, NULL, '{coinmetrics}', 'active'),
    ('chain.activity.nvt_proxy',            'chain', 'activity',  'NVT Proxy (derived)',              'ratio', 'numeric', 'market_level', '1 day', '2 days', false, 'market_cap / transfer_volume', '{}', 'active');

-- §5.7 Macro Domain (23 metrics) — source: fred
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('macro.rates.fed_funds_effective',    'macro', 'rates',      'Fed Funds Effective Rate',            'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.yield_10y',              'macro', 'rates',      '10-Year Treasury Yield',             'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.yield_2y',               'macro', 'rates',      '2-Year Treasury Yield',              'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.yield_30y',              'macro', 'rates',      '30-Year Treasury Yield',             'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.yield_spread_10y2y',     'macro', 'rates',      '10Y-2Y Yield Spread',                'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.yield_spread_10y3m',     'macro', 'rates',      '10Y-3M Yield Spread',                'pct',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.fx.dxy',                       'macro', 'fx',         'US Dollar Index (DXY)',              'index',        'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.inflation.cpi',                'macro', 'inflation',  'CPI (All Urban Consumers)',          'index',        'numeric', 'market_level', '30 days', '54 days',  false, NULL, '{fred}', 'active'),
    ('macro.inflation.core_pce',           'macro', 'inflation',  'Core PCE Price Index',               'index',        'numeric', 'market_level', '30 days', '54 days',  false, NULL, '{fred}', 'active'),
    ('macro.labor.nonfarm_payrolls',       'macro', 'labor',      'Nonfarm Payrolls',                   'thousands',    'numeric', 'market_level', '30 days', '54 days',  false, NULL, '{fred}', 'active'),
    ('macro.labor.jobless_claims',         'macro', 'labor',      'Initial Jobless Claims',             'count',        'numeric', 'market_level', '7 days',  '14 days',  false, NULL, '{fred}', 'active'),
    ('macro.liquidity.m2',                 'macro', 'liquidity',  'M2 Money Supply',                    'usd_billions', 'numeric', 'market_level', '30 days', '54 days',  false, NULL, '{fred}', 'active'),
    ('macro.liquidity.monetary_base',      'macro', 'liquidity',  'Monetary Base',                      'usd_billions', 'numeric', 'market_level', '14 days', '15 days',  false, NULL, '{fred}', 'active'),
    ('macro.liquidity.fed_balance_sheet',  'macro', 'liquidity',  'Fed Balance Sheet',                  'usd_millions', 'numeric', 'market_level', '7 days',  '14 days',  false, NULL, '{fred}', 'active'),
    ('macro.liquidity.ecb_balance_sheet',  'macro', 'liquidity',  'ECB Balance Sheet',                  'eur_millions', 'numeric', 'market_level', '7 days',  '14 days',  false, NULL, '{fred}', 'active'),
    ('macro.liquidity.boj_balance_sheet',  'macro', 'liquidity',  'BOJ Balance Sheet',                  'jpy_billions', 'numeric', 'market_level', '30 days', '54 days',  false, NULL, '{fred}', 'active'),
    ('macro.volatility.vix',               'macro', 'volatility', 'VIX Volatility Index',               'index',        'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.equity.sp500',                 'macro', 'equity',     'S&P 500 Index',                      'index',        'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.commodity.gold',               'macro', 'commodity',  'Gold Price (USD/oz)',                 'usd',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.commodity.wti_crude',          'macro', 'commodity',  'WTI Crude Oil Price',                'usd',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.growth.real_gdp',              'macro', 'growth',     'Real GDP Growth',                    'pct',          'numeric', 'market_level', '90 days', '114 days', false, NULL, '{fred}', 'active'),
    ('macro.credit.hy_oas',               'macro', 'credit',     'High Yield OAS',                      'bps',          'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active'),
    ('macro.rates.move_index',             'macro', 'rates',      'MOVE Index (Bond Volatility)',       'index',        'numeric', 'market_level', '1 day',   '2 days',   false, NULL, '{fred}', 'active');

-- §5.8 Price / Volume Domain (4 metrics) — mixed sources
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('price.spot.close_usd',        'price', 'spot',   'Spot Close Price (USD)',       'usd',       'numeric', 'per_instrument', '6 hours',  '12 hours', false, NULL, '{tiingo}',      'active'),
    ('price.spot.volume_usd_24h',   'price', 'spot',   'Spot Volume 24h (USD)',        'usd',       'numeric', 'per_instrument', '6 hours',  '12 hours', false, NULL, '{tiingo}',      'active'),
    ('price.spot.ohlcv',            'price', 'spot',   'Spot OHLCV',                   'composite', 'numeric', 'per_instrument', '6 hours',  '12 hours', false, NULL, '{tiingo}',      'active'),
    ('price.market.total_cap_usd',  'price', 'market', 'Total Market Cap (USD)',       'usd',       'numeric', 'market_level',   '1 day',    '2 days',   false, NULL, '{coinpaprika}', 'active');

-- §5.9 Metadata Domain (4 metrics) — mixed sources
-- RENAMED: meta.* → metadata.*
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type, granularity,
    cadence, staleness_threshold, is_nullable, computation, sources, status
) VALUES
    ('metadata.instrument.sector',       'metadata', 'instrument', 'Instrument Sector',          'text', 'categorical', 'per_instrument', '1 day', '7 days', true, NULL, '{coinpaprika}', 'active'),
    ('metadata.instrument.category',     'metadata', 'instrument', 'Instrument Category',        'text', 'categorical', 'per_instrument', '1 day', '7 days', true, NULL, '{coinpaprika}', 'active'),
    ('metadata.instrument.listing_date', 'metadata', 'instrument', 'Instrument Listing Date',    'text', 'categorical', 'per_instrument', '1 day', '7 days', true, NULL, '{coinpaprika}', 'active'),
    ('metadata.futures.expiry_schedule', 'metadata', 'futures',    'Futures Expiry Schedule',     'text', 'categorical', 'per_instrument', '1 day', '7 days', true, NULL, '{}',            'active');

-- ---------------------------------------------------------------------------
-- 11. INSERT instruments seeds (4 rows)
-- ---------------------------------------------------------------------------
INSERT INTO forge.instruments (
    instrument_id, asset_class, name, is_active, collection_tier, base_currency, quote_currency
) VALUES
    ('BTC-USD',     'crypto', 'Bitcoin / USD',  true, 'collection', 'BTC', 'USD'),
    ('ETH-USD',     'crypto', 'Ethereum / USD', true, 'collection', 'ETH', 'USD'),
    ('SOL-USD',     'crypto', 'Solana / USD',   true, 'collection', 'SOL', 'USD'),
    ('__market__',  'index',  'Market Level',   true, 'system',     NULL,  NULL);

-- ---------------------------------------------------------------------------
-- 12. Roles and privileges
-- ---------------------------------------------------------------------------

-- Ensure core roles exist
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_writer') THEN
        CREATE ROLE forge_writer;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_reader') THEN
        CREATE ROLE forge_reader;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'calendar_writer') THEN
        CREATE ROLE calendar_writer;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'risk_writer') THEN
        CREATE ROLE risk_writer;
    END IF;
END $$;

-- Schema-level usage
GRANT USAGE ON SCHEMA forge TO forge_writer, forge_reader, calendar_writer;

-- forge_writer: INSERT + SELECT on all forge tables
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA forge TO forge_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA forge TO forge_writer;

-- forge_reader: SELECT only on all forge tables
GRANT SELECT ON ALL TABLES IN SCHEMA forge TO forge_reader;

-- calendar_writer: INSERT + SELECT on event_calendar only
GRANT SELECT, INSERT ON forge.event_calendar TO calendar_writer;
GRANT USAGE ON SEQUENCE forge.event_calendar_event_id_seq TO calendar_writer;

-- risk_writer: INSERT + SELECT on empire.risk_assessment only (if exists)
-- Note: empire.risk_assessment is in the empire schema, created by EDS.
-- These grants are no-ops if the schema/table doesn't exist yet.
DO $$ BEGIN
    IF EXISTS (SELECT FROM information_schema.schemata WHERE schema_name = 'empire') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA empire TO risk_writer';
    END IF;
    IF EXISTS (SELECT FROM information_schema.tables
               WHERE table_schema = 'empire' AND table_name = 'risk_assessment') THEN
        EXECUTE 'GRANT SELECT, INSERT ON empire.risk_assessment TO risk_writer';
    END IF;
END $$;

-- Ensure future tables inherit these grants
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT SELECT, INSERT ON TABLES TO forge_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT SELECT ON TABLES TO forge_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT USAGE ON SEQUENCES TO forge_writer;

-- ---------------------------------------------------------------------------
-- 13. Verification queries (run after migration)
-- ---------------------------------------------------------------------------
-- SELECT COUNT(*) FROM forge.metric_catalog;          -- expect 74
-- SELECT COUNT(*) FROM forge.source_catalog;          -- expect 10
-- SELECT COUNT(*) FROM forge.instruments;             -- expect 4
-- SELECT DISTINCT domain FROM forge.metric_catalog ORDER BY domain;
--   -- expect: chain, defi, derivatives, etf, flows, macro, metadata, price, stablecoin
-- SELECT domain, COUNT(*) FROM forge.metric_catalog GROUP BY domain ORDER BY domain;
--   -- expect: chain 6, defi 12, derivatives 10, etf 2, flows 8, macro 23, metadata 4, price 4, stablecoin 5

COMMIT;
