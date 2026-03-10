-- =============================================================================
-- instrument_source_map — cross-source symbol resolution
-- Target: empire_postgres (port 5433), database: crypto_structured
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS forge.instrument_source_map (
    instrument_id   TEXT NOT NULL REFERENCES forge.instruments(instrument_id),
    source_id       TEXT NOT NULL REFERENCES forge.source_catalog(source_id),
    source_symbol   TEXT NOT NULL,
    PRIMARY KEY (instrument_id, source_id)
);

CREATE INDEX idx_instrument_source_map_source
    ON forge.instrument_source_map (source_id);

-- Grant permissions (match existing pattern — DEFAULT PRIVILEGES cover this,
-- but explicit grants ensure idempotency if run before defaults apply)
GRANT SELECT, INSERT ON forge.instrument_source_map TO forge_writer;
GRANT SELECT ON forge.instrument_source_map TO forge_reader;

-- Seed Tiingo mappings
INSERT INTO forge.instrument_source_map (instrument_id, source_id, source_symbol) VALUES
    ('BTC-USD', 'tiingo', 'btcusd'),
    ('ETH-USD', 'tiingo', 'ethusd'),
    ('SOL-USD', 'tiingo', 'solusd')
ON CONFLICT DO NOTHING;

-- Seed metric_lineage rows for Tiingo (if not present)
INSERT INTO forge.metric_lineage (metric_id, source_id, compute_agent, is_primary) VALUES
    ('price.spot.close_usd',      'tiingo', 'collect_tiingo_price', true),
    ('price.spot.volume_usd_24h', 'tiingo', 'collect_tiingo_price', true),
    ('price.spot.ohlcv',          'tiingo', 'collect_tiingo_price', true)
ON CONFLICT DO NOTHING;

COMMIT;
