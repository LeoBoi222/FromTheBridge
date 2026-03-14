"""Tests for Great Expectations integration — bronze_core suite.

Tests the GE-based validation that replaces per-row validate_observation()
in the sync bridge. Covers all 8 core expectations from v4.0 §2427-2436.
"""
import json
from datetime import UTC, datetime

import pytest

from ftb.validation.core import Observation
from ftb.validation.expectations import (
    build_bronze_core_suite,
    validate_with_ge,
)
from ftb.writers.silver import DeadLetterRow


@pytest.fixture
def metric_catalog():
    """Catalog with range-bounded and nullable metrics."""
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
        "price.spot.close_usd": {
            "is_nullable": False,
            "expected_range_low": None,
            "expected_range_high": None,
        },
        "defi.tvl.total_usd": {
            "is_nullable": True,
            "expected_range_low": 0.0,
            "expected_range_high": None,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


def _obs(metric_id, instrument_id, value, observed_at=None):
    """Helper to build test observations."""
    return Observation(
        metric_id=metric_id,
        instrument_id=instrument_id,
        source_id="eds_derived",
        observed_at=observed_at or datetime(2024, 6, 15, 12, 0, tzinfo=UTC),
        value=value,
    )


# --- Suite builder tests ---


class TestBuildBronzeCoreSuite:
    def test_returns_expectation_suite(self, metric_catalog, instrument_set):
        suite = build_bronze_core_suite(metric_catalog, instrument_set)
        assert suite is not None
        assert suite.name == "bronze_core"

    def test_has_core_expectations(self, metric_catalog, instrument_set):
        suite = build_bronze_core_suite(metric_catalog, instrument_set)
        # Should have at least the 8 core expectations
        # (some conditional ones generate multiple expectations)
        assert len(suite.expectations) >= 8

    def test_empty_catalog_still_builds(self, instrument_set):
        suite = build_bronze_core_suite({}, instrument_set)
        # Core nullability + uniqueness expectations still present
        assert len(suite.expectations) >= 4


# --- Full validation pipeline tests ---


class TestValidateWithGe:
    def test_all_valid_returns_empty_dead_letter(self, metric_catalog, instrument_set):
        obs = [
            _obs("macro.rates.fed_funds_effective", None, 5.33),
            _obs("price.spot.close_usd", "BTC-USD", 65000.0),
        ]
        valid, dead, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 2
        assert len(dead) == 0
        assert checkpoint["passed"] is True
        assert checkpoint["rows_rejected"] == 0

    def test_unknown_metric_rejected(self, metric_catalog, instrument_set):
        obs = [_obs("unknown.metric", None, 1.0)]
        valid, dead, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "UNKNOWN_METRIC"

    def test_unknown_instrument_rejected(self, metric_catalog, instrument_set):
        obs = [_obs("price.spot.close_usd", "DOGE-USD", 0.15)]
        valid, dead, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "UNKNOWN_INSTRUMENT"

    def test_null_instrument_allowed_for_market_level(self, metric_catalog, instrument_set):
        """Market-level metrics (instrument_id=None) should pass instrument check."""
        obs = [_obs("macro.rates.fed_funds_effective", None, 5.33)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_range_violation_low(self, metric_catalog, instrument_set):
        obs = [_obs("macro.rates.fed_funds_effective", None, -1.0)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "RANGE_VIOLATION"

    def test_range_violation_high(self, metric_catalog, instrument_set):
        obs = [_obs("chain.valuation.mvrv_ratio", None, 150.0)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "RANGE_VIOLATION"

    def test_no_range_check_when_bounds_undefined(self, metric_catalog, instrument_set):
        """price.spot.close_usd has no range bounds — any positive value OK."""
        obs = [_obs("price.spot.close_usd", "BTC-USD", 999999.0)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_null_value_rejected_for_non_nullable(self, metric_catalog, instrument_set):
        obs = [_obs("macro.rates.fed_funds_effective", None, None)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "NULL_VIOLATION"

    def test_null_value_allowed_for_nullable(self, metric_catalog, instrument_set):
        obs = [_obs("defi.tvl.total_usd", None, None)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_null_observed_at_rejected(self, metric_catalog, instrument_set):
        obs = [Observation("macro.rates.fed_funds_effective", None, "eds_derived", None, 5.33)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "NULL_TIMESTAMP"

    def test_null_metric_id_rejected(self, metric_catalog, instrument_set):
        obs = [Observation(None, None, "eds_derived",
                          datetime(2024, 6, 15, tzinfo=UTC), 5.0)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "NULL_METRIC"

    def test_duplicate_observations_rejected(self, metric_catalog, instrument_set):
        ts = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
        obs = [
            _obs("macro.rates.fed_funds_effective", None, 5.33, ts),
            _obs("macro.rates.fed_funds_effective", None, 5.34, ts),  # same key, diff value
        ]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        # One should pass, the duplicate should be rejected
        assert len(valid) == 1
        assert len(dead) == 1
        assert dead[0].rejection_code == "DUPLICATE_OBSERVATION"

    def test_mixed_valid_and_invalid(self, metric_catalog, instrument_set):
        obs = [
            _obs("macro.rates.fed_funds_effective", None, 5.33),  # valid
            _obs("unknown.metric", None, 1.0),  # unknown metric
            _obs("price.spot.close_usd", "BTC-USD", 65000.0),  # valid
            _obs("chain.valuation.mvrv_ratio", None, -5.0),  # range violation
        ]
        valid, dead, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 2
        assert len(dead) == 2
        assert checkpoint["rows_validated"] == 4
        assert checkpoint["rows_rejected"] == 2

    def test_dead_letter_has_raw_payload(self, metric_catalog, instrument_set):
        obs = [_obs("unknown.metric", None, 42.0)]
        _, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        payload = json.loads(dead[0].raw_payload)
        assert payload["metric_id"] == "unknown.metric"
        assert payload["value"] == 42.0

    def test_dead_letter_row_type(self, metric_catalog, instrument_set):
        obs = [_obs("unknown.metric", None, 1.0)]
        _, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert isinstance(dead[0], DeadLetterRow)


# --- Checkpoint summary tests ---


class TestCheckpointSummary:
    def test_checkpoint_structure(self, metric_catalog, instrument_set):
        obs = [_obs("macro.rates.fed_funds_effective", None, 5.33)]
        _, _, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert checkpoint["suite"] == "bronze_core"
        assert isinstance(checkpoint["passed"], bool)
        assert "expectations_total" in checkpoint
        assert "expectations_failed" in checkpoint
        assert "rows_validated" in checkpoint
        assert "rows_rejected" in checkpoint
        assert "rejection_breakdown" in checkpoint

    def test_checkpoint_counts_accurate(self, metric_catalog, instrument_set):
        obs = [
            _obs("macro.rates.fed_funds_effective", None, 5.33),
            _obs("unknown.metric", None, 1.0),
        ]
        _, _, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert checkpoint["rows_validated"] == 2
        assert checkpoint["rows_rejected"] == 1
        assert checkpoint["rejection_breakdown"]["UNKNOWN_METRIC"] == 1

    def test_checkpoint_passed_true_when_all_valid(self, metric_catalog, instrument_set):
        obs = [_obs("macro.rates.fed_funds_effective", None, 5.33)]
        _, _, checkpoint = validate_with_ge(obs, metric_catalog, instrument_set)
        assert checkpoint["passed"] is True
        assert checkpoint["expectations_failed"] == 0

    def test_empty_batch_returns_trivial_checkpoint(self, metric_catalog, instrument_set):
        valid, dead, checkpoint = validate_with_ge([], metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 0
        assert checkpoint["rows_validated"] == 0
        assert checkpoint["passed"] is True


# --- Edge cases ---


class TestEdgeCases:
    def test_single_row_multiple_failures_gets_first_rejection(self, metric_catalog, instrument_set):
        """A row with null metric_id AND null observed_at gets one dead letter entry."""
        obs = [Observation(None, None, "eds_derived", None, 5.0)]
        _, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(dead) == 1  # one entry, not two

    def test_range_check_only_for_non_null_values(self, metric_catalog, instrument_set):
        """Nullable metric with null value should not trigger range check."""
        obs = [_obs("defi.tvl.total_usd", None, None)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_defi_lending_nullability_gate(self):
        """Gate criterion: null borrow_apy valid, null utilization_rate dead-lettered.

        Per v4.0 §2667: borrow_apy is nullable (supply-only pools lack borrow side),
        utilization_rate is non-nullable (NULL_VIOLATION).
        """
        catalog = {
            "defi.lending.borrow_apy": {
                "is_nullable": True,
                "expected_range_low": None,
                "expected_range_high": None,
            },
            "defi.lending.utilization_rate": {
                "is_nullable": False,
                "expected_range_low": None,
                "expected_range_high": None,
            },
        }
        instruments = {"__market__"}

        # Null borrow_apy should pass (nullable)
        obs_valid = [_obs("defi.lending.borrow_apy", None, None)]
        valid, dead, _ = validate_with_ge(obs_valid, catalog, instruments)
        assert len(valid) == 1
        assert len(dead) == 0

        # Null utilization_rate should be dead-lettered (non-nullable)
        obs_invalid = [_obs("defi.lending.utilization_rate", None, None)]
        valid, dead, _ = validate_with_ge(obs_invalid, catalog, instruments)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "NULL_VIOLATION"

    def test_partial_range_bounds_low_only(self, metric_catalog, instrument_set):
        """defi.tvl.total_usd has range_low=0 but no range_high."""
        obs = [_obs("defi.tvl.total_usd", None, -100.0)]
        valid, dead, _ = validate_with_ge(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "RANGE_VIOLATION"
