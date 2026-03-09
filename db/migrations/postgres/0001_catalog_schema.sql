-- =============================================================================
-- Phase 0: Catalog Schema — PostgreSQL (Layer 7)
-- FromTheBridge / Empire Architecture
-- Authority: thread_4_data_universe.md v2.0, thread_6_build_plan.md v2.0 §Phase 0
-- =============================================================================
-- Target: empire_postgres (port 5433), database: crypto_structured
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"
-- =============================================================================
-- Idempotent: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING guards.
-- =============================================================================
-- NOTE: This migration deploys ONLY the 12 relational catalog tables and seed
-- data. Time-series objects (observations, dead_letter, current_values) live in
-- ClickHouse Silver — see db/migrations/clickhouse/0001_silver_schema.sql.
-- Rule 3: No time series data in PostgreSQL — ever.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Schema
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS forge;

-- ---------------------------------------------------------------------------
-- 2. Core identifier tables (thread_4 §2)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forge.assets (
    asset_id             TEXT PRIMARY KEY,
    canonical_name       TEXT NOT NULL,
    asset_type           TEXT NOT NULL CHECK (asset_type IN (
                             'l1','l2','token','stablecoin','wrapped','lsd','index')),
    chain                TEXT,
    contract_address     TEXT,
    decimals             INT,
    launch_date          DATE,
    coingecko_id         TEXT,
    coinpaprika_id       TEXT,
    sector               TEXT,
    category             TEXT,
    universe_rank        INT,
    is_active            BOOLEAN DEFAULT true,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.asset_aliases (
    alias_id             TEXT NOT NULL,
    asset_id             TEXT REFERENCES forge.assets(asset_id),
    effective_date       DATE NOT NULL,
    reason               TEXT,
    PRIMARY KEY (alias_id, effective_date)
);

CREATE TABLE IF NOT EXISTS forge.venues (
    venue_id             TEXT PRIMARY KEY,
    venue_type           TEXT NOT NULL CHECK (venue_type IN (
                             'cex','dex','lending','bridge','staking')),
    chain                TEXT,
    api_type             TEXT,
    is_active            BOOLEAN DEFAULT true,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.instruments (
    instrument_id        TEXT PRIMARY KEY,
    asset_id             TEXT REFERENCES forge.assets(asset_id),
    quote_asset          TEXT NOT NULL,
    venue_id             TEXT REFERENCES forge.venues(venue_id),
    instrument_type      TEXT NOT NULL CHECK (instrument_type IN (
                             'spot','perp','future','option')),
    settlement           TEXT CHECK (settlement IN ('linear','inverse','quanto')),
    contract_size        NUMERIC,
    tick_size            NUMERIC,
    is_active            BOOLEAN DEFAULT true,
    tier                 TEXT CHECK (tier IN ('collection','scoring','signal_eligible'))
);

-- ---------------------------------------------------------------------------
-- 3. Source catalog (thread_4 §7)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forge.source_catalog (
    source_id            TEXT PRIMARY KEY,
    display_name         TEXT NOT NULL,
    api_type             TEXT NOT NULL,
    tos_audited          BOOLEAN DEFAULT false,
    redistribution_allowed TEXT NOT NULL CHECK (redistribution_allowed IN (
                             'yes','no','pending_audit','internal_only')),
    cadence_hours        NUMERIC,
    rate_limit           TEXT,
    auth_required        BOOLEAN,
    notes                TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 4. Metric catalog (thread_4 §4)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forge.metric_catalog (
    metric_id            TEXT PRIMARY KEY,
    canonical_name       TEXT NOT NULL UNIQUE,
    display_name         TEXT NOT NULL,
    domain               TEXT NOT NULL CHECK (domain IN (
                             'derivatives','spot','flows','defi','macro','etf',
                             'stablecoin','chain','valuation','price','metadata')),
    subdomain            TEXT NOT NULL,
    source_id            TEXT REFERENCES forge.source_catalog(source_id),
    instrument_scoped    BOOLEAN NOT NULL,
    cadence_hours        NUMERIC,
    unit                 TEXT,
    confidence_tier      INT CHECK (confidence_tier IN (1,2,3)),
    is_active            BOOLEAN DEFAULT true,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.metric_lineage (
    metric_id            TEXT PRIMARY KEY REFERENCES forge.metric_catalog(metric_id),
    compute_agent        TEXT NOT NULL,
    compute_version      TEXT NOT NULL,
    input_sources        JSONB NOT NULL,
    formula_ref          TEXT NOT NULL,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    deprecated_at        TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 5. Supporting tables (thread_4 §8)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forge.event_calendar (
    event_id             SERIAL PRIMARY KEY,
    event_type           TEXT NOT NULL CHECK (event_type IN (
                             'fomc', 'cpi_release', 'nfp_release', 'gdp_release',
                             'futures_expiry', 'options_expiry', 'token_unlock')),
    event_date           DATE NOT NULL,
    description          TEXT,
    source               TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.supply_events (
    event_id             SERIAL PRIMARY KEY,
    asset_id             TEXT REFERENCES forge.assets(asset_id),
    event_type           TEXT NOT NULL CHECK (event_type IN (
                             'fork','migration','split','airdrop','unlock','depeg','delist')),
    event_date           TIMESTAMPTZ NOT NULL,
    details              JSONB,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.adjustment_factors (
    asset_id             TEXT REFERENCES forge.assets(asset_id),
    effective_date       DATE NOT NULL,
    factor_type          TEXT NOT NULL CHECK (factor_type IN (
                             'split','migration','redenomination')),
    adjustment_ratio     NUMERIC NOT NULL,
    notes                TEXT,
    PRIMARY KEY (asset_id, effective_date, factor_type)
);

-- ---------------------------------------------------------------------------
-- 6. Collection tracking tables (thread_4 §8)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forge.collection_events (
    event_id             BIGSERIAL PRIMARY KEY,
    source_id            TEXT NOT NULL REFERENCES forge.source_catalog(source_id),
    metric_id            TEXT REFERENCES forge.metric_catalog(metric_id),
    instrument_id        TEXT REFERENCES forge.instruments(instrument_id),
    collected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rows_written         INT,
    rows_rejected        INT,
    duration_ms          INT,
    status               TEXT NOT NULL CHECK (status IN ('success','partial','failed')),
    error_message        TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS forge.instrument_metric_coverage (
    instrument_id        TEXT REFERENCES forge.instruments(instrument_id),
    metric_id            TEXT REFERENCES forge.metric_catalog(metric_id),
    source_id            TEXT REFERENCES forge.source_catalog(source_id),
    first_observed       TIMESTAMPTZ,
    last_observed        TIMESTAMPTZ,
    observation_count    BIGINT DEFAULT 0,
    coverage_status      TEXT CHECK (coverage_status IN ('active','stale','gap','never_collected')),
    updated_at           TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (instrument_id, metric_id, source_id)
);

-- ---------------------------------------------------------------------------
-- 7. Roles and privileges
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_writer') THEN
        CREATE ROLE forge_writer;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_reader') THEN
        CREATE ROLE forge_reader;
    END IF;
END $$;

-- Schema-level usage
GRANT USAGE ON SCHEMA forge TO forge_writer, forge_reader;

-- forge_writer: INSERT + SELECT on all tables
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA forge TO forge_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA forge TO forge_writer;

-- forge_reader: SELECT only
GRANT SELECT ON ALL TABLES IN SCHEMA forge TO forge_reader;

-- Ensure future tables inherit these grants
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT SELECT, INSERT ON TABLES TO forge_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT SELECT ON TABLES TO forge_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA forge GRANT USAGE ON SEQUENCES TO forge_writer;

-- ---------------------------------------------------------------------------
-- 8. Source catalog pre-population (10 rows)
-- ---------------------------------------------------------------------------
-- 10 active v1 sources per design_index.md Sources Catalog Summary.
-- Canonical source_ids verified against thread_4 §7 and design_index.md.
-- Excluded permanently: Santiment, Glassnode, BSCScan, Solscan.
-- Excluded from catalog: CoinGecko, KuCoin (T3 fallback, not catalogued).
-- explorer removed: etherscan covers both contract/token API and exchange
--   flow wallet tracking via Etherscan V2 API (same underlying API).
-- ---------------------------------------------------------------------------

INSERT INTO forge.source_catalog (source_id, display_name, api_type, tos_audited, redistribution_allowed, cadence_hours, rate_limit, auth_required, notes)
VALUES
    ('bgeometrics',   'BGeometrics',             'rest',      false, 'pending_audit',  24,   NULL,              false, 'MVRV/SOPR/NUPL/Puell Multiple'),
    ('binance_blc01', 'Binance (BLC-01)',        'websocket', false, 'pending_audit',  NULL, NULL,              false, 'Tick liquidations, WebSocket, ~65-72k events/day'),
    ('coinalyze',     'Coinalyze',               'rest',      false, 'pending_audit',  8,    '40 calls/min',   true,  '121 perp instruments'),
    ('coinmetrics',   'CoinMetrics',             'csv',       false, 'no',             24,   NULL,              false, 'On-chain transfer volume via GitHub CSVs — redistribution blocked'),
    ('coinpaprika',   'CoinPaprika',             'rest',      false, 'yes',            24,   NULL,              false, 'Market cap, sector, category metadata'),
    ('defillama',     'DeFiLlama',               'rest',      false, 'yes',            12,   NULL,              false, 'Keyless, free, excellent coverage'),
    ('etherscan',     'Etherscan V2 / Explorer', 'rest',      false, 'pending_audit',  8,    '5 calls/sec',    true,  'ETH + Arbitrum: contract/token API + exchange flow wallet tracking'),
    ('fred',          'FRED (Federal Reserve)',   'rest',      false, 'yes',            24,   '120 req/min',    true,  'Public domain'),
    ('sosovalue',     'SoSoValue',               'rest',      false, 'no',             24,   NULL,              false, 'ETF flows — internal only, non-commercial ToS'),
    ('tiingo',        'Tiingo',                  'rest',      false, 'yes',            6,    NULL,              true,  'OHLCV, paid commercial tier')
ON CONFLICT (source_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 9. Metric catalog pre-population (thread_4 §5.1 — §5.9)
-- ---------------------------------------------------------------------------
-- 74 metrics total across 9 domains.
-- metric_id = canonical_name (hierarchical, immutable once set).
-- ---------------------------------------------------------------------------

-- §5.1 Derivatives Domain (9 metrics) — source: coinalyze
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('derivatives.perpetual.funding_rate',              'derivatives.perpetual.funding_rate',              'Perpetual Funding Rate',              'derivatives', 'perpetual', 'coinalyze', true,  8,    'rate',  1),
    ('derivatives.perpetual.open_interest_usd',         'derivatives.perpetual.open_interest_usd',         'Perpetual Open Interest (USD)',        'derivatives', 'perpetual', 'coinalyze', true,  8,    'usd',   1),
    ('derivatives.perpetual.open_interest_change_usd',  'derivatives.perpetual.open_interest_change_usd',  'Perpetual OI Change (USD, derived)',   'derivatives', 'perpetual', 'coinalyze', true,  8,    'usd',   1),
    ('derivatives.perpetual.liquidations_long_usd',     'derivatives.perpetual.liquidations_long_usd',     'Perpetual Liquidations Long (USD)',    'derivatives', 'perpetual', 'coinalyze', true,  8,    'usd',   1),
    ('derivatives.perpetual.liquidations_short_usd',    'derivatives.perpetual.liquidations_short_usd',    'Perpetual Liquidations Short (USD)',   'derivatives', 'perpetual', 'coinalyze', true,  8,    'usd',   1),
    ('derivatives.perpetual.perp_basis',                'derivatives.perpetual.perp_basis',                'Perpetual Basis',                     'derivatives', 'perpetual', 'coinalyze', true,  8,    'pct',   1),
    ('derivatives.perpetual.long_short_ratio',          'derivatives.perpetual.long_short_ratio',          'Long/Short Ratio',                    'derivatives', 'perpetual', 'coinalyze', true,  8,    'ratio', 1),
    ('derivatives.perpetual.cumulative_volume_delta',   'derivatives.perpetual.cumulative_volume_delta',   'Cumulative Volume Delta',             'derivatives', 'perpetual', 'coinalyze', true,  8,    'usd',   1),
    ('derivatives.futures.expiry_proximity_days',       'derivatives.futures.expiry_proximity_days',       'Futures Expiry Proximity (days)',      'derivatives', 'futures',   NULL,        true,  NULL, 'days',  2)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.2 Exchange Flow Domain (8 metrics) — source: etherscan
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('flows.exchange.inflow_usd',        'flows.exchange.inflow_usd',        'Exchange Inflow (USD)',                'flows', 'exchange', 'etherscan', true,  8,    'usd',   1),
    ('flows.exchange.outflow_usd',       'flows.exchange.outflow_usd',       'Exchange Outflow (USD)',               'flows', 'exchange', 'etherscan', true,  8,    'usd',   1),
    ('flows.exchange.net_position_usd',  'flows.exchange.net_position_usd',  'Exchange Net Position (USD, derived)', 'flows', 'exchange', 'etherscan', true,  8,    'usd',   1),
    ('flows.exchange.reserve_proxy_usd', 'flows.exchange.reserve_proxy_usd', 'Exchange Reserve Proxy (USD)',         'flows', 'exchange', 'etherscan', true,  8,    'usd',   2),
    ('flows.whale.transaction_count',    'flows.whale.transaction_count',    'Whale Transaction Count',              'flows', 'whale',    'etherscan', true,  8,    'count', 2),
    ('flows.whale.net_direction',        'flows.whale.net_direction',        'Whale Net Direction (derived)',         'flows', 'whale',    'etherscan', true,  8,    'ratio', 2),
    ('flows.exchange.spot_volume_usd',   'flows.exchange.spot_volume_usd',   'Exchange Spot Volume (USD)',           'flows', 'exchange', NULL,        true,  24,   'usd',   2),
    ('flows.exchange.btc_net_flow',      'flows.exchange.btc_net_flow',      'BTC Exchange Net Flow',                'flows', 'exchange', NULL,        false, 24,   'usd',   NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.3 ETF Flow Domain (5 metrics) — source: sosovalue
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('flows.etf.btc_net_flow_usd',        'flows.etf.btc_net_flow_usd',        'BTC ETF Net Flow (USD)',              'flows', 'etf', 'sosovalue', false, 24, 'usd', 1),
    ('flows.etf.eth_net_flow_usd',        'flows.etf.eth_net_flow_usd',        'ETH ETF Net Flow (USD)',              'flows', 'etf', 'sosovalue', false, 24, 'usd', 1),
    ('flows.etf.sol_net_flow_usd',        'flows.etf.sol_net_flow_usd',        'SOL ETF Net Flow (USD)',              'flows', 'etf', 'sosovalue', false, 24, 'usd', 1),
    ('flows.etf.btc_cumulative_flow_usd', 'flows.etf.btc_cumulative_flow_usd', 'BTC ETF Cumulative Flow (USD, derived)', 'flows', 'etf', 'sosovalue', false, 24, 'usd', 1),
    ('flows.etf.eth_cumulative_flow_usd', 'flows.etf.eth_cumulative_flow_usd', 'ETH ETF Cumulative Flow (USD, derived)', 'flows', 'etf', 'sosovalue', false, 24, 'usd', 1)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.4 Stablecoin Domain (4 metrics) — source: defillama
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('flows.stablecoin.circulating_supply_usd', 'flows.stablecoin.circulating_supply_usd', 'Stablecoin Circulating Supply (USD)', 'flows', 'stablecoin', 'defillama', true,  12,   'usd',   1),
    ('flows.stablecoin.peg_price_usd',          'flows.stablecoin.peg_price_usd',          'Stablecoin Peg Price (USD)',          'flows', 'stablecoin', 'defillama', true,  12,   'usd',   1),
    ('flows.stablecoin.peg_deviation',          'flows.stablecoin.peg_deviation',          'Stablecoin Peg Deviation (derived)',  'flows', 'stablecoin', 'defillama', true,  12,   'pct',   1),
    ('flows.stablecoin.mint_burn_events',       'flows.stablecoin.mint_burn_events',       'Stablecoin Mint/Burn Events',         'flows', 'stablecoin', 'defillama', true,  NULL, 'count', 2)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.5 DeFi Domain (11 metrics) — source: defillama
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('defi.protocol.tvl_usd',              'defi.protocol.tvl_usd',              'Protocol TVL (USD)',                       'defi', 'protocol', 'defillama', true,  12,   'usd',   1),
    ('defi.protocol.revenue_usd',          'defi.protocol.revenue_usd',          'Protocol Revenue (USD)',                   'defi', 'protocol', 'defillama', true,  12,   'usd',   1),
    ('defi.protocol.revenue_to_tvl_ratio', 'defi.protocol.revenue_to_tvl_ratio', 'Protocol Revenue/TVL Ratio (derived)',     'defi', 'protocol', 'defillama', true,  12,   'ratio', 1),
    ('defi.lending.borrow_apy',            'defi.lending.borrow_apy',            'Lending Borrow APY',                       'defi', 'lending',  'defillama', true,  12,   'pct',   1),
    ('defi.lending.supply_apy',            'defi.lending.supply_apy',            'Lending Supply APY',                       'defi', 'lending',  'defillama', true,  12,   'pct',   1),
    ('defi.lending.borrow_supply_spread',  'defi.lending.borrow_supply_spread',  'Lending Borrow-Supply Spread (derived)',   'defi', 'lending',  'defillama', true,  12,   'pct',   1),
    ('defi.lending.utilization_rate',      'defi.lending.utilization_rate',      'Lending Utilization Rate (proxy)',          'defi', 'lending',  'defillama', true,  12,   'pct',   2),
    ('defi.dex.volume_usd',               'defi.dex.volume_usd',               'DEX Volume (USD)',                          'defi', 'dex',      'defillama', false, 24,   'usd',   1),
    ('defi.dex.volume_by_chain_usd',      'defi.dex.volume_by_chain_usd',      'DEX Volume by Chain (USD)',                 'defi', 'dex',      'defillama', false, 24,   'usd',   1),
    ('defi.dex.volume_to_tvl_ratio',      'defi.dex.volume_to_tvl_ratio',      'DEX Volume/TVL Ratio (derived)',            'defi', 'dex',      'defillama', false, 24,   'ratio', 1),
    ('defi.bridge.volume_usd',            'defi.bridge.volume_usd',            'Bridge Volume (USD, deferred)',              'defi', 'bridge',   'defillama', false, 24,   'usd',   NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.6 On-Chain Domain (6 metrics) — mixed sources
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('chain.valuation.mvrv_ratio',          'chain.valuation.mvrv_ratio',          'MVRV Ratio',                       'chain', 'valuation', 'bgeometrics', false, 24, 'ratio', NULL),
    ('chain.valuation.sopr',                'chain.valuation.sopr',                'SOPR',                             'chain', 'valuation', 'bgeometrics', false, 24, 'ratio', NULL),
    ('chain.valuation.nupl',                'chain.valuation.nupl',                'NUPL',                             'chain', 'valuation', 'bgeometrics', false, 24, 'ratio', NULL),
    ('chain.valuation.puell_multiple',      'chain.valuation.puell_multiple',      'Puell Multiple (BTC only)',        'chain', 'valuation', 'bgeometrics', false, 24, 'ratio', NULL),
    ('chain.activity.transfer_volume_usd',  'chain.activity.transfer_volume_usd',  'On-Chain Transfer Volume (USD)',   'chain', 'activity',  'coinmetrics', false, 24, 'usd',   NULL),
    ('chain.activity.nvt_proxy',            'chain.activity.nvt_proxy',            'NVT Proxy (derived)',              'chain', 'activity',  NULL,          false, 24, 'ratio', NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.7 Macro Domain (23 metrics) — source: fred
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('macro.rates.fed_funds',              'macro.rates.fed_funds',              'Fed Funds Rate',                     'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.rates.yield_10y',              'macro.rates.yield_10y',              '10-Year Treasury Yield',             'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.rates.yield_2y',               'macro.rates.yield_2y',               '2-Year Treasury Yield',              'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.rates.yield_30y',              'macro.rates.yield_30y',              '30-Year Treasury Yield',             'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.rates.yield_spread_10y2y',     'macro.rates.yield_spread_10y2y',     '10Y-2Y Yield Spread',                'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.rates.yield_spread_10y3m',     'macro.rates.yield_spread_10y3m',     '10Y-3M Yield Spread',                'macro', 'rates',     'fred', false, 24,   'pct',          NULL),
    ('macro.fx.dxy',                       'macro.fx.dxy',                       'US Dollar Index (DXY)',              'macro', 'fx',        'fred', false, 24,   'index',        NULL),
    ('macro.inflation.cpi',                'macro.inflation.cpi',                'CPI (All Urban Consumers)',          'macro', 'inflation', 'fred', false, 720,  'index',        NULL),
    ('macro.inflation.core_pce',           'macro.inflation.core_pce',           'Core PCE Price Index',               'macro', 'inflation', 'fred', false, 720,  'index',        NULL),
    ('macro.labor.nonfarm_payrolls',       'macro.labor.nonfarm_payrolls',       'Nonfarm Payrolls',                   'macro', 'labor',     'fred', false, 720,  'thousands',    NULL),
    ('macro.labor.jobless_claims',         'macro.labor.jobless_claims',         'Initial Jobless Claims',             'macro', 'labor',     'fred', false, 168,  'count',        NULL),
    ('macro.liquidity.m2',                 'macro.liquidity.m2',                 'M2 Money Supply',                    'macro', 'liquidity', 'fred', false, 720,  'usd_billions', NULL),
    ('macro.liquidity.monetary_base',      'macro.liquidity.monetary_base',      'Monetary Base',                      'macro', 'liquidity', 'fred', false, 336,  'usd_billions', NULL),
    ('macro.liquidity.fed_balance_sheet',  'macro.liquidity.fed_balance_sheet',  'Fed Balance Sheet',                  'macro', 'liquidity', 'fred', false, 168,  'usd_millions', NULL),
    ('macro.liquidity.ecb_balance_sheet',  'macro.liquidity.ecb_balance_sheet',  'ECB Balance Sheet',                  'macro', 'liquidity', 'fred', false, 168,  'eur_millions', NULL),
    ('macro.liquidity.boj_balance_sheet',  'macro.liquidity.boj_balance_sheet',  'BOJ Balance Sheet',                  'macro', 'liquidity', 'fred', false, 720,  'jpy_billions', NULL),
    ('macro.volatility.vix',               'macro.volatility.vix',               'VIX Volatility Index',               'macro', 'volatility','fred', false, 24,   'index',        NULL),
    ('macro.equity.sp500',                 'macro.equity.sp500',                 'S&P 500 Index',                      'macro', 'equity',    'fred', false, 24,   'index',        NULL),
    ('macro.commodity.gold',               'macro.commodity.gold',               'Gold Price (USD/oz)',                 'macro', 'commodity', 'fred', false, 24,   'usd',          NULL),
    ('macro.commodity.wti_crude',          'macro.commodity.wti_crude',          'WTI Crude Oil Price',                'macro', 'commodity', 'fred', false, 24,   'usd',          NULL),
    ('macro.growth.real_gdp',              'macro.growth.real_gdp',              'Real GDP Growth',                    'macro', 'growth',    'fred', false, 2160, 'pct',          NULL),
    ('macro.credit.hy_oas',               'macro.credit.hy_oas',               'High Yield OAS',                      'macro', 'credit',    'fred', false, 24,   'bps',          NULL),
    ('macro.rates.move_index',             'macro.rates.move_index',             'MOVE Index (Bond Volatility)',       'macro', 'rates',     'fred', false, 24,   'index',        NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.8 Price / Volume Domain (4 metrics) — mixed sources
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('price.spot.close_usd',        'price.spot.close_usd',        'Spot Close Price (USD)',       'price', 'spot',   'tiingo',      true,  6,  'usd',       NULL),
    ('price.spot.volume_usd_24h',   'price.spot.volume_usd_24h',   'Spot Volume 24h (USD)',        'price', 'spot',   'tiingo',      true,  6,  'usd',       NULL),
    ('price.spot.ohlcv',            'price.spot.ohlcv',            'Spot OHLCV',                   'price', 'spot',   'tiingo',      true,  6,  'composite',  NULL),
    ('price.market.total_cap_usd',  'price.market.total_cap_usd',  'Total Market Cap (USD)',       'price', 'market', 'coinpaprika', false, 24, 'usd',       NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- §5.9 Metadata Domain (4 metrics) — mixed sources
INSERT INTO forge.metric_catalog (metric_id, canonical_name, display_name, domain, subdomain, source_id, instrument_scoped, cadence_hours, unit, confidence_tier)
VALUES
    ('meta.instrument.sector',          'meta.instrument.sector',          'Instrument Sector',              'metadata', 'instrument', 'coinpaprika', true,  NULL, NULL, NULL),
    ('meta.instrument.category',        'meta.instrument.category',        'Instrument Category',            'metadata', 'instrument', 'coinpaprika', true,  NULL, NULL, NULL),
    ('meta.instrument.listing_date',    'meta.instrument.listing_date',    'Instrument Listing Date',        'metadata', 'instrument', 'coinpaprika', true,  NULL, NULL, NULL),
    ('meta.futures.expiry_schedule',    'meta.futures.expiry_schedule',    'Futures Expiry Schedule',         'metadata', 'futures',    NULL,          true,  NULL, NULL, NULL)
ON CONFLICT (metric_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Metric catalog count verification (expected: 74)
-- Breakdown: Derivatives 9, Exchange Flow 8, ETF 5, Stablecoin 4, DeFi 11,
--            On-Chain 6, Macro 23, Price/Volume 4, Metadata 4
-- ---------------------------------------------------------------------------

COMMIT;
