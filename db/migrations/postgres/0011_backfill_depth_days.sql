-- 0011_backfill_depth_days.sql
-- Populate backfill_depth_days for all active metrics.
-- Values derived from T3 API depth audit + T3b training window analysis.
-- Target depth = source data availability aligned to ML training floor dates.
--
-- Phase 1 gate: SELECT COUNT(*) FROM forge.metric_catalog
--               WHERE status = 'active' AND backfill_depth_days IS NULL
--               must return 0.

BEGIN;

-- =============================================================================
-- MACRO (FRED) — deep history, government data
-- Training relevance: Macro Regime model floor = 2014-01 (~4450 days)
-- Most FRED series go back decades; use actual availability as target.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 4450
WHERE metric_id IN (
    'macro.rates.fed_funds_effective',       -- FRED DFF, from 1954
    'macro.rates.yield_10y',                 -- FRED DGS10
    'macro.rates.yield_2y',                  -- FRED DGS2
    'macro.rates.yield_30y',                 -- FRED DGS30
    'macro.rates.yield_spread_10y2y',        -- FRED T10Y2Y, from 1976
    'macro.rates.yield_spread_10y3m',        -- FRED T10Y3M
    'macro.inflation.cpi',                   -- FRED CPIAUCSL, from 1947
    'macro.inflation.core_pce',              -- FRED PCEPILFE
    'macro.labor.nonfarm_payrolls',          -- FRED PAYEMS
    'macro.labor.jobless_claims',            -- FRED ICSA
    'macro.growth.real_gdp',                 -- FRED GDPC1
    'macro.liquidity.m2',                    -- FRED M2SL, from 1959
    'macro.liquidity.monetary_base',         -- FRED BOGMBASE
    'macro.liquidity.fed_balance_sheet',     -- FRED WALCL
    'macro.liquidity.ecb_balance_sheet',     -- FRED ECBASSETSW
    'macro.liquidity.boj_balance_sheet',     -- FRED JPNASSETS
    'macro.volatility.vix',                  -- FRED VIXCLS, from 1990
    'macro.equity.sp500',                    -- FRED SP500
    'macro.fx.dxy',                          -- FRED DTWEXBGS, from 2006
    'macro.commodity.gold',                  -- FRED GOLDAMGBD228NLBM
    'macro.commodity.wti_crude',             -- FRED DCOILWTICO, from 1986
    'macro.credit.hy_oas',                   -- FRED BAMLH0A0HYM2, from 1996
    'macro.rates.move_index'                 -- FRED, bond vol
);

-- =============================================================================
-- PRICES (Tiingo) — BTC from 2014, ETH from 2015, altcoins vary
-- Use 4380 days (~12yr) as target; EDS adapter handles per-instrument floors.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 4380
WHERE metric_id IN (
    'price.spot.close_usd',
    'price.spot.ohlcv',
    'price.spot.volume_usd_24h',
    'price.market.total_cap_usd'
);

-- =============================================================================
-- DERIVATIVES (Coinalyze) — from 2021-03, effective training floor 2022-02
-- Use 1825 days (~5yr) as target depth.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 1825
WHERE metric_id IN (
    'derivatives.perpetual.funding_rate',
    'derivatives.perpetual.open_interest_usd',
    'derivatives.perpetual.open_interest_change_usd',
    'derivatives.perpetual.long_short_ratio',
    'derivatives.perpetual.liquidations_long_usd',
    'derivatives.perpetual.liquidations_short_usd',
    'derivatives.perpetual.cumulative_volume_delta',
    'derivatives.perpetual.perp_basis',
    'derivatives.futures.expiry_proximity_days'
);

-- =============================================================================
-- BLC-01 LIQUIDATIONS — tick data from Oct 2025, ~180 days accumulated
-- Excluded from initial ML training (becomes relevant ~Jul 2027).
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 180
WHERE metric_id IN (
    'derivatives.perpetual.liquidation_count',
    'derivatives.perpetual.liquidation_ls_ratio'
);

-- =============================================================================
-- DEFI (DeFiLlama) — TVL from 2020-05, DEX from 2016, lending from ~2021
-- Use 2190 days (~6yr) for TVL/DEX, 1825 (~5yr) for lending.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 2190
WHERE metric_id IN (
    'defi.aggregate.tvl_usd',
    'defi.dex.volume_usd_24h',
    'defi.dex.volume_by_chain_usd',
    'defi.dex.volume_to_tvl_ratio',
    'defi.protocol.tvl_usd',
    'defi.protocol.revenue_usd',
    'defi.protocol.revenue_to_tvl_ratio'
);

UPDATE forge.metric_catalog SET backfill_depth_days = 1825
WHERE metric_id IN (
    'defi.lending.supply_apy',
    'defi.lending.borrow_apy',
    'defi.lending.utilization_rate',
    'defi.lending.borrow_supply_spread'
);

-- =============================================================================
-- STABLECOINS (DeFiLlama) — USDT from 2017-11, others later
-- Use 2555 days (~7yr) as target.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 2555
WHERE metric_id IN (
    'stablecoin.supply.total_usd',
    'stablecoin.supply.per_asset_usd',
    'stablecoin.supply.mint_burn_events',
    'stablecoin.peg.price_usd',
    'stablecoin.peg.deviation'
);

-- =============================================================================
-- ON-CHAIN (BGeometrics) — BTC from ~2011, ETH from ~2015
-- Use 4380 days (~12yr BTC); per-instrument floors handled by adapter.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 4380
WHERE metric_id IN (
    'chain.valuation.mvrv_ratio',
    'chain.valuation.sopr',
    'chain.valuation.nupl',
    'chain.valuation.puell_multiple',
    'chain.activity.transfer_volume_usd',
    'chain.activity.nvt_proxy'
);

-- =============================================================================
-- EXCHANGE FLOWS (Etherscan) — practical floor ~2017-2018
-- Use 2920 days (~8yr) as target.
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 2920
WHERE metric_id IN (
    'flows.exchange.inflow_usd',
    'flows.exchange.outflow_usd',
    'flows.exchange.net_flow_usd',
    'flows.exchange.reserve_proxy_usd',
    'flows.exchange.spot_volume_usd',
    'flows.exchange.btc_net_flow',
    'flows.whale.transaction_count',
    'flows.whale.net_direction'
);

-- =============================================================================
-- METADATA (CoinPaprika) — reference data, not time series for training
-- Use 365 days as target (metadata changes slowly).
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 365
WHERE metric_id IN (
    'metadata.instrument.category',
    'metadata.instrument.sector',
    'metadata.instrument.listing_date',
    'metadata.futures.expiry_schedule'
);

-- =============================================================================
-- SEC EDGAR ETF — quarterly, ~2yr history available
-- =============================================================================
UPDATE forge.metric_catalog SET backfill_depth_days = 730
WHERE metric_id IN (
    'macro.etf.aum_usd',
    'macro.etf.shares_outstanding'
);

-- =============================================================================
-- Verification: no active metrics should have NULL backfill_depth_days
-- =============================================================================
DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count
    FROM forge.metric_catalog
    WHERE status = 'active' AND backfill_depth_days IS NULL;

    IF null_count > 0 THEN
        RAISE EXCEPTION 'GATE FAIL: % active metrics still have NULL backfill_depth_days', null_count;
    END IF;
END $$;

COMMIT;
