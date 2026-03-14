-- 0014_defi_metrics_eds_only.sql
-- Remove eds_derived from per-protocol/per-chain DeFi metrics.
-- These use protocol slugs and chain names as instrument_ids,
-- which don't map to forge.instruments. FTB uses the aggregates
-- (defi.aggregate.tvl_usd, defi.dex.volume_usd_24h) instead.

BEGIN;

UPDATE forge.metric_catalog
SET sources = array_remove(sources, 'eds_derived')
WHERE metric_id IN (
    'defi.protocol.tvl_usd',
    'defi.protocol.fees_usd_24h',
    'defi.dex.volume_by_chain_usd'
);

-- Verify: these 3 should no longer have eds_derived
DO $$
DECLARE
    cnt integer;
BEGIN
    SELECT count(*) INTO cnt
    FROM forge.metric_catalog
    WHERE metric_id IN ('defi.protocol.tvl_usd', 'defi.protocol.fees_usd_24h', 'defi.dex.volume_by_chain_usd')
      AND 'eds_derived' = ANY(sources);
    IF cnt != 0 THEN
        RAISE EXCEPTION '0014: expected 0 eds_derived DeFi metrics, got %', cnt;
    END IF;
END $$;

-- Verify: total eds_derived count should be 38 (was 41, minus 3)
DO $$
DECLARE
    cnt integer;
BEGIN
    SELECT count(*) INTO cnt
    FROM forge.metric_catalog
    WHERE 'eds_derived' = ANY(sources);
    IF cnt != 38 THEN
        RAISE EXCEPTION '0014: expected 38 eds_derived metrics, got %', cnt;
    END IF;
END $$;

COMMIT;
