"""Tests for observation validation logic."""
from datetime import datetime, timezone

import pytest

from ftb.validation.core import validate_observation, Observation, ValidationResult


@pytest.fixture
def metric_catalog():
    """Minimal metric catalog for testing."""
    return {
        "price.spot.close_usd": {
            "is_nullable": False,
            "expected_range_low": None,
            "expected_range_high": None,
        },
        "price.spot.volume_usd_24h": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": None,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


def _obs(metric_id="price.spot.close_usd", instrument_id="BTC-USD",
         value=48000.0, observed_at=None):
    return Observation(
        metric_id=metric_id,
        instrument_id=instrument_id,
        source_id="tiingo",
        observed_at=observed_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        value=value,
    )


class TestValidateObservation:
    def test_valid_observation_passes(self, metric_catalog, instrument_set):
        result = validate_observation(_obs(), metric_catalog, instrument_set)
        assert result.is_valid
        assert result.rejection_code is None

    def test_unknown_metric_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(metric_id="fake.metric"), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "UNKNOWN_METRIC"

    def test_unknown_instrument_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(instrument_id="DOGE-USD"), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "UNKNOWN_INSTRUMENT"

    def test_null_value_rejected_when_not_nullable(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(value=None), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "NULL_VIOLATION"

    def test_below_range_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(metric_id="price.spot.volume_usd_24h", value=-100.0),
            metric_catalog, instrument_set,
        )
        assert not result.is_valid
        assert result.rejection_code == "RANGE_VIOLATION"

    def test_no_range_bounds_skips_check(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(value=999999999.0), metric_catalog, instrument_set
        )
        assert result.is_valid
