-- Add eds_derived source for empire_to_forge_sync bridge
INSERT INTO forge.source_catalog (
    source_id, display_name, tier, tos_risk,
    commercial_use, redistribution_status, propagate_restriction,
    attribution_required, cost_tier, reliability_slo,
    is_active, metadata
) VALUES (
    'eds_derived',
    'EDS Derived Metrics',
    0,
    'none',
    true,
    'allowed',
    false,
    false,
    'free',
    0.99,
    true,
    '{"description": "Metrics derived by EDS and synced via empire_to_forge_sync"}'::jsonb
);
