"""Tests for empire_to_forge_sync business logic."""
from datetime import datetime, timezone

import pytest

from ftb.sync.bridge import (
    map_empire_to_forge,
    build_empire_query,
    INSTRUMENT_ID_MARKET,
)
from ftb.validation.core import Observation


class TestMapEmpireToForge:
    def test_market_level_metric_maps_instrument_to_none(self):
        rows = [
            {
                "metric_id": "macro.rates.fed_funds_effective",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 5.33,
            }
        ]
        promoted = {"macro.rates.fed_funds_effective"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].instrument_id is None
        assert result[0].source_id == "eds_derived"
        assert result[0].metric_id == "macro.rates.fed_funds_effective"
        assert result[0].value == 5.33

    def test_instrument_scoped_metric_preserves_instrument_id(self):
        rows = [
            {
                "metric_id": "chain.valuation.mvrv_ratio",
                "instrument_id": "BTC-USD",
                "source_id": "eds_node_derivation",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 1.85,
            }
        ]
        promoted = {"chain.valuation.mvrv_ratio"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].instrument_id == "BTC-USD"

    def test_unpromoted_metrics_filtered_out(self):
        rows = [
            {
                "metric_id": "some.unknown.metric",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 42.0,
            },
            {
                "metric_id": "macro.rates.fed_funds_effective",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 5.33,
            },
        ]
        promoted = {"macro.rates.fed_funds_effective"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].metric_id == "macro.rates.fed_funds_effective"

    def test_empty_rows_returns_empty_list(self):
        result = map_empire_to_forge([], {"any"})
        assert result == []

    def test_all_observations_get_eds_derived_source(self):
        rows = [
            {
                "metric_id": "m1",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 1.0,
            },
            {
                "metric_id": "m2",
                "instrument_id": "BTC-USD",
                "source_id": "eds_node_derivation",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 2.0,
            },
        ]
        promoted = {"m1", "m2"}
        result = map_empire_to_forge(rows, promoted)
        assert all(o.source_id == "eds_derived" for o in result)


class TestBuildEmpireQuery:
    def test_first_run_no_watermark(self):
        sql, params = build_empire_query(
            metric_ids=["macro.rates.fed_funds_effective", "chain.valuation.mvrv_ratio"],
            watermark=None,
        )
        assert "metric_id IN" in sql
        assert "ingested_at >" not in sql
        assert len(params["metric_ids"]) == 2

    def test_incremental_with_watermark(self):
        wm = datetime(2024, 1, 15, tzinfo=timezone.utc)
        sql, params = build_empire_query(
            metric_ids=["macro.rates.fed_funds_effective"],
            watermark=wm,
        )
        assert "ingested_at >" in sql
        assert params["watermark"] == wm
