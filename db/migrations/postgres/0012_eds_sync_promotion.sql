-- 0012_eds_sync_promotion.sql
-- Promote all EDS-delivered metrics to eds_derived source.
--
-- Context: EDS metric_id rename complete (C3 violation cleared 2026-03-13).
-- empire.observations now uses FTB canonical metric_ids.
-- empire_to_forge_sync picks up metrics WHERE 'eds_derived' = ANY(sources).
--
-- This migration:
--   35 UPDATE: replace sources with '{eds_derived}' for all metrics EDS delivers
--              (20 FRED + 7 DeFiLlama + 2 Coinalyze + 2 Tiingo + 2 BLC-01 + 2 SEC EDGAR)
--    4 INSERT: 3 COT metrics + defi.protocol.fees_usd_24h (new to forge catalog)
--    2 SKIP:   liquidation_count, liquidation_ls_ratio (already {eds_derived})
--   41 TOTAL:  eds_derived metrics after migration
--
-- Pending EDS delivery (NOT in this migration):
--   - macro.cot.institutional_long_pct — derivation not yet deployed
--   - defi.protocol.revenue_usd_24h — not yet in empire.observations
--   - macro.commodity.gold — not yet in empire.observations
--   - macro.liquidity.boj_balance_sheet — not yet in empire.observations
--   - macro.rates.move_index — not yet in empire.observations
--
-- Decision: eds_derived REPLACES old source entries, does not append.
-- Old FTB adapters for these metrics should be decommissioned per v4.0
-- §Adapter Decommission Protocol.

BEGIN;

-- ============================================================
-- 1. UPDATE: FRED metrics (20 metrics, 19 changing + 1 already has eds_derived)
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'macro.commodity.wti_crude',
    'macro.credit.hy_oas',
    'macro.equity.sp500',
    'macro.fx.dxy',
    'macro.growth.real_gdp',
    'macro.inflation.core_pce',
    'macro.inflation.cpi',
    'macro.labor.jobless_claims',
    'macro.labor.nonfarm_payrolls',
    'macro.liquidity.ecb_balance_sheet',
    'macro.liquidity.fed_balance_sheet',
    'macro.liquidity.m2',
    'macro.liquidity.monetary_base',
    'macro.rates.fed_funds_effective',
    'macro.rates.yield_10y',
    'macro.rates.yield_2y',
    'macro.rates.yield_30y',
    'macro.rates.yield_spread_10y2y',
    'macro.rates.yield_spread_10y3m',
    'macro.volatility.vix'
);

-- ============================================================
-- 2. UPDATE: DeFiLlama metrics (7 metrics)
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'defi.aggregate.tvl_usd',
    'defi.dex.volume_by_chain_usd',
    'defi.dex.volume_usd_24h',
    'defi.protocol.tvl_usd',
    'stablecoin.peg.price_usd',
    'stablecoin.supply.per_asset_usd',
    'stablecoin.supply.total_usd'
);

-- ============================================================
-- 3. UPDATE: Coinalyze metrics (2 metrics)
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'derivatives.perpetual.funding_rate',
    'derivatives.perpetual.open_interest_usd'
);

-- ============================================================
-- 4. UPDATE: Tiingo/price metrics (2 metrics, replacing {tiingo,eds_derived})
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'price.spot.close_usd',
    'price.spot.volume_usd_24h'
);

-- ============================================================
-- 5. UPDATE: BLC-01 liquidation metrics (2 metrics, replacing {coinalyze,eds_derived})
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'derivatives.perpetual.liquidations_long_usd',
    'derivatives.perpetual.liquidations_short_usd'
);

-- ============================================================
-- 6. UPDATE: SEC EDGAR metrics (2 metrics)
-- ============================================================
UPDATE forge.metric_catalog
SET sources = '{eds_derived}'
WHERE metric_id IN (
    'macro.etf.aum_usd',
    'macro.etf.shares_outstanding'
);

-- ============================================================
-- 7. INSERT: CFTC COT metrics (3 metrics — in empire.observations, not yet in forge)
--    Cadence: weekly (Fridays 15:30 ET release). observed_at = Tuesday as-of date.
--    Granularity: per_instrument (BTC-USD, ETH-USD via CME futures).
--    See v4.0 §CFTC COT (E4) for field mappings.
-- ============================================================
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type,
    granularity, cadence, staleness_threshold, is_nullable, computation, sources
) VALUES
(
    'macro.cot.institutional_net_position',
    'macro', 'cot',
    'Institutional Net Position (TFF Non-Commercial)',
    'contracts', 'numeric',
    'per_instrument', '7 days', '14 days',
    false, 'Non-commercial long minus short', '{eds_derived}'
),
(
    'macro.cot.open_interest_contracts',
    'macro', 'cot',
    'COT Total Open Interest (Contracts)',
    'contracts', 'numeric',
    'per_instrument', '7 days', '14 days',
    false, NULL, '{eds_derived}'
),
(
    'macro.cot.dealer_net_position',
    'macro', 'cot',
    'Dealer Net Position (TFF Dealer/Intermediary)',
    'contracts', 'numeric',
    'per_instrument', '7 days', '14 days',
    false, 'Dealer long minus short', '{eds_derived}'
);

-- ============================================================
-- 8. INSERT: DeFiLlama fees metric (in empire.observations, not yet in forge)
--    Follows existing defi.protocol.* pattern (per_protocol, 12h cadence).
-- ============================================================
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type,
    granularity, cadence, staleness_threshold, is_nullable, computation, sources
) VALUES
(
    'defi.protocol.fees_usd_24h',
    'defi', 'protocol',
    'Protocol Fees 24h (USD)',
    'usd', 'numeric',
    'per_protocol', '12 hours', '1 day',
    false, NULL, '{eds_derived}'
);

-- ============================================================
-- Verification: count eds_derived metrics after migration
-- Pre-migration: 7 (2 sole eds_derived + 5 multi-source)
-- Post-migration: 35 updated + 2 unchanged + 4 inserted = 41
-- ============================================================
DO $$
DECLARE
    cnt INTEGER;
BEGIN
    SELECT count(*) INTO cnt
    FROM forge.metric_catalog
    WHERE 'eds_derived' = ANY(sources) AND status = 'active';

    IF cnt <> 41 THEN
        RAISE EXCEPTION 'Expected 41 eds_derived metrics, got %', cnt;
    END IF;
END $$;

COMMIT;
