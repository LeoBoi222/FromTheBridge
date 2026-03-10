"""Tests for empire_to_forge_sync Dagster asset logic."""
import json
from datetime import datetime, timezone

import pytest

from ftb.sync.sync_asset import validate_and_split
from ftb.validation.core import Observation
from ftb.writers.silver import DeadLetterRow


@pytest.fixture
def metric_catalog():
    return {
        "macro.rates.fed_funds_effective": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": 25.0,
        },
        "chain.valuation.mvrv_ratio": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": 100.0,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


class TestValidateAndSplit:
    def test_all_valid(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 5.33),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_unknown_metric_goes_to_dead_letter(self, metric_catalog, instrument_set):
        obs = [
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "UNKNOWN_METRIC"

    def test_range_violation_goes_to_dead_letter(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), -5.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "RANGE_VIOLATION"

    def test_mixed_valid_and_invalid(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 5.33),
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 1

    def test_dead_letter_has_raw_payload(self, metric_catalog, instrument_set):
        obs = [
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        _, dead = validate_and_split(obs, metric_catalog, instrument_set)
        payload = json.loads(dead[0].raw_payload)
        assert payload["metric_id"] == "unknown.metric"
        assert payload["value"] == 1.0
