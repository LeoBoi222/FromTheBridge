-- 0013_stablecoin_instruments.sql
-- Add top-3 stablecoin instruments for per-asset supply and peg tracking.
-- v4.0: "USDC, USDT are stablecoin supply/peg feeds at __market__ level. Not instruments."
-- But per-asset peg deviation (max_deviation feature) needs individual rows.
-- Scope locked to USDT, USDC, DAI. EDS filters emission to match.

BEGIN;

INSERT INTO forge.instruments (instrument_id, asset_class, name, is_active, collection_tier)
VALUES
    ('USDT-USD', 'crypto', 'Tether / USD', true, 'collection'),
    ('USDC-USD', 'crypto', 'USD Coin / USD', true, 'collection'),
    ('DAI-USD',  'crypto', 'Dai / USD',      true, 'collection')
ON CONFLICT (instrument_id) DO NOTHING;

-- Verify: exactly 7 instruments after insert
DO $$
DECLARE
    cnt integer;
BEGIN
    SELECT count(*) INTO cnt FROM forge.instruments;
    IF cnt != 7 THEN
        RAISE EXCEPTION '0013: expected 7 instruments, got %', cnt;
    END IF;
END $$;

COMMIT;
