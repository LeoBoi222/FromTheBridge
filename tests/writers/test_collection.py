"""Tests for collection event writer."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from ftb.writers.collection import build_collection_event_params


class TestBuildCollectionEventParams:
    def test_builds_correct_params(self):
        params = build_collection_event_params(
            source_id="tiingo",
            metric_id=None,
            instrument_id=None,
            status="completed",
            observations_written=42,
            observations_rejected=3,
            metrics_covered=["price.spot.close_usd", "price.spot.volume_usd_24h"],
            instruments_covered=["BTC-USD", "ETH-USD", "SOL-USD"],
        )
        assert params["source_id"] == "tiingo"
        assert params["status"] == "completed"
        assert params["observations_written"] == 42
        assert params["observations_rejected"] == 3
        assert params["metrics_covered"] == ["price.spot.close_usd", "price.spot.volume_usd_24h"]
        assert params["instruments_covered"] == ["BTC-USD", "ETH-USD", "SOL-USD"]

    def test_failed_status(self):
        params = build_collection_event_params(
            source_id="tiingo",
            status="failed",
            error_detail="API returned 500",
        )
        assert params["status"] == "failed"
        assert params["error_detail"] == "API returned 500"
