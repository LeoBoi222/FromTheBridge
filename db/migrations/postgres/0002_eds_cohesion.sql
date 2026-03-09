-- ---------------------------------------------------------------------------
-- 0002_eds_cohesion.sql — EDS↔FTB Cohesion Audit Corrective
-- ---------------------------------------------------------------------------
-- Resolves: audit G5 (eds_derived source), Decision 1 (valuation domain)
-- Audit report: docs/design/eds_ftb_cohesion_audit.md
-- Date: 2026-03-08
-- ---------------------------------------------------------------------------

-- G5: Add eds_derived source to source_catalog
-- Required for empire_to_forge_sync to write observations with a recognized source_id.
-- EDS-derived metrics flow through this source after manual promotion to metric_catalog.
INSERT INTO forge.source_catalog (source_id, display_name, api_type, tos_audited, redistribution_allowed, cadence_hours, rate_limit, auth_required, notes)
VALUES
    ('eds_derived', 'Empire Data Services', 'derived', false, 'yes', 6, NULL, false,
     'Node-derived on-chain metrics synced via empire_to_forge_sync. Cadence = sync interval (6h).')
ON CONFLICT (source_id) DO NOTHING;

-- Decision 1: valuation domain acknowledgment
-- The metric_catalog table (0001) has no domain CHECK constraint in the deployed schema.
-- The design doc reference DDL has been updated to include: chain, valuation, price, metadata
-- in addition to the original 7 domains.
-- Existing seed data already uses domain='chain' for MVRV/SOPR/NUPL/Puell/transfer_volume/NVT.
-- No DDL change needed here — this comment documents the decision.

-- Verification queries:
-- SELECT source_id, display_name FROM forge.source_catalog WHERE source_id = 'eds_derived';
-- SELECT DISTINCT domain FROM forge.metric_catalog ORDER BY domain;
--   Expected: chain, defi, derivatives, etf, flows, macro, metadata, price, stablecoin
--   (valuation will appear when EDS metrics are promoted with domain='valuation')
