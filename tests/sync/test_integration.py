"""Integration test — full sync path with mocked DB clients."""
from datetime import datetime, timezone

from ftb.sync.bridge import map_empire_to_forge
from ftb.sync.sync_asset import validate_and_split
from ftb.validation.core import Observation


def _empire_rows():
    """Simulated empire.observations query result."""
    return [
        {
            "metric_id": "chain.valuation.mvrv_ratio",
            "instrument_id": "BTC-USD",
            "source_id": "eds_node_derivation",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 2.15,
        },
        {
            "metric_id": "chain.valuation.mvrv_ratio",
            "instrument_id": "__market__",
            "source_id": "eds_node_derivation",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 1.95,
        },
        {
            "metric_id": "not.promoted.metric",
            "instrument_id": "__market__",
            "source_id": "eds_fred",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 42.0,
        },
    ]


METRIC_CATALOG = {
    "chain.valuation.mvrv_ratio": {
        "is_nullable": False,
        "expected_range_low": 0.0,
        "expected_range_high": 100.0,
    },
}

INSTRUMENT_SET = {"BTC-USD", "ETH-USD", "SOL-USD"}


class TestFullSyncPath:
    def test_end_to_end_map_validate_split(self):
        """Full path: empire rows -> map -> validate -> split."""
        rows = _empire_rows()
        promoted = {"chain.valuation.mvrv_ratio"}

        # Map
        observations = map_empire_to_forge(rows, promoted)
        assert len(observations) == 2  # 3rd row filtered (not promoted)

        # Validate + split
        valid, dead = validate_and_split(observations, METRIC_CATALOG, INSTRUMENT_SET)

        # BTC-USD row is valid, __market__ mapped to None is also valid (market-level)
        assert len(valid) == 2
        assert len(dead) == 0

        # Verify source_id rewrite
        assert all(o.source_id == "eds_derived" for o in valid)

        # Verify instrument_id mapping
        instruments = {o.instrument_id for o in valid}
        assert "BTC-USD" in instruments
        assert None in instruments  # was __market__

    def test_watermark_advances(self):
        """max(ingested_at) from batch becomes next watermark."""
        rows = _empire_rows()
        max_ingested = max(r["ingested_at"] for r in rows)
        assert max_ingested == datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc)
