-- 0015: Add macro.cot.institutional_long_pct to catalog
-- Now in empire.observations (672 obs, BTC-USD from 2018, ETH-USD from 2021)
-- Completes CFTC COT: 4 of 4 metrics now in forge catalog.

INSERT INTO forge.metric_catalog (
    metric_id, domain, subdomain, description, unit, value_type,
    granularity, cadence, staleness_threshold, sources, signal_pillar, status
) VALUES (
    'macro.cot.institutional_long_pct', 'macro', 'cot',
    'Institutional (asset manager + leveraged funds) long positions as percentage of total open interest, from CFTC COT report',
    'percent', 'numeric', 'per_instrument',
    '7 days'::interval, '14 days'::interval,
    ARRAY['eds_derived'], NULL, 'active'
);
