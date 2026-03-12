-- 0010_blc01_metric_promotion.sql
-- Promote BLC-01 liquidation metrics for empire_to_forge_sync.
-- EDS now delivers 4 liquidation metrics from binance_blc01 to empire.observations.
-- FTB receives them via the sync bridge (source_id='eds_derived').
--
-- 2 existing metrics: add eds_derived to sources array
-- 2 new metrics: liquidation_count and liquidation_ls_ratio (BLC-01-only)

BEGIN;

-- 1. Update existing shared metrics (also sourced by Coinalyze)
UPDATE forge.metric_catalog
SET sources = array_append(sources, 'eds_derived')
WHERE metric_id IN (
    'derivatives.perpetual.liquidations_long_usd',
    'derivatives.perpetual.liquidations_short_usd'
)
  AND NOT ('eds_derived' = ANY(sources));

-- 2. Insert BLC-01-only metrics
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type,
    granularity, cadence, staleness_threshold, is_nullable, computation, sources
) VALUES
(
    'derivatives.perpetual.liquidation_count',
    'derivatives', 'perpetual',
    'Liquidation Event Count (1h bucket)',
    'count', 'numeric',
    'per_instrument', '01:00:00', '02:00:00',
    false, 'COUNT(*) per 1h bucket', '{eds_derived}'
),
(
    'derivatives.perpetual.liquidation_ls_ratio',
    'derivatives', 'perpetual',
    'Liquidation Long/Short Ratio',
    'ratio', 'numeric',
    'per_instrument', '01:00:00', '02:00:00',
    true, 'long_usd / short_usd; NULL when short_usd = 0', '{eds_derived}'
);

COMMIT;
