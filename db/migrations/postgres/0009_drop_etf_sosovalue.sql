-- Drop ETF flow metrics and SoSoValue source (2026-03-10)
-- Reason: SoSoValue non-commercial ToS, no commercial license path.
-- ETF flow features removed from pillar specs and ML model inputs.
-- Replaced by SEC EDGAR quarterly structural metrics.

BEGIN;

-- Remove any metric_lineage entries referencing dropped metrics/source (FK deps)
DELETE FROM forge.metric_lineage WHERE
    metric_id IN ('etf.flows.net_flow_usd', 'etf.flows.cumulative_flow_usd')
    OR source_id = 'sosovalue';

-- Remove ETF flow metrics from catalog
DELETE FROM forge.metric_catalog WHERE metric_id IN (
    'etf.flows.net_flow_usd',
    'etf.flows.cumulative_flow_usd'
);

-- Remove SoSoValue from source catalog
DELETE FROM forge.source_catalog WHERE source_id = 'sosovalue';

-- Add SEC EDGAR source
INSERT INTO forge.source_catalog (
    source_id, display_name, tier, tos_risk, commercial_use,
    redistribution_status, cost_tier, redistribution_notes,
    propagate_restriction, attribution_required, is_active
) VALUES (
    'sec_edgar', 'SEC EDGAR', 2, 'none', true,
    'allowed', 'free', 'Quarterly ETF structural data — public domain, no restrictions',
    false, false, true
);

-- Add EDGAR structural metrics
INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type,
    granularity, cadence, staleness_threshold,
    sources, status
) VALUES
    ('macro.etf.aum_usd', 'macro', 'etf', 'ETF AUM (USD)', 'usd', 'numeric',
     'market_level', '2160 hours', '180 days', '{sec_edgar}', 'active'),
    ('macro.etf.shares_outstanding', 'macro', 'etf', 'ETF Shares Outstanding', 'count', 'numeric',
     'market_level', '2160 hours', '180 days', '{sec_edgar}', 'active');

COMMIT;
