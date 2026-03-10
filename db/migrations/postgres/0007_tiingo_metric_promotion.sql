-- 0007_tiingo_metric_promotion.sql
-- Promote Tiingo price metrics to eds_derived source for empire_to_forge_sync.
-- EDS now collects Tiingo data (eds_track_2_tiingo in empire.observations).
-- FTB receives it via the sync bridge instead of direct API calls.

BEGIN;

-- Add eds_derived to sources array for the two Tiingo price metrics
UPDATE forge.metric_catalog
SET sources = array_append(sources, 'eds_derived')
WHERE metric_id IN ('price.spot.close_usd', 'price.spot.volume_usd_24h')
  AND NOT ('eds_derived' = ANY(sources));

COMMIT;
